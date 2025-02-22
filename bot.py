import telegram
from telegram.ext import Application, CommandHandler, MessageHandler, filters
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

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
TOKEN = '7770681728:AAHfiSsbREtVz0t9oBH032nPaybnUcfbKb4'
ADMIN_CHAT_ID = '6705617539'  # Your user ID
GROUP_CHAT_ID = '-1001381982408'
TIMEZONE = pytz.timezone('Europe/Vilnius')  # Lithuania timezone
COINFLIP_STICKER_ID = 'CAACAgIAAxkBAAEN32tnuPb-ovynJR5WNO1TQyv_ea17DwAC-RkAAtswEEqAzfrZRd8B1zYE'

# Save and load data functions
def load_data(filename, default):
    if os.path.exists(filename):
        with open(filename, 'rb') as f:
            return pickle.load(f)
    return default

def save_data(data, filename):
    with open(filename, 'wb') as f:
        pickle.dump(data, f)

# Create scheduler with our timezone
scheduler = AsyncIOScheduler(timezone=TIMEZONE)
scheduler.add_executor(ThreadPoolExecutor(max_workers=10), alias='default')

# Post-init callback to configure job queue with our scheduler
async def configure_scheduler(application):
    application.job_queue.scheduler = scheduler
    scheduler.start()

# Build Application
application = Application.builder().token(TOKEN).post_init(configure_scheduler).build()

# Data storage - Load from files
trusted_sellers = ['@Seller1', '@Seller2', '@Seller3']  # Add your sellers here
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
last_chat_day = load_data('last_chat_day.pkl', defaultdict(lambda: datetime.min.replace(tzinfo=TIMEZONE)))
allowed_groups = {GROUP_CHAT_ID}
PASSWORD = 'shoebot123'
valid_licenses = {'LICENSE-XYZ123', 'LICENSE-ABC456'}
pending_activation = {}
username_to_id = {}

# Eligibility check (7-day membership)
def is_eligible_voter(bot, user_id: int, chat_id: str) -> bool:
    try:
        member = bot.get_chat_member(chat_id, user_id)
        join_date = member.joined_date or datetime.now(TIMEZONE)
        eligible = (datetime.now(TIMEZONE) - join_date).days >= 7
        logger.info(f"User {user_id} eligibility: {eligible} (Joined: {join_date})")
        return eligible
    except telegram.error.TelegramError as e:
        logger.error(f"Eligibility check failed for {user_id}: {str(e)}")
        return False

# Group restriction check
def is_allowed_group(chat_id: str) -> bool:
    allowed = str(chat_id) in allowed_groups
    logger.info(f"Chat {chat_id} allowed: {allowed}")
    return allowed

# Debug command
async def debug(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali naudoti šią komandą! Only admin can use this!")
        return
    chat_id = update.message.chat_id
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_list = "\n".join([f"@{m.user.username or m.user.id} (ID: {m.user.id})" for m in admins])
        cache_list = "\n".join([f"{k}: {v}" for k, v in username_to_id.items()])
        streak_list = "\n".join([f"User {k}: {v} days" for k, v in chat_streaks.items()])
        weekly_list = "\n".join([f"User {k}: {v} msgs" for k, v in weekly_messages.items()])
        alltime_list = "\n".join([f"User {k}: {v} msgs" for k, v in alltime_messages.items()])
        await update.message.reply_text(f"Matomi adminai:\n{admin_list}\n\nCache:\n{cache_list}\n\nStreaks:\n{streak_list}\n\nWeekly:\n{weekly_list}\n\nAll-Time:\n{alltime_list}")
        logger.info(f"Admins in {chat_id}: {admin_list}\nCache: {cache_list}\nStreaks: {streak_list}\nWeekly: {weekly_list}\nAll-Time: {alltime_list}")
    except telegram.error.TelegramError as e:
        logger.error(f"Debug failed: {str(e)}")
        await update.message.reply_text(f"Debug failed: {str(e)}")

# Whoami command
async def whoami(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        username = f"@{member.user.username}" if member.user.username else "No username"
        await update.message.reply_text(f"Jūs esate: {username} (ID: {user_id})")
        logger.info(f"Whoami: User {user_id} is {username} in chat {chat_id}")
    except telegram.error.TelegramError as e:
        logger.error(f"Whoami failed for {user_id}: {str(e)}")
        await update.message.reply_text(f"Error: {str(e)}")

# Start command
async def startas(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    if chat_id != user_id:  # Group chat
        if is_allowed_group(chat_id):
            await update.message.reply_text(
                "Sveiki! Vote with /balsuoju or /nepatiko (5 pts). Chat daily for 1-3 pts (50+ = 1, 100+ = 2, 250+ = 3 max) + streaks. Check /topvendoriai or /chatking. Gamble with /coinflip!"
            )
        else:
            await update.message.reply_text("Šis botas skirtas tik mano grupėms! Siųsk /startas Password privačiai!")
    else:  # Private chat
        try:
            password = context.args[0]
            if password == PASSWORD:
                pending_activation[user_id] = "password"
                await update.message.reply_text("Slaptažodis teisingas! Siųsk /activate_group GroupChatID.")
            else:
                await update.message.reply_text("Neteisingas slaptažodis!")
        except IndexError:
            await update.message.reply_text("Naudok: /startas Password privačiai!")

# Activate with license
async def activate_with_license(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    if chat_id != user_id:
        await update.message.reply_text("Siųsk /license Key privačiai!")
        return
    try:
        license_key = context.args[0]
        if license_key in valid_licenses:
            pending_activation[user_id] = license_key
            await update.message.reply_text("Licencija teisinga! Siųsk /activate_group GroupChatID.")
        else:
            await update.message.reply_text("Neteisinga licencija! Pirk iš @kunigasnew.")
    except IndexError:
        await update.message.reply_text("Naudok: /license LicenseKey privačiai!")

# Activate group
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

# Positive vote
async def balsuoju(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    logger.info(f"/balsuoju called by user {user_id} in chat {chat_id}")
    
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    
    now = datetime.now(TIMEZONE)
    if now - last_vote_attempt[user_id] < timedelta(days=7):
        await update.message.reply_text("Palauk 7 dienas po paskutinio balsavimo!")
        return
    
    try:
        vendor = context.args[0]
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
        logger.info(f"User {user_id} voting for {vendor} with reason: {reason}")
        
        if vendor not in trusted_sellers:
            await update.message.reply_text(f"{vendor} nėra patikimas pardavėjas!")
            return
        
        timestamp = datetime.now(TIMEZONE)
        votes_weekly[vendor] += 1
        votes_monthly[vendor].append((timestamp, 1))
        votes_alltime[vendor] += 1
        voters.add(user_id)
        vote_history[vendor].append((user_id, "up", reason, timestamp))
        user_points[user_id] += 5
        last_vote_attempt[user_id] = now
        await update.message.reply_text(f"Ačiū! You voted for {vendor}{f' - {reason}' if reason else ''}. +5 taškų!")
        logger.info(f"Vote successful for {vendor} by user {user_id}")
        # Save data
        save_data(votes_weekly, 'votes_weekly.pkl')
        save_data(votes_monthly, 'votes_monthly.pkl')
        save_data(votes_alltime, 'votes_alltime.pkl')
        save_data(vote_history, 'vote_history.pkl')
        save_data(user_points, 'user_points.pkl')
    except IndexError:
        await update.message.reply_text("Naudok: /balsuoju @VendorTag ['Reason']")

# Downvote
async def nepatiko(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    logger.info(f"/nepatiko called by user {user_id} in chat {chat_id}")
    
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    
    now = datetime.now(TIMEZONE)
    if now - last_downvote_attempt[user_id] < timedelta(days=7):
        await update.message.reply_text("Palauk 7 dienas po paskutinio nepritarimo!")
        return
    
    try:
        vendor, reason = context.args[0], " ".join(context.args[1:])
        logger.info(f"User {user_id} downvoting {vendor} with reason: {reason}")
        
        if vendor not in trusted_sellers:
            await update.message.reply_text(f"{vendor} nėra patikimas pardavėjas!")
            return
        if not reason:
            await update.message.reply_text("Prašau nurodyti priežastį!")
            return
        
        global complaint_id
        complaint_id += 1
        timestamp = datetime.now(TIMEZONE)
        pending_downvotes[complaint_id] = (vendor, user_id, reason, timestamp)
        downvoters.add(user_id)
        vote_history[vendor].append((user_id, "down", reason, timestamp))
        user_points[user_id] += 5
        last_downvote_attempt[user_id] = now
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"Skundas #{complaint_id}: {vendor} - '{reason}' by User {user_id}. Patvirtinti su /approve {complaint_id} arba atmesti su /reject {complaint_id}"
        )
        await update.message.reply_text(f"Skundas pateiktas! Prašau atsiųsti įrodymus @kunigasnew dėl Skundo #{complaint_id}. +5 taškų!")
        logger.info(f"Downvote successful for {vendor} by user {user_id}, complaint #{complaint_id}")
        # Save data
        save_data(vote_history, 'vote_history.pkl')
        save_data(user_points, 'user_points.pkl')
    except IndexError:
        await update.message.reply_text("Naudok: /nepatiko @VendorTag 'Reason'")

# Approve downvote
async def approve(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        return
    try:
        cid = int(context.args[0])
        if cid not in pending_downvotes:
            await update.message.reply_text("Neteisingas skundo ID!")
            return
        vendor, user_id, reason, timestamp = pending_downvotes[cid]
        votes_weekly[vendor] -= 1
        votes_monthly[vendor].append((timestamp, -1))
        votes_alltime[vendor] -= 1
        approved_downvotes[cid] = pending_downvotes[cid]
        del pending_downvotes[cid]
        await update.message.reply_text(f"Skundas patvirtintas dėl {vendor}!")
        logger.info(f"Downvote #{cid} approved for {vendor} by admin {user_id}")
        # Save data
        save_data(votes_weekly, 'votes_weekly.pkl')
        save_data(votes_monthly, 'votes_monthly.pkl')
        save_data(votes_alltime, 'votes_alltime.pkl')
    except (IndexError, ValueError):
        await update.message.reply_text("Naudok: /approve ComplaintID")

# Reject downvote
async def reject(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        return
    try:
        cid = int(context.args[0])
        if cid in pending_downvotes:
            del pending_downvotes[cid]
            await update.message.reply_text("Skundas atmestas!")
            logger.info(f"Downvote #{cid} rejected by admin {user_id}")
    except (IndexError, ValueError):
        await update.message.reply_text("Naudok: /reject ComplaintID")

# Reopen downvote
async def reopen(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        return
    try:
        cid = int(context.args[0])
        if cid not in approved_downvotes:
            await update.message.reply_text("Neteisingas skundo ID!")
            return
        vendor, user_id, reason, timestamp = approved_downvotes[cid]
        pending_downvotes[cid] = (vendor, user_id, reason, timestamp)
        del approved_downvotes[cid]
        votes_weekly[vendor] += 1
        votes_monthly[vendor].append((datetime.now(TIMEZONE), 1))
        votes_alltime[vendor] += 1
        await update.message.reply_text(f"Skundas #{cid} peržiūrimas iš naujo!")
        logger.info(f"Downvote #{cid} reopened by admin {user_id}")
        # Save data
        save_data(votes_weekly, 'votes_weekly.pkl')
        save_data(votes_monthly, 'votes_monthly.pkl')
        save_data(votes_alltime, 'votes_alltime.pkl')
    except (IndexError, ValueError):
        await update.message.reply_text("Naudok: /perziureti ComplaintID")

# Add trusted seller
async def addseller(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        return
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    try:
        vendor = context.args[0]
        if vendor in trusted_sellers:
            await update.message.reply_text(f"{vendor} jau yra patikimų pardavėjų sąraše!")
            return
        trusted_sellers.append(vendor)
        await update.message.reply_text(f"Pardavėjas {vendor} pridėtas!")
        logger.info(f"Seller {vendor} added by admin {user_id}")
    except IndexError:
        await update.message.reply_text("Naudok: /addseller @VendorTag")

# Remove trusted seller
async def removeseller(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        return
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    try:
        vendor = context.args[0]
        if vendor not in trusted_sellers:
            await update.message.reply_text(f"{vendor} nėra patikimų pardavėjų sąraše!")
            return
        trusted_sellers.remove(vendor)
        await update.message.reply_text(f"Pardavėjas {vendor} pašalintas!")
        logger.info(f"Seller {vendor} removed by admin {user_id}")
    except IndexError:
        await update.message.reply_text("Naudok: /removeseller @VendorTag")

# Seller info
async def sellerinfo(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    try:
        vendor = context.args[0]
        if vendor not in trusted_sellers:
            await update.message.reply_text(f"{vendor} nėra patikimas pardavėjas!")
            return
        now = datetime.now(TIMEZONE)
        monthly_score = sum(s for ts, s in votes_monthly[vendor] if now - ts < timedelta(days=30))
        downvotes_30d = sum(1 for cid, (v, _, _, ts) in approved_downvotes.items() if v == vendor and now - ts < timedelta(days=30))
        info = f"{vendor} Info:\nSavaitė: {votes_weekly[vendor]}\nMėnuo: {monthly_score}\nViso: {votes_alltime[vendor]}\nNeigiami (30d): {downvotes_30d}"
        await update.message.reply_text(info)
    except IndexError:
        await update.message.reply_text("Naudok: /pardavejoinfo @VendorTag")

# Vendor leaderboard
async def topvendoriai(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    now = datetime.now(TIMEZONE)
    weekly_board = "🏆 Savaitės Top Pardavėjai 🏆\n"
    if not votes_weekly:
        weekly_board += "Dar nėra balsų šią savaitę!\n"
    else:
        sorted_weekly = sorted(votes_weekly.items(), key=lambda x: x[1], reverse=True)
        for vendor, score in sorted_weekly[:3]:
            weekly_board += f"{vendor}: {score}\n"
    
    monthly_board = "📅 Mėnesio Top Pardavėjai 📅\n"
    monthly_totals = defaultdict(int)
    for vendor, votes_list in votes_monthly.items():
        votes_list[:] = [(ts, s) for ts, s in votes_list if now - ts < timedelta(days=30)]
        monthly_totals[vendor] = sum(s for _, s in votes_list)
    if not monthly_totals:
        monthly_board += "Nėra balsų per 30 dienų!\n"
    else:
        sorted_monthly = sorted(monthly_totals.items(), key=lambda x: x[1], reverse=True)
        for vendor, score in sorted_monthly[:3]:
            monthly_board += f"{vendor}: {score}\n"
    
    alltime_board = "🌟 Visų Laikų Top Pardavėjas 🌟\n"
    if not votes_alltime:
        alltime_board += "Dar nėra balsų!\n"
    else:
        sorted_alltime = sorted(votes_alltime.items(), key=lambda x: x[1], reverse=True)
        top_vendor, top_score = sorted_alltime[0]
        alltime_board += f"{top_vendor}: {top_score}\n"
    
    await update.message.reply_text(f"{weekly_board}\n{monthly_board}\n{alltime_board}")

# All-time chat leaderboard
async def chatking(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    
    if not alltime_messages:
        await update.message.reply_text("Dar nėra žinučių!")
        return
    
    sorted_chatters = sorted(alltime_messages.items(), key=lambda x: x[1], reverse=True)[:10]
    leaderboard = "👑 Visų Laikų Pokalbių Karaliai 👑\n"
    for user_id, msg_count in sorted_chatters:
        try:
            username = next(k for k, v in username_to_id.items() if v == user_id)
            leaderboard += f"{username}: {msg_count} žinučių\n"
        except StopIteration:
            leaderboard += f"User {user_id}: {msg_count} žinučių\n"
    
    await update.message.reply_text(leaderboard)
    logger.info(f"Chatking leaderboard shown: {leaderboard}")

# Vote log
async def votelog(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali peržiūrėti balsų istoriją!")
        return
    try:
        vendor = context.args[0]
        if vendor not in trusted_sellers:
            await update.message.reply_text(f"{vendor} nėra patikimas pardavėjas!")
            return
        log = f"Balsų Istorija for {vendor} (Šią Savaitę):\n"
        now = datetime.now(TIMEZONE)
        for user_id, vote_type, reason, timestamp in vote_history[vendor]:
            if now - timestamp < timedelta(days=7):
                status = "Patvirtinta" if vote_type == "up" or any(cid for cid, (v, _, _, _) in approved_downvotes.items() if v == vendor) else "Laukia"
                log += f"User {user_id}: {vote_type.upper()} - {reason} ({status}) - {timestamp.strftime('%Y-%m-%d %H:%M')}\n"
        await update.message.reply_text(log or f"Nėra balsų šią savaitę for {vendor}!")
        logger.info(f"Vote log for {vendor} shown to admin {user_id}")
    except IndexError:
        await update.message.reply_text("Naudok: /balsu_istorija @VendorTag")

# Message activity points
async def handle_message(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id) or update.message.text.startswith('/'):
        return
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    if username:
        username_to_id[f"@{username.lower()}"] = user_id
        logger.info(f"Cached @{username} as ID {user_id}")
    
    today = datetime.now(TIMEZONE)
    yesterday = today - timedelta(days=1)
    daily_messages[user_id][today.date()] += 1
    weekly_messages[user_id] += 1
    alltime_messages[user_id] += 1
    
    last_day = last_chat_day[user_id].date()
    if last_day == yesterday.date():
        chat_streaks[user_id] += 1
    elif last_day != today.date():
        chat_streaks[user_id] = 1
    last_chat_day[user_id] = today
    logger.info(f"User {user_id} chat streak: {chat_streaks[user_id]} days")
    # Save data
    save_data(alltime_messages, 'alltime_messages.pkl')
    save_data(chat_streaks, 'chat_streaks.pkl')
    save_data(last_chat_day, 'last_chat_day.pkl')

# Award daily points
async def award_daily_points(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    today = datetime.now(TIMEZONE).date()
    yesterday = today - timedelta(days=1)
    for user_id in daily_messages:
        msg_count = daily_messages[user_id].get(yesterday, 0)
        if msg_count < 50:
            continue
        
        if msg_count >= 250:
            chat_points = 3
            msg = f"{msg_count} žinučių - gavai 3 taškus už aktyvumą vakar! Chat beast!"
        elif msg_count >= 100:
            chat_points = 2
            msg = f"{msg_count} žinučių - gavai 2 taškus už aktyvumą vakar! Solid vibes!"
        else:
            chat_points = 1
            msg = f"{msg_count} žinučių - gavai 1 tašką už aktyvumą vakar! Casual chatter!"
        
        streak = chat_streaks[user_id]
        streak_bonus = streak // 3
        if streak_bonus > 0:
            msg += f" +{streak_bonus} tašką(-us) už {streak}-dienų seriją!"
        
        total_points = chat_points + streak_bonus
        user_points[user_id] += total_points
        logger.info(f"User {user_id} got {total_points} points (chat: {chat_points}, streak: {streak_bonus}, msgs: {msg_count})")
        
        try:
            username = next(k for k, v in username_to_id.items() if v == user_id)
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=f"{username}, {msg} Dabar turi {user_points[user_id]} taškų!"
            )
        except StopIteration:
            logger.warning(f"User {user_id} not in cache, no notification sent")
    
    daily_messages.clear()
    # Save data
    save_data(user_points, 'user_points.pkl')
    save_data(chat_streaks, 'chat_streaks.pkl')

# Weekly recap
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
    logger.info(f"Weekly recap posted: {recap}")

# Coinflip challenge
async def coinflip(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    initiator_id = update.message.from_user.id
    logger.info(f"coinflip called by user {initiator_id}, points: {user_points[initiator_id]}")
    try:
        amount = int(context.args[0])
        opponent = context.args[1]
        logger.info(f"Attempting coinflip with opponent: {opponent}")
        
        if amount <= 0 or user_points[initiator_id] < amount:
            await update.message.reply_text("Netinkama suma arba trūksta taškų!")
            return
        
        initiator_member = await context.bot.get_chat_member(chat_id, initiator_id)
        initiator_username = f"@{initiator_member.user.username}" if initiator_member.user.username else f"@User{initiator_id}"

        target_id = None
        opponent_tag = opponent
        if opponent.isdigit():
            target_id = int(opponent)
            try:
                member = await context.bot.get_chat_member(chat_id, target_id)
                opponent_tag = f"@{member.user.username}" if member.user.username else f"@User{target_id}"
                logger.info(f"Resolved numeric ID {target_id} to {opponent_tag}")
            except telegram.error.TelegramError as e:
                logger.error(f"get_chat_member failed for ID {target_id}: {str(e)}")
                raise ValueError(f"Could not find user ID {target_id} in this chat")
        elif opponent.startswith('@'):
            opponent_lower = opponent.lower()
            if opponent_lower in username_to_id:
                target_id = username_to_id[opponent_lower]
                try:
                    member = await context.bot.get_chat_member(chat_id, target_id)
                    opponent_tag = f"@{member.user.username}" if member.user.username else f"@User{target_id}"
                    logger.info(f"Resolved {opponent} to ID {target_id} from cache")
                except telegram.error.TelegramError as e:
                    logger.error(f"Cache validation failed for {opponent}: {str(e)}")
                    del username_to_id[opponent_lower]
                    target_id = None
            if not target_id:
                for _ in range(2):
                    try:
                        member = await context.bot.get_chat_member(chat_id, target_id if target_id else opponent)
                        target_id = member.user.id
                        opponent_tag = f"@{member.user.username}" if member.user.username else f"@User{target_id}"
                        username_to_id[opponent_lower] = target_id
                        logger.info(f"Resolved {opponent} to ID {target_id} via get_chat_member and cached")
                        break
                    except telegram.error.TelegramError as e:
                        logger.error(f"get_chat_member retry failed for {opponent}: {str(e)}")
                        await asyncio.sleep(1)
            if not target_id:
                raise ValueError(f"Could not find user {opponent} in this chat - send a message first or use numeric ID")
        else:
            raise ValueError("Opponent must be @Username or numeric UserID")
        
        opponent_id = target_id
        if opponent_id == initiator_id:
            await update.message.reply_text("Negalima mesti iššūkio sau!")
            return
        if opponent_id not in user_points or user_points[opponent_id] < amount:
            await update.message.reply_text(f"{opponent_tag} neturi pakankamai taškų!")
            return
        if opponent_id in coinflip_challenges:
            await update.message.reply_text(f"{opponent_tag} jau turi iššūkį!")
            return
        
        timestamp = datetime.now(TIMEZONE)
        coinflip_challenges[opponent_id] = (initiator_id, amount, timestamp, initiator_username, opponent_tag, chat_id)
        logger.info(f"Stored challenge: {opponent_id} -> {coinflip_challenges[opponent_id]}")
        await update.message.reply_text(f"{initiator_username} iššaukė {opponent_tag} monetos metimui už {amount} taškų! {opponent_tag} turi 5 min priimti su /accept_coinflip!")
        context.job_queue.run_once(expire_challenge, 300, context=(opponent_id, context))
    except (IndexError, ValueError, telegram.error.TelegramError) as e:
        logger.error(f"Error in coinflip: {str(e)}")
        await update.message.reply_text(f"Error: {str(e)}. Naudok: /coinflip Amount @Username")

# Accept coinflip
async def accept_coinflip(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    user_id = update.message.from_user.id
    logger.info(f"accept_coinflip called by user {user_id}, challenges: {coinflip_challenges}")
    if user_id not in coinflip_challenges:
        await update.message.reply_text("Nėra aktyvaus iššūkio jums!")
        return
    try:
        initiator_id, amount, timestamp, initiator_username, opponent_username, original_chat_id = coinflip_challenges[user_id]
        logger.info(f"Found challenge for {user_id}: {initiator_id}, {amount}, {timestamp}, {initiator_username}, {opponent_username}, {original_chat_id}")
        now = datetime.now(TIMEZONE)
        if now - timestamp > timedelta(minutes=5):
            del coinflip_challenges[user_id]
            await update.message.reply_text("Iššūkis pasibaigė!")
            return
        if chat_id != original_chat_id:
            await update.message.reply_text("Priimk iššūkį toje pačioje grupėje!")
            return
        result = random.choice([initiator_id, user_id])
        await context.bot.send_sticker(chat_id=chat_id, sticker=COINFLIP_STICKER_ID)
        if result == initiator_id:
            user_points[initiator_id] += amount
            user_points[user_id] -= amount
            await update.message.reply_text(f"🪙 Monetos metimas: {initiator_username} vs {opponent_username} ({amount} taškų)\nLaimėtojas: {initiator_username} 🎉")
        else:
            user_points[user_id] += amount
            user_points[initiator_id] -= amount
            await update.message.reply_text(f"🪙 Monetos metimas: {initiator_username} vs {opponent_username} ({amount} taškų)\nLaimėtojas: {opponent_username} 🎉")
        del coinflip_challenges[user_id]
        logger.info(f"Challenge accepted and removed for {user_id}")
        # Save data
        save_data(user_points, 'user_points.pkl')
    except Exception as e:
        logger.error(f"Error in accept_coinflip: {str(e)}")
        await update.message.reply_text(f"Error accepting challenge: {str(e)}")

# Expire challenge
async def expire_challenge(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    opponent_id, ctx = context.job.context
    if opponent_id in coinflip_challenges:
        initiator_id, amount, _, initiator_username, opponent_username, chat_id = coinflip_challenges[opponent_id]
        del coinflip_challenges[opponent_id]
        await ctx.bot.send_message(chat_id, f"Iššūkis tarp {initiator_username} ir {opponent_username} už {amount} taškų pasibaigė!")
        logger.info(f"Challenge expired for {opponent_id}")

# Add points
async def addpoints(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    logger.info(f"addpoints called by user {user_id}")
    if user_id != ADMIN_CHAT_ID:
        logger.info(f"User {user_id} is not admin ({ADMIN_CHAT_ID}), skipping")
        await update.message.reply_text("Tik adminas gali pridėti taškus!")
        return
    try:
        amount = int(context.args[0])
        target = context.args[1]
        logger.info(f"Attempting to add {amount} points to {target}")
        
        chat_id = update.message.chat_id
        target_id = None
        
        if target.isdigit():
            target_id = int(target)
            try:
                member = await context.bot.get_chat_member(chat_id, target_id)
                target = f"@{member.user.username}" if member.user.username else f"@User{target_id}"
                logger.info(f"Resolved numeric ID {target_id} to {target}")
            except telegram.error.TelegramError as e:
                logger.error(f"get_chat_member failed for ID {target_id}: {str(e)}")
                raise ValueError(f"Could not find user ID {target_id} in this chat")
        elif target.startswith('@'):
            target_lower = target.lower()
            if target_lower in username_to_id:
                target_id = username_to_id[target_lower]
                logger.info(f"Resolved {target} to ID {target_id} from cache")
            else:
                for _ in range(2):
                    try:
                        member = await context.bot.get_chat_member(chat_id, target_id if target_id else target)
                        target_id = member.user.id
                        target = f"@{member.user.username}" if member.user.username else f"@User{target_id}"
                        username_to_id[target_lower] = target_id
                        logger.info(f"Resolved {target} to ID {target_id} via get_chat_member and cached")
                        break
                    except telegram.error.TelegramError as e:
                        logger.error(f"get_chat_member retry failed for {target}: {str(e)}")
                        await asyncio.sleep(1)
            if not target_id:
                raise ValueError(f"Could not find user {target} in this chat - send a message first or use numeric ID")
        else:
            raise ValueError("Target must be @Username or numeric UserID")
        
        logger.info(f"Adding {amount} points to User {target_id} ({target}), current points: {user_points[target_id]}")
        user_points[target_id] += amount
        await update.message.reply_text(f"Pridėta {amount} taškų {target}! Dabar: {user_points[target_id]}")
        # Save data
        save_data(user_points, 'user_points.pkl')
    except (IndexError, ValueError, telegram.error.TelegramError) as e:
        logger.error(f"Error in addpoints: {str(e)}")
        await update.message.reply_text(f"Error: {str(e)}. Naudok: /addpoints Amount @Username")

# Remove points
async def removepoints(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali pašalinti taškus!")
        return
    try:
        amount = int(context.args[0])
        user_tag = context.args[1]
        user_id = int(user_tag.strip('@User'))
        if user_points[user_id] < amount:
            await update.message.reply_text(f"@User{user_id} neturi pakankamai taškų!")
            return
        user_points[user_id] -= amount
        await update.message.reply_text(f"Pašalinta {amount} taškų iš @User{user_id}! Dabar: {user_points[user_id]}")
        # Save data
        save_data(user_points, 'user_points.pkl')
    except (IndexError, ValueError):
        await update.message.reply_text("Naudok: /removepoints Amount @UserID")

# Check points
async def points(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    user_id = update.message.from_user.id
    streak = chat_streaks[user_id]
    alltime_msgs = alltime_messages[user_id]
    await update.message.reply_text(f"Jūsų taškai: {user_points[user_id]}\nSerija: {streak} dienų\nViso žinučių: {alltime_msgs}")

# Reset votes weekly
async def reset_votes(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    global votes_weekly, voters, downvoters, pending_downvotes, complaint_id
    votes_weekly.clear()
    voters.clear()
    downvoters.clear()
    pending_downvotes.clear()
    complaint_id = 0
    await context.bot.send_message(GROUP_CHAT_ID, "Nauja balsavimo savaitė! Use /balsuoju or /nepatiko.")
    logger.info("Votes and voters reset for new week")
    # Save data
    save_data(votes_weekly, 'votes_weekly.pkl')

# Add handlers
application.add_handler(CommandHandler(['startas'], startas))
application.add_handler(CommandHandler(['license'], activate_with_license))
application.add_handler(CommandHandler(['activate_group'], activate_group))
application.add_handler(CommandHandler(['balsuoju', 'vote'], balsuoju))
application.add_handler(CommandHandler(['nepatiko', 'dislike'], nepatiko))
application.add_handler(CommandHandler(['approve'], approve))
application.add_handler(CommandHandler(['reject'], reject))
application.add_handler(CommandHandler(['perziureti', 'reopen'], reopen))
application.add_handler(CommandHandler(['addseller'], addseller))
application.add_handler(CommandHandler(['removeseller'], removeseller))
application.add_handler(CommandHandler(['pardavejoinfo', 'sellerinfo'], sellerinfo))
application.add_handler(CommandHandler(['topvendoriai'], topvendoriai))
application.add_handler(CommandHandler(['balsu_istorija', 'votelog'], votelog))
application.add_handler(CommandHandler(['coinflip'], coinflip))
application.add_handler(CommandHandler(['accept_coinflip'], accept_coinflip))
application.add_handler(CommandHandler(['addpoints'], addpoints))
application.add_handler(CommandHandler(['removepoints'], removepoints))
application.add_handler(CommandHandler(['points', 'taskai'], points))
application.add_handler(CommandHandler(['debug'], debug))
application.add_handler(CommandHandler(['whoami'], whoami))
application.add_handler(CommandHandler(['chatking'], chatking))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Schedule daily points
application.job_queue.run_daily(award_daily_points, time=time(hour=0, minute=0))

# Schedule weekly recap (Sunday at 23:00)
application.job_queue.scheduler.add_job(
    weekly_recap,
    trigger=CronTrigger(day_of_week='sun', hour=23, minute=0, timezone=TIMEZONE),
    args=[application],
    id='weekly_recap'
)

# Schedule weekly reset (Monday at 00:00)
application.job_queue.scheduler.add_job(
    reset_votes,
    trigger=CronTrigger(day_of_week='mon', hour=0, minute=0, timezone=TIMEZONE),
    args=[application],
    id='reset_votes_weekly'
)

# Start the bot
application.run_polling()