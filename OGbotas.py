import telegram
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger
import pytz
from collections import defaultdict, deque
from datetime import datetime, timedelta, time
import random
import logging
import asyncio
import pickle
import os
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info(f"Running on Python {sys.version}")

# Get sensitive information from environment variables
TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID')
PASSWORD = os.getenv('PASSWORD', 'shoebot123')  # Default password or fetch from env if needed

# Check if required environment variables are set
if not TOKEN:
    logger.error("TELEGRAM_TOKEN environment variable is not set.")
    sys.exit(1)
if not ADMIN_CHAT_ID:
    logger.error("ADMIN_CHAT_ID environment variable is not set.")
    sys.exit(1)
if not GROUP_CHAT_ID:
    logger.error("GROUP_CHAT_ID environment variable is not set.")
    sys.exit(1)

# Constants
TIMEZONE = pytz.timezone('Europe/Vilnius')
COINFLIP_STICKER_ID = 'CAACAgIAAxkBAAEN32tnuPb-ovynJR5WNO1TQyv_ea17DwAC-RkAAtswEEqAzfrZRd8B1zYE'

# Data loading and saving functions
def load_data(filename, default):
    filepath = f"/opt/render/data/{filename}"
    try:
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            with open(filepath, 'rb') as f:
                return pickle.load(f)
        return default
    except (FileNotFoundError, EOFError, pickle.UnpicklingError):
        return default

def save_data(data, filename):
    filepath = f"/opt/render/data/{filename}"
    if isinstance(data, defaultdict):
        data = dict(data)
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            pickle.dump(data, f)
        logger.info(f"Saved data to {filepath}")
    except Exception as e:
        logger.error(f"Failed to save {filepath}: {str(e)}")

# Load initial data
featured_media_id = load_data('featured_media_id.pkl', None)
featured_media_type = load_data('featured_media_type.pkl', None)
barygos_media_id = load_data('barygos_media_id.pkl', None)
barygos_media_type = load_data('barygos_media_type.pkl', None)

PARDAVEJAI_MESSAGE_FILE = 'pardavejai_message.pkl'
DEFAULT_PARDAVEJAI_MESSAGE = "Pasirink pardavėją, už kurį nori balsuoti iš žemiau esančių mygtukų:"
pardavejai_message = load_data(PARDAVEJAI_MESSAGE_FILE, DEFAULT_PARDAVEJAI_MESSAGE)
last_addftbaryga_message = None
last_addftbaryga2_message = None

def save_pardavejai_message():
    save_data(pardavejai_message, PARDAVEJAI_MESSAGE_FILE)

# Scheduler setup
scheduler = AsyncIOScheduler(timezone=TIMEZONE)
scheduler.add_executor(ThreadPoolExecutor(max_workers=10), alias='default')

async def configure_scheduler(application):
    logger.info("Configuring scheduler...")
    application.job_queue.scheduler = scheduler
    scheduler.start()
    logger.info("Scheduler started successfully.")

# Bot initialization
application = Application.builder().token(TOKEN).post_init(configure_scheduler).build()
logger.info("Bot initialized")

# Data structures
trusted_sellers = ['@Seller1', '@Seller2', '@Seller3']
votes_weekly = load_data('votes_weekly.pkl', defaultdict(int))
votes_monthly = load_data('votes_monthly.pkl', defaultdict(list))
votes_alltime = load_data('votes_alltime.pkl', defaultdict(int))
voters = set()
downvoters = set()
pending_downvotes = {}
approved_downvotes = {}
vote_history = load_data('vote_history.pkl', defaultdict(list))
last_vote_attempt = defaultdict(lambda: datetime.min.replace(tzinfo=TIMEZONE))
last_downvote_attempt = defaultdict(lambda: datetime.min.replace(tzinfo=TIMEZONE))
complaint_id = 0
user_points = load_data('user_points.pkl', defaultdict(int))
coinflip_challenges = {}
daily_messages = defaultdict(lambda: defaultdict(int))
weekly_messages = defaultdict(int)
alltime_messages = load_data('alltime_messages.pkl', defaultdict(int))
chat_streaks = load_data('chat_streaks.pkl', defaultdict(int))
last_chat_day_raw = load_data('last_chat_day.pkl', {})
last_chat_day = defaultdict(lambda: datetime.min.replace(tzinfo=TIMEZONE), last_chat_day_raw)
allowed_groups = {GROUP_CHAT_ID}
valid_licenses = {'LICENSE-XYZ123', 'LICENSE-ABC456'}
pending_activation = {}
username_to_id = {}
polls = {}

def is_allowed_group(chat_id: str) -> bool:
    return str(chat_id) in allowed_groups

# Message deletion function
async def delete_message_job(context: telegram.ext.CallbackContext):
    job = context.job
    chat_id, message_id = job.context
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except telegram.error.BadRequest as e:
        if "Message to delete not found" in str(e):
            pass
        else:
            logger.error(f"Failed to delete message: {str(e)}")

# Command handlers
async def debug(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali naudoti šią komandą!")
        return
    chat_id = update.message.chat_id
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_list = "\n".join([f"@{m.user.username or m.user.id} (ID: {m.user.id})" for m in admins])
        msg = await update.message.reply_text(f"Matomi adminai:\n{admin_list}")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
    except telegram.error.TelegramError as e:
        msg = await update.message.reply_text(f"Debug failed: {str(e)}")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def whoami(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        username = f"@{member.user.username}" if member.user.username else "No username"
        msg = await update.message.reply_text(f"Jūs esate: {username} (ID: {user_id})")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
    except telegram.error.TelegramError as e:
        msg = await update.message.reply_text(f"Error: {str(e)}")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def startas(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    if chat_id != user_id:
        if is_allowed_group(chat_id):
            msg = await update.message.reply_text(
                "Sveiki! Use /balsuoju to vote for sellers with buttons. /nepatiko for downvotes (5 pts). "
                "Chat daily for 1-3 pts + streaks. Check /barygos, /chatking, /coinflip, or /apklausa!"
            )
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        else:
            msg = await update.message.reply_text("Šis botas skirtas tik mano grupėms! Siųsk /startas Password privačiai!")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
    else:
        try:
            password = context.args[0]
            if password == PASSWORD:
                pending_activation[user_id] = "password"
                await update.message.reply_text("Slaptažodis teisingas! Siųsk /activate_group GroupChatID.")
            else:
                await update.message.reply_text("Neteisingas slaptažodis!")
        except IndexError:
            await update.message.reply_text("Naudok: /startas Password privačiai!")

async def activate_group(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    if str(user_id) != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali aktyvuoti grupes!")
        return
    if user_id not in pending_activation:
        await update.message.reply_text("Pirma įvesk slaptažodį privačiai!")
        return
    try:
        group_id = context.args[0]
        if group_id in allowed_groups:
            await update.message.reply_text("Grupė jau aktyvuota!")
        else:
            allowed_groups.add(group_id)
            if pending_activation[user_id] != "password":
                valid_licenses.remove(pending_activation[user_id])
            del pending_activation[user_id]
            await update.message.reply_text(f"Grupė {group_id} aktyvuota! Use /startas in the group.")
    except IndexError:
        await update.message.reply_text("Naudok: /activate_group GroupChatID")

async def privatus(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        msg = await update.message.reply_text("Tik adminas gali naudoti šią komandą!")
        context.job_queue.run_once(delete_message_job, 45, context=(update.message.chat_id, msg.message_id))
        return
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    keyboard = [[InlineKeyboardButton("Valdyti privačiai", url=f"https://t.me/{context.bot.username}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = await update.message.reply_text("Spausk mygtuką, kad valdytum botą privačiai:", reply_markup=reply_markup)
    context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def start_private(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if chat_id == int(user_id) and user_id == ADMIN_CHAT_ID:
        keyboard = [
            [InlineKeyboardButton("Pridėti pardavėją", callback_data="admin_addseller")],
            [InlineKeyboardButton("Pašalinti pardavėją", callback_data="admin_removeseller")],
            [InlineKeyboardButton("Redaguoti /balsuoju tekstą", callback_data="admin_editpardavejai")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Sveikas, admin! Ką nori valdyti?", reply_markup=reply_markup)

async def handle_admin_button(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await query.answer("Tik adminas gali tai daryti!")
        return
    chat_id = query.message.chat_id
    if chat_id != int(user_id):
        await query.answer("Šią komandą naudok privačiai!")
        return

    data = query.data
    if data == "admin_addseller":
        await query.edit_message_text("Įvesk: /addseller @VendorTag")
    elif data == "admin_removeseller":
        await query.edit_message_text("Įvesk: /removeseller @VendorTag")
    elif data == "admin_editpardavejai":
        await query.edit_message_text("Įvesk: /editpardavejai 'Naujas tekstas'")
    await query.answer()

async def balsuoju(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if not is_allowed_group(chat_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return

    keyboard = [[InlineKeyboardButton(seller, callback_data=f"vote_{seller}")] for seller in trusted_sellers]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if featured_media_type == 'photo':
            msg = await context.bot.send_photo(chat_id=user_id, photo=featured_media_id, caption=pardavejai_message, reply_markup=reply_markup)
        elif featured_media_type == 'animation':
            msg = await context.bot.send_animation(chat_id=user_id, animation=featured_media_id, caption=pardavejai_message, reply_markup=reply_markup)
        elif featured_media_type == 'video':
            msg = await context.bot.send_video(chat_id=user_id, video=featured_media_id, caption=pardavejai_message, reply_markup=reply_markup)
        else:
            msg = await context.bot.send_message(chat_id=user_id, text=pardavejai_message, reply_markup=reply_markup)
        # Store message ID in context for later deletion
        context.user_data['balsuoju_message_id'] = (msg.chat_id, msg.message_id)
        logger.info(f"/balsuoju called by user_id={user_id}, buttons sent privately, message_id={msg.message_id}")
    except telegram.error.Unauthorized:
        msg = await context.bot.send_message(chat_id=chat_id, text="Please start a conversation with me privately by sending /start to vote.")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def addftbaryga(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        msg = await update.message.reply_text("Tik adminas gali pridėti media!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    if not update.message.reply_to_message:
        msg = await update.message.reply_text("Atsakyk į žinutę su paveikslėliu, GIF ar video!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    
    global featured_media_id, featured_media_type, last_addftbaryga_message
    reply = update.message.reply_to_message
    if reply.photo:
        media = reply.photo[-1]
        featured_media_id = media.file_id
        featured_media_type = 'photo'
        last_addftbaryga_message = "Paveikslėlis pridėtas prie /balsuoju!"
    elif reply.animation:
        media = reply.animation
        featured_media_id = media.file_id
        featured_media_type = 'animation'
        last_addftbaryga_message = "GIF pridėtas prie /balsuoju!"
    elif reply.video:
        media = reply.video
        featured_media_id = media.file_id
        featured_media_type = 'video'
        last_addftbaryga_message = "Video pridėtas prie /balsuoju!"
    else:
        msg = await update.message.reply_text("Atsakyk į žinutę su paveikslėliu, GIF ar video!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    
    save_data(featured_media_id, 'featured_media_id.pkl')
    save_data(featured_media_type, 'featured_media_type.pkl')
    msg = await update.message.reply_text(last_addftbaryga_message)
    context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def addftbaryga2(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        msg = await update.message.reply_text("Tik adminas gali pridėti media!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    if not update.message.reply_to_message:
        msg = await update.message.reply_text("Atsakyk į žinutę su paveikslėliu, GIF ar video!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    
    global barygos_media_id, barygos_media_type, last_addftbaryga2_message
    reply = update.message.reply_to_message
    if reply.photo:
        media = reply.photo[-1]
        barygos_media_id = media.file_id
        barygos_media_type = 'photo'
        last_addftbaryga2_message = "Paveikslėlis pridėtas prie /barygos!"
    elif reply.animation:
        media = reply.animation
        barygos_media_id = media.file_id
        barygos_media_type = 'animation'
        last_addftbaryga2_message = "GIF pridėtas prie /barygos!"
    elif reply.video:
        media = reply.video
        barygos_media_id = media.file_id
        barygos_media_type = 'video'
        last_addftbaryga2_message = "Video pridėtas prie /barygos!"
    else:
        msg = await update.message.reply_text("Atsakyk į žinutę su paveikslėliu, GIF ar video!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    
    save_data(barygos_media_id, 'barygos_media_id.pkl')
    save_data(barygos_media_type, 'barygos_media_type.pkl')
    msg = await update.message.reply_text(last_addftbaryga2_message)
    context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def editpardavejai(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        msg = await update.message.reply_text("Tik adminas gali redaguoti šį tekstą!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return

    try:
        new_message = " ".join(context.args)
        if not new_message:
            msg = await update.message.reply_text("Naudok: /editpardavejai 'Naujas tekstas'")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return
        global pardavejai_message
        pardavejai_message = new_message
        save_pardavejai_message()
        msg = await update.message.reply_text(f"Pardavėjų žinutė atnaujinta: '{pardavejai_message}'")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
    except IndexError:
        msg = await update.message.reply_text("Naudok: /editpardavejai 'Naujas tekstas'")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def handle_vote_button(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        logger.error("No callback query received")
        return
    
    user_id = query.from_user.id
    if query.message is None:
        await query.answer("Klaida: Balsavimo žinutė nerasta. Prašau bandyti dar kartą.")
        logger.error(f"Message is None for user_id={user_id}, callback_data={query.data}")
        return
    
    chat_id = query.message.chat_id  # This will be the private chat_id
    data = query.data

    logger.info(f"Vote attempt by user_id={user_id} in chat_id={chat_id}, callback_data={data}")

    if not data.startswith("vote_"):
        logger.warning(f"Invalid callback data: {data} from user_id={user_id}")
        return

    seller = data.replace("vote_", "")
    if seller not in trusted_sellers:
        await query.answer("Šis pardavėjas nebegalioja!")
        logger.warning(f"Attempt to vote for invalid seller '{seller}' by user_id={user_id}. Trusted sellers: {trusted_sellers}")
        # Delete the message even if the seller is invalid
        if 'balsuoju_message_id' in context.user_data:
            chat_id, message_id = context.user_data['balsuoju_message_id']
            context.job_queue.run_once(delete_message_job, 5, context=(chat_id, message_id))
            del context.user_data['balsuoju_message_id']
        return

    now = datetime.now(TIMEZONE)
    last_vote = last_vote_attempt.get(user_id, datetime.min.replace(tzinfo=TIMEZONE))
    cooldown_remaining = timedelta(days=7) - (now - last_vote)
    if cooldown_remaining > timedelta(0):
        days_left = max(1, int(cooldown_remaining.total_seconds() // 86400))
        await query.answer(f"Tu jau balsavai! Liko {days_left} dienų iki kito balsavimo.")
        await context.bot.send_message(chat_id=chat_id, text=f"Tu jau balsavai! Liko {days_left} dienų iki kito balsavimo.")
        # Delete the balsuoju message after showing cooldown
        if 'balsuoju_message_id' in context.user_data:
            chat_id, message_id = context.user_data['balsuoju_message_id']
            context.job_queue.run_once(delete_message_job, 5, context=(chat_id, message_id))
            del context.user_data['balsuoju_message_id']
        logger.info(f"User_id={user_id} blocked by cooldown, {days_left} days left.")
        return

    user_points.setdefault(user_id, 0)
    votes_weekly.setdefault(seller, 0)
    votes_alltime.setdefault(seller, 0)
    votes_monthly.setdefault(seller, [])

    logger.info(f"Before vote: user_id={user_id}, points={user_points[user_id]}, votes_weekly[{seller}]={votes_weekly[seller]}, votes_alltime[{seller}]={votes_alltime[seller]}")

    votes_weekly[seller] += 1
    votes_monthly[seller].append((now, 1))
    votes_alltime[seller] += 1
    voters.add(user_id)
    vote_history[seller].append((user_id, "up", "Button vote", now))
    user_points[user_id] += 5
    last_vote_attempt[user_id] = now

    logger.info(f"After vote: user_id={user_id}, points={user_points[user_id]}, votes_weekly[{seller}]={votes_weekly[seller]}, votes_alltime[{seller}]={votes_alltime[seller]}")

    await query.answer("Ačiū už jūsų balsą, 5 taškai buvo pridėti prie jūsų sąskaitos.")
    await query.edit_message_text(f"Ačiū už jūsų balsą už {seller}, 5 taškai pridėti!")
    
    # Delete the balsuoju message after successful vote
    if 'balsuoju_message_id' in context.user_data:
        chat_id, message_id = context.user_data['balsuoju_message_id']
        context.job_queue.run_once(delete_message_job, 5, context=(chat_id, message_id))
        del context.user_data['balsuoju_message_id']
    
    save_data(votes_weekly, 'votes_weekly.pkl')
    save_data(votes_monthly, 'votes_monthly.pkl')
    save_data(votes_alltime, 'votes_alltime.pkl')
    save_data(vote_history, 'vote_history.pkl')
    save_data(user_points, 'user_points.pkl')

async def apklausa(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if not is_allowed_group(chat_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return

    try:
        question = " ".join(context.args)
        if not question:
            msg = await update.message.reply_text("Naudok: /apklausa 'Klausimas'")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return

        poll_id = f"{chat_id}_{user_id}_{int(datetime.now(TIMEZONE).timestamp())}"
        polls[poll_id] = {"question": question, "yes": 0, "no": 0, "voters": set()}
        logger.info(f"Created poll with ID: {poll_id}")

        keyboard = [
            [InlineKeyboardButton("Taip (0)", callback_data=f"poll_{poll_id}_yes"),
             InlineKeyboardButton("Ne (0)", callback_data=f"poll_{poll_id}_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"📊 Apklausa: {question}", reply_markup=reply_markup)
        # No deletion scheduled for /apklausa
    except IndexError:
        msg = await update.message.reply_text("Naudok: /apklausa 'Klausimas'")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def handle_poll_button(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if not data.startswith("poll_"):
        return

    parts = data.rsplit("_", 1)
    if len(parts) != 2:
        logger.error(f"Invalid callback data format: {data}")
        await query.answer("Klaida balsuojant!")
        return

    poll_id, vote = parts[0][5:], parts[1]
    logger.info(f"Poll button pressed: data={data}, poll_id={poll_id}, vote={vote}, polls.keys={list(polls.keys())}")

    if poll_id not in polls:
        logger.error(f"Poll ID {poll_id} not found in polls: {polls}")
        await query.answer("Ši apklausa nebegalioja!")
        return

    poll = polls[poll_id]
    if user_id in poll["voters"]:
        await query.answer("Jau balsavai šioje apklausoje!")
        return

    poll["voters"].add(user_id)
    if vote == "yes":
        poll["yes"] += 1
    elif vote == "no":
        poll["no"] += 1
    else:
        logger.error(f"Invalid vote type: {vote}")
        await query.answer("Klaida balsuojant!")
        return

    keyboard = [
        [InlineKeyboardButton(f"Taip ({poll['yes']})", callback_data=f"poll_{poll_id}_yes"),
         InlineKeyboardButton(f"Ne ({poll['no']})", callback_data=f"poll_{poll_id}_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"📊 Apklausa: {poll['question']}\nBalsai: Taip - {poll['yes']}, Ne - {poll['no']}", reply_markup=reply_markup)
    await query.answer("Tavo balsas užskaitytas!")

async def nepatiko(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    if not is_allowed_group(chat_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    
    now = datetime.now(TIMEZONE)
    if now - last_downvote_attempt[user_id] < timedelta(days=7):
        msg = await update.message.reply_text("Palauk 7 dienas po paskutinio nepritarimo!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    
    try:
        vendor = context.args[0]
        if not vendor.startswith('@'):
            vendor = '@' + vendor  # Normalize by adding '@'
        reason = " ".join(context.args[1:])
        if not reason:
            msg = await update.message.reply_text("Prašau nurodyti priežastį!")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return
        
        global complaint_id
        complaint_id += 1
        pending_downvotes[complaint_id] = (vendor, user_id, reason, now)
        downvoters.add(user_id)
        vote_history.setdefault(vendor, []).append((user_id, "down", reason, now))
        user_points[user_id] += 5
        last_downvote_attempt[user_id] = now
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"Skundas #{complaint_id}: {vendor} - '{reason}' by User {user_id}. Patvirtinti su /approve {complaint_id}"
        )
        msg = await update.message.reply_text(f"Skundas pateiktas! Atsiųsk įrodymus @kunigasnew dėl Skundo #{complaint_id}. +5 taškų!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        save_data(vote_history, 'vote_history.pkl')
        save_data(user_points, 'user_points.pkl')
    except IndexError:
        msg = await update.message.reply_text("Naudok: /nepatiko @VendorTag 'Reason'")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def approve(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        return
    if not (is_allowed_group(chat_id) or chat_id == int(user_id)):
        msg = await update.message.reply_text("Ši komanda veikia tik grupėje arba privačiai!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    try:
        cid = int(context.args[0])
        if cid not in pending_downvotes:
            msg = await update.message.reply_text("Neteisingas skundo ID!")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return
        vendor, user_id, reason, timestamp = pending_downvotes[cid]
        votes_weekly[vendor] -= 1
        votes_monthly[vendor].append((timestamp, -1))
        votes_alltime[vendor] -= 1
        approved_downvotes[cid] = pending_downvotes[cid]
        del pending_downvotes[cid]
        msg = await update.message.reply_text(f"Skundas patvirtintas dėl {vendor}!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        save_data(votes_weekly, 'votes_weekly.pkl')
        save_data(votes_monthly, 'votes_monthly.pkl')
        save_data(votes_alltime, 'votes_alltime.pkl')
    except (IndexError, ValueError):
        msg = await update.message.reply_text("Naudok: /approve ComplaintID")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def addseller(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        msg = await update.message.reply_text("Tik adminas gali pridėti pardavėją!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    if not is_allowed_group(chat_id) and chat_id != int(user_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje arba naudok privačiai!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    try:
        vendor = context.args[0]
        if not vendor.startswith('@'):
            vendor = '@' + vendor  # Normalize by adding '@'
        if vendor in trusted_sellers:
            msg = await update.message.reply_text(f"{vendor} jau yra patikimų pardavėjų sąraše!")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return
        trusted_sellers.append(vendor)
        msg = await update.message.reply_text(f"Pardavėjas {vendor} pridėtas! Jis dabar matomas /balsuoju sąraše.")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
    except IndexError:
        msg = await update.message.reply_text("Naudok: /addseller @VendorTag")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def removeseller(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        msg = await update.message.reply_text("Tik adminas gali pašalinti pardavėją!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    if not is_allowed_group(chat_id) and chat_id != int(user_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje arba naudok privačiai!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    try:
        vendor = context.args[0]
        if not vendor.startswith('@'):
            vendor = '@' + vendor  # Normalize by adding '@'
        if vendor not in trusted_sellers:
            msg = await update.message.reply_text(f"'{vendor}' nėra patikimų pardavėjų sąraše! Sąrašas: {trusted_sellers}")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return
        trusted_sellers.remove(vendor)
        votes_weekly.pop(vendor, None)
        votes_monthly.pop(vendor, None)
        votes_alltime.pop(vendor, None)
        msg = await update.message.reply_text(f"Pardavėjas {vendor} pašalintas iš sąrašo ir balsų!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        save_data(votes_weekly, 'votes_weekly.pkl')
        save_data(votes_monthly, 'votes_monthly.pkl')
        save_data(votes_alltime, 'votes_alltime.pkl')
    except IndexError:
        msg = await update.message.reply_text("Naudok: /removeseller @VendorTag")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def sellerinfo(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    try:
        vendor = context.args[0]
        if not vendor.startswith('@'):
            vendor = '@' + vendor  # Normalize by adding '@'
        if vendor not in trusted_sellers:
            msg = await update.message.reply_text(f"{vendor} nėra patikimas pardavėjas!")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return
        now = datetime.now(TIMEZONE)
        monthly_score = sum(s for ts, s in votes_monthly[vendor] if now - ts < timedelta(days=30))
        downvotes_30d = sum(1 for cid, (v, _, _, ts) in approved_downvotes.items() if v == vendor and now - ts < timedelta(days=30))
        info = f"{vendor} Info:\nSavaitė: {votes_weekly[vendor]}\nMėnuo: {monthly_score}\nViso: {votes_alltime[vendor]}\nNeigiami (30d): {downvotes_30d}"
        msg = await update.message.reply_text(info)
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
    except IndexError:
        msg = await update.message.reply_text("Naudok: /pardavejoinfo @VendorTag")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def barygos(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    now = datetime.now(TIMEZONE)
    
    message = ""
    if last_addftbaryga2_message:
        message += f"{last_addftbaryga2_message}\n\n"
    
    weekly_board = "🏆 Savaitės Top Pardavėjai 🏆\n"
    if not votes_weekly:
        weekly_board += "Dar nėra balsų šią savaitę!\n"
    else:
        sorted_weekly = sorted(votes_weekly.items(), key=lambda x: x[1], reverse=True)
        for vendor, score in sorted_weekly:  # Show all sellers
            weekly_board += f"{vendor[1:]}: {score}\n"  # Remove @ from vendor name
    
    monthly_board = "📅 Mėnesio Top Pardavėjai 📅\n"
    monthly_totals = defaultdict(int)
    for vendor, votes_list in votes_monthly.items():
        recent_votes = [(ts, s) for ts, s in votes_list if now - ts < timedelta(days=30)]
        monthly_totals[vendor] = sum(s for _, s in recent_votes)
    if not monthly_totals:
        monthly_board += "Nėra balsų per 30 dienų!\n"
    else:
        sorted_monthly = sorted(monthly_totals.items(), key=lambda x: x[1], reverse=True)
        for vendor, score in sorted_monthly[:15]:  # Show top 15 sellers
            monthly_board += f"{vendor[1:]}: {score}\n"  # Remove @ from vendor name
    
    alltime_board = "🌟 Visų Laikų Top 10 Pardavėjai 🌟\n"
    if not votes_alltime:
        alltime_board += "Dar nėra balsų!\n"
    else:
        sorted_alltime = sorted(votes_alltime.items(), key=lambda x: x[1], reverse=True)
        for i, (vendor, score) in enumerate(sorted_alltime[:10], 1):  # Show top 10 sellers
            alltime_board += f"{i}. {vendor[1:]}: {score}\n"  # Remove @ from vendor name
    
    full_message = f"{message}{weekly_board}\n{monthly_board}\n{alltime_board}"
    if 'barygos_media_id' in globals() and barygos_media_id and barygos_media_type:
        if barygos_media_type == 'photo':
            msg = await context.bot.send_photo(chat_id=chat_id, photo=barygos_media_id, caption=full_message)
        elif barygos_media_type == 'animation':
            msg = await context.bot.send_animation(chat_id=chat_id, animation=barygos_media_id, caption=full_message)
        elif barygos_media_type == 'video':
            msg = await context.bot.send_video(chat_id=chat_id, video=barygos_media_id, caption=full_message)
    else:
        msg = await context.bot.send_message(chat_id=chat_id, text=full_message)
    context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def chatking(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    
    if not alltime_messages:
        msg = await update.message.reply_text("Dar nėra žinučių!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    
    sorted_chatters = sorted(alltime_messages.items(), key=lambda x: x[1], reverse=True)[:10]
    leaderboard = "👑 Visų Laikų Pokalbių Karaliai 👑\n"
    for user_id, msg_count in sorted_chatters:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            username = f"@{member.user.username}" if member.user.username else f"User {user_id}"
            leaderboard += f"{username}: {msg_count} žinučių\n"
        except telegram.error.TelegramError:
            leaderboard += f"User {user_id}: {msg_count} žinučių\n"
    
    msg = await update.message.reply_text(leaderboard)
    context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def handle_message(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id) or update.message.text.startswith('/'):
        return
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    if username:
        username_to_id[f"@{username.lower()}"] = user_id
    
    today = datetime.now(TIMEZONE)
    daily_messages[user_id][today.date()] += 1
    weekly_messages[user_id] += 1
    alltime_messages.setdefault(user_id, 0)
    alltime_messages[user_id] += 1
    
    yesterday = today - timedelta(days=1)
    last_day = last_chat_day[user_id].date()
    if last_day == yesterday.date():
        chat_streaks[user_id] += 1
    elif last_day != today.date():
        chat_streaks[user_id] = 1
    last_chat_day[user_id] = today
    save_data(alltime_messages, 'alltime_messages.pkl')
    save_data(chat_streaks, 'chat_streaks.pkl')
    save_data(last_chat_day, 'last_chat_day.pkl')

async def award_daily_points(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    today = datetime.now(TIMEZONE).date()
    yesterday = today - timedelta(days=1)
    for user_id in daily_messages:
        msg_count = daily_messages[user_id].get(yesterday, 0)
        if msg_count < 50:
            continue
        
        chat_points = min(3, (msg_count // 50))
        streak_bonus = chat_streaks[user_id] // 3
        total_points = chat_points + streak_bonus
        user_points[user_id] += total_points
        
        msg = f"Gavai {chat_points} taškus už {msg_count} žinučių vakar!"
        if streak_bonus > 0:
            msg += f" +{streak_bonus} už {chat_streaks[user_id]}-dienų seriją!"
        
        try:
            username = next(k for k, v in username_to_id.items() if v == user_id)
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=f"{username}, {msg} Dabar turi {user_points[user_id]} taškų!"
            )
        except StopIteration:
            pass
    
    daily_messages.clear()
    save_data(user_points, 'user_points.pkl')

async def weekly_recap(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    if not weekly_messages:
        return
    
    sorted_chatters = sorted(weekly_messages.items(), key=lambda x: x[1], reverse=True)[:3]
    recap = "📢 Savaitės Pokalbių Karaliai 📢\n"
    for user_id, msg_count in sorted_chatters:
        try:
            username = next(k for k, v in username_to_id.items() if v == user_id)
            recap += f"{username}: {msg_count} žinučių\n"
        except StopIteration:
            recap += f"User {user_id}: {msg_count} žinučių\n"
    
    await context.bot.send_message(GROUP_CHAT_ID, recap)
    weekly_messages.clear()

async def coinflip(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    initiator_id = update.message.from_user.id
    try:
        amount = int(context.args[0])
        opponent = context.args[1]
        
        if amount <= 0 or user_points[initiator_id] < amount:
            msg = await update.message.reply_text("Netinkama suma arba trūksta taškų!")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return
        
        initiator_member = await context.bot.get_chat_member(chat_id, initiator_id)
        initiator_username = f"@{initiator_member.user.username}" if initiator_member.user.username else f"@User{initiator_id}"

        target_id = username_to_id.get(opponent.lower(), None)
        if not target_id or opponent == initiator_username:
            msg = await update.message.reply_text("Negalima mesti iššūkio sau ar neegzistuojančiam vartotojui!")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return
        
        opponent_tag = opponent
        if user_points[target_id] < amount:
            msg = await update.message.reply_text(f"{opponent_tag} neturi pakankamai taškų!")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return
        
        coinflip_challenges[target_id] = (initiator_id, amount, datetime.now(TIMEZONE), initiator_username, opponent_tag, chat_id)
        msg = await update.message.reply_text(f"{initiator_username} iššaukė {opponent_tag} monetos metimui už {amount} taškų! Priimk su /accept_coinflip!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        context.job_queue.run_once(expire_challenge, 300, context=(target_id, context))
    except (IndexError, ValueError):
        msg = await update.message.reply_text("Naudok: /coinflip Amount @Username")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def accept_coinflip(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    if user_id not in coinflip_challenges:
        msg = await update.message.reply_text("Nėra aktyvaus iššūkio!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    initiator_id, amount, timestamp, initiator_username, opponent_username, original_chat_id = coinflip_challenges[user_id]
    now = datetime.now(TIMEZONE)
    if now - timestamp > timedelta(minutes=5) or chat_id != original_chat_id:
        del coinflip_challenges[user_id]
        msg = await update.message.reply_text("Iššūkis pasibaigė arba neteisinga grupė!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    result = random.choice([initiator_id, user_id])
    await context.bot.send_sticker(chat_id=chat_id, sticker=COINFLIP_STICKER_ID)
    if result == initiator_id:
        user_points[initiator_id] += amount
        user_points[user_id] -= amount
        msg = await update.message.reply_text(f"🪙 {initiator_username} laimėjo {amount} taškų prieš {opponent_username}!")
    else:
        user_points[user_id] += amount
        user_points[initiator_id] -= amount
        msg = await update.message.reply_text(f"🪙 {opponent_username} laimėjo {amount} taškų prieš {initiator_username}!")
    context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
    del coinflip_challenges[user_id]
    save_data(user_points, 'user_points.pkl')

async def expire_challenge(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    opponent_id, ctx = context.job.context
    if opponent_id in coinflip_challenges:
        _, amount, _, initiator_username, opponent_username, chat_id = coinflip_challenges[opponent_id]
        del coinflip_challenges[opponent_id]
        msg = await ctx.bot.send_message(chat_id, f"Iššūkis tarp {initiator_username} ir {opponent_username} už {amount} taškų pasibaigė!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def addpoints(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        msg = await update.message.reply_text("Tik adminas gali pridėti taškus!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    try:
        amount = int(context.args[0])
        target = context.args[1]
        target_id = int(target.strip('@User'))
        user_points[target_id] += amount
        msg = await update.message.reply_text(f"Pridėta {amount} taškų @User{target_id}! Dabar: {user_points[target_id]}")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        save_data(user_points, 'user_points.pkl')
    except (IndexError, ValueError):
        msg = await update.message.reply_text("Naudok: /addpoints Amount @UserID")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def pridetitaskus(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        msg = await update.message.reply_text("Tik adminas gali naudoti šią komandą!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        return
    try:
        seller = context.args[0]
        if not seller.startswith('@'):
            seller = '@' + seller  # Normalize by adding '@'
        amount = int(context.args[1])
        if seller not in trusted_sellers:
            msg = await update.message.reply_text(f"{seller} nėra patikimų pardavėjų sąraše!")
            context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
            return
        votes_alltime[seller] += amount
        msg = await update.message.reply_text(f"Pridėta {amount} taškų {seller} visų laikų balsams. Dabar: {votes_alltime[seller]}")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        save_data(votes_alltime, 'votes_alltime.pkl')
    except (IndexError, ValueError):
        msg = await update.message.reply_text("Naudok: /pridetitaskus @Seller Amount")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))

async def points(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    logger.info(f"/points called by user_id={user_id} in chat_id={chat_id}")

    if not is_allowed_group(chat_id):
        msg = await update.message.reply_text("Botas neveikia šioje grupėje!")
        context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
        logger.warning(f"Chat_id={chat_id} not in allowed_groups={allowed_groups}")
        return

    points = user_points.get(user_id, 0)
    streak = chat_streaks.get(user_id, 0)
    msg = await update.message.reply_text(f"Jūsų taškai: {points}\nSerija: {streak} dienų")
    context.job_queue.run_once(delete_message_job, 45, context=(chat_id, msg.message_id))
    logger.info(f"Points for user_id={user_id}: {points}, Streak: {streak}")

async def reset_votes(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    global votes_weekly, voters, downvoters, pending_downvotes, complaint_id, last_vote_attempt
    votes_weekly.clear()
    voters.clear()
    downvoters.clear()
    pending_downvotes.clear()
    last_vote_attempt.clear()
    complaint_id = 0
    await context.bot.send_message(GROUP_CHAT_ID, "Nauja balsavimo savaitė prasidėjo!")
    save_data(votes_weekly, 'votes_weekly.pkl')

async def reset_monthly_votes(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    global votes_monthly
    votes_monthly.clear()
    await context.bot.send_message(GROUP_CHAT_ID, "Naujas balsavimo mėnuo prasidėjo!")
    save_data(votes_monthly, 'votes_monthly.pkl')

# Add handlers
application.add_handler(CommandHandler(['startas'], startas))
application.add_handler(CommandHandler(['activate_group'], activate_group))
application.add_handler(CommandHandler(['nepatiko'], nepatiko))
application.add_handler(CommandHandler(['approve'], approve))
application.add_handler(CommandHandler(['addseller'], addseller))
application.add_handler(CommandHandler(['removeseller'], removeseller))
application.add_handler(CommandHandler(['pardavejoinfo'], sellerinfo))
application.add_handler(CommandHandler(['barygos'], barygos))
application.add_handler(CommandHandler(['balsuoju'], balsuoju))
application.add_handler(CommandHandler(['chatking'], chatking))
application.add_handler(CommandHandler(['coinflip'], coinflip))
application.add_handler(CommandHandler(['accept_coinflip'], accept_coinflip))
application.add_handler(CommandHandler(['addpoints'], addpoints))
application.add_handler(CommandHandler(['pridetitaskus'], pridetitaskus))
application.add_handler(CommandHandler(['points'], points))
application.add_handler(CommandHandler(['debug'], debug))
application.add_handler(CommandHandler(['whoami'], whoami))
application.add_handler(CommandHandler(['addftbaryga'], addftbaryga))
application.add_handler(CommandHandler(['addftbaryga2'], addftbaryga2))
application.add_handler(CommandHandler(['editpardavejai'], editpardavejai))
application.add_handler(CommandHandler(['apklausa'], apklausa))  # Fixed: Corrected from 'ap Inclusionlausa' to 'apklausa'
application.add_handler(CommandHandler(['privatus'], privatus))
application.add_handler(MessageHandler(filters.Regex('^/start$') & filters.ChatType.PRIVATE, start_private))
application.add_handler(CallbackQueryHandler(handle_vote_button, pattern="vote_"))
application.add_handler(CallbackQueryHandler(handle_poll_button, pattern="poll_"))
application.add_handler(CallbackQueryHandler(handle_admin_button, pattern="admin_"))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Schedule jobs
application.job_queue.run_daily(award_daily_points, time=time(hour=0, minute=0))
application.job_queue.scheduler.add_job(
    weekly_recap, CronTrigger(day_of_week='sun', hour=23, minute=0, timezone=TIMEZONE), args=[application], id='weekly_recap'
)
application.job_queue.scheduler.add_job(
    reset_votes, CronTrigger(day_of_week='mon', hour=0, minute=0, timezone=TIMEZONE), args=[application], id='reset_votes_weekly'
)
application.job_queue.scheduler.add_job(
    reset_monthly_votes, CronTrigger(day=1, hour=0, minute=0, timezone=TIMEZONE), args=[application], id='reset_votes_monthly'
)

if __name__ == '__main__':  
    try:  
        logger.info("Starting bot polling...")  
        application.run_polling()  
    except Exception as e:  
        logger.error(f"Polling failed: {str(e)}")  
    logger.info("Bot polling stopped.")
