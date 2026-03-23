import telebot
from telebot import types
import qrcode
import time
import threading
from datetime import datetime, timedelta
import logging
from io import BytesIO
import json
import os
import sys
import requests
import shlex
from flask import Flask

# Import config and verification
import config
from config import *
from verif import init_verification

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Initialize verification system
verif = init_verification(bot)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Auto-save thread
def auto_save_data():
    while True:
        time.sleep(30)
        save_all_data()

auto_save_thread = threading.Thread(target=auto_save_data, daemon=True)
auto_save_thread.start()

# Flask App for Railway Health Check
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

def initialize_spam_data():
    """Ensure all existing users have spam_data entries"""
    initialized = 0
    for user_id_str in users_data.keys():
        if user_id_str not in spam_data:
            spam_data[user_id_str] = {
                "requests": [],
                "warnings": 0,
                "blocked_until": 0,
                "block_level": 0,
                "ban_reason": "",
                "banned_by": 0
            }
            initialized += 1
    if initialized > 0:
        print(f"✅ Initialized spam data for {initialized} users")

# ========== FORCE JOIN REQUEST HANDLER ==========
@bot.chat_join_request_handler()
def handle_join_request(message):
    """Handle chat join requests and send confirmation message"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check if this is the set force join channel
    force_ch = settings.get('force_request_channel', '')
    if force_ch and str(chat_id) == str(force_ch):
        # 1. AUTO-ACCEPT (New Feature)
        if settings.get('auto_accept_requests', False):
            try:
                bot.approve_chat_join_request(chat_id, user_id)
                logging.info(f"Auto-accepted user {user_id} in {chat_id}")
                # Don't add to join_requests if auto-accepted
                msg_text = "<b>✅ Access Granted!</b>\n\nYour request has been <b>automatically accepted</b>. You can now use all the bot's features!"
                bot.send_message(user_id, msg_text, parse_mode="HTML")
                return
            except Exception as e:
                logging.error(f"Auto-accept failed for {user_id}: {e}")

        # 2. STANDARD FLOW (If auto-accept is off or failed)
        if user_id not in join_requests:
            join_requests.append(user_id)
            save_json_file(JOIN_REQUESTS_FILE, join_requests)
            
        msg_text = settings.get('force_request_msg', "<b>✅ Request Received!</b>")
        try:
            bot.send_message(user_id, msg_text, parse_mode="HTML")
            
            # Optionally alert admin in log channel
            log_ch = settings.get('log_channel', '')
            if log_ch:
                bot.send_message(log_ch, f"📩 <b>New Join Request</b>\n👤 User: {message.from_user.first_name}\n🆔 ID: <code>{user_id}</code>", parse_mode="HTML")
        except:
            pass

@bot.chat_member_handler()
def handle_chat_member_update(message):
    """Handle chat member status changes (join/leave)"""
    new_status = message.new_chat_member.status
    user_id = message.new_chat_member.user.id
    chat_id = message.chat.id
    
    # Check if this is our force join channel
    force_ch = settings.get('force_request_channel', '')
    if force_ch and str(chat_id) == str(force_ch):
        if new_status in ['member', 'administrator', 'creator']:
            # User joined! Remove from pending requests list
            if user_id in join_requests:
                join_requests.remove(user_id)
                save_json_file(JOIN_REQUESTS_FILE, join_requests)
                logging.info(f"User {user_id} joined channel, removed from join_requests.")
                
                # AUTO-WELCOME MESSAGE
                try:
                    welcome_text = """
<b>🎉 ACCESS GRANTED!</b>

Your join request has been accepted by our admin. You can now use all the bot's premium features!

👇 <b>Click /start to begin!</b>
                    """
                    bot.send_message(user_id, welcome_text, parse_mode="HTML")
                except:
                    pass

# ========== MEMBERSHIP CHECK ==========
def is_user_member(user_id):
    """Check if user joined or requested to join the required channel"""
    if not settings.get('force_join_status', True):
        return True
        
    force_ch = settings.get('force_request_channel', '')
    
    # 1. Check if user is in our join_requests list (sent request but not yet accepted)
    if user_id in join_requests:
        return True
        
    if not force_ch:
        # Fallback to old membership_channels check if new system not set
        channels = settings.get('membership_channels', [])
        if not channels:
            return True
            
        for channel_id in channels:
            try:
                member = bot.get_chat_member(channel_id, user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    return True
            except:
                continue
        return False
        
    # 2. Check if user is already a member/admin (request already accepted)
    try:
        member = bot.get_chat_member(force_ch, user_id)
        if member.status in ['member', 'administrator', 'creator']:
            # If they are already a member, make sure they aren't in the pending list anymore
            if user_id in join_requests:
                join_requests.remove(user_id)
                save_json_file(JOIN_REQUESTS_FILE, join_requests)
            return True
        
        return False
    except Exception as e:
        # If user never interacted with channel or bot isn't admin
        return False

def send_membership_message(chat_id):
    """Send message to join/request required channel"""
    force_ch = settings.get('force_request_channel', '')
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    if force_ch:
        try:
            chat = bot.get_chat(force_ch)
            title = chat.title or "Our Channel"
            # Get invite link that supports join requests
            url = chat.invite_link
            if not url and chat.username:
                url = f"https://t.me/{chat.username}"
                
            if url:
                markup.add(types.InlineKeyboardButton(f"� Request to Join {title}", url=url))
            else:
                markup.add(types.InlineKeyboardButton(f"📩 Request to Join (ID: {force_ch})", callback_data="req_join"))
        except:
            markup.add(types.InlineKeyboardButton("� Request to Join Channel", url=f"https://t.me/{str(force_ch).replace('-100', '')}"))
    else:
        # Fallback to old system
        channels = settings.get('membership_channels', [])
        for idx, cid in enumerate(channels, 1):
            markup.add(types.InlineKeyboardButton(f"📢 Join Channel {idx}", url=f"https://t.me/{str(cid).replace('-100', '')}"))

    markup.add(types.InlineKeyboardButton("🔄 Done / Refresh", callback_data="check_joined"))
    
    bot.send_message(
        chat_id,
        "<b>❌ ACCESS DENIED!</b>\n\nTo use this bot, you must send a <b>Join Request</b> to our main channel. Once you send the request, you can click the button below to start using the bot.",
        reply_markup=markup,
        parse_mode="HTML"
    )

# ============ SPAM PROTECTION FUNCTIONS ============
def update_user_activity(user_id):
    user_id_str = str(user_id)
    current_time = time.time()
    
    if user_id_str not in spam_data:
        spam_data[user_id_str] = {
            "requests": [],
            "warnings": 0,
            "blocked_until": 0,
            "block_level": 0,
            "ban_reason": "",
            "banned_by": 0
        }
    
    if "requests" not in spam_data[user_id_str]:
        spam_data[user_id_str]["requests"] = []
    
    spam_data[user_id_str]["requests"] = [
        ts for ts in spam_data[user_id_str]["requests"] 
        if current_time - ts < SPAM_TIME_WINDOW
    ]
    
    spam_data[user_id_str]["requests"].append(current_time)
    return len(spam_data[user_id_str]["requests"])

def check_user_blocked(user_id):
    user_id_str = str(user_id)
    
    if user_id_str not in spam_data:
        return False, None
    
    user_data = spam_data[user_id_str]
    
    if "blocked_until" not in user_data:
        user_data["blocked_until"] = 0
    
    current_time = time.time()
    
    if user_data["blocked_until"] > current_time:
        time_left = int(user_data["blocked_until"] - current_time)
        minutes = time_left // 60
        seconds = time_left % 60
        hours = minutes // 60
        minutes = minutes % 60
        
        warning_msg = f"⛔ <b>YOU ARE BLOCKED!</b>\n\n"
        
        if user_data.get("ban_reason"):
            warning_msg += f"<b>Reason:</b> {user_data['ban_reason']}\n"
        
        if hours > 0:
            warning_msg += f"⏳ Please wait <b>{hours} hours {minutes} minutes</b>\n\n"
        else:
            warning_msg += f"⏳ Please wait <b>{minutes}:{seconds:02d}</b>\n\n"
        
        return True, warning_msg
    
    return False, None

def check_spam(user_id):
    user_id_str = str(user_id)
    
    is_blocked, block_msg = check_user_blocked(user_id)
    if is_blocked:
        return block_msg
    
    current_time = time.time()
    request_count = update_user_activity(user_id)
    
    if "warnings" not in spam_data[user_id_str]:
        spam_data[user_id_str]["warnings"] = 0
    if "block_level" not in spam_data[user_id_str]:
        spam_data[user_id_str]["block_level"] = 0
    if "blocked_until" not in spam_data[user_id_str]:
        spam_data[user_id_str]["blocked_until"] = 0
    
    if request_count >= MAX_SPAM_COUNT:
        user_data = spam_data[user_id_str]
        user_data["block_level"] = min(2, user_data.get("block_level", 0) + 1)
        block_duration = BLOCK_DURATIONS[user_data["block_level"]]
        user_data["blocked_until"] = current_time + block_duration
        user_data["requests"] = []
        user_data["warnings"] = 0
        
        # Notify admin
        try:
            admin_msg = f"""
🚨 <b>USER BLOCKED FOR SPAM</b>

👤 User ID: <code>{user_id}</code>
📛 Block Level: {user_data['block_level'] + 1}
⏰ Duration: {block_duration//60} minutes
🔢 Spam Count: {request_count}
            """
            bot.send_message(ADMIN_ID, admin_msg, parse_mode="HTML")
        except:
            pass
        
        minutes = block_duration // 60
        seconds = block_duration % 60
        
        return f"⛔ <b>BLOCKED FOR SPAM!</b>\n\n⏳ Wait {minutes}:{seconds:02d}"
    
    if request_count >= 3:
        warning_level = min(2, request_count - 3)
        if spam_data[user_id_str].get("warnings", 0) < warning_level + 1:
            spam_data[user_id_str]["warnings"] = warning_level + 1
            warning_msg = f"{WARNING_MESSAGES[warning_level]}\n\n⚠️ {MAX_SPAM_COUNT - request_count} attempts left!"
            try:
                bot.send_message(user_id, warning_msg, parse_mode="HTML")
            except:
                pass
    
    return None

def reset_spam_counter(user_id):
    user_id_str = str(user_id)
    if user_id_str in spam_data:
        if spam_data[user_id_str].get("blocked_until", 0) < time.time():
            spam_data[user_id_str]["requests"] = []
            spam_data[user_id_str]["warnings"] = 0

def ban_user(user_id, duration_seconds, reason="", banned_by=ADMIN_ID):
    user_id_str = str(user_id)
    current_time = time.time()
    
    if user_id_str not in spam_data:
        spam_data[user_id_str] = {
            "requests": [],
            "warnings": 0,
            "blocked_until": 0,
            "block_level": 0,
            "ban_reason": reason,
            "banned_by": banned_by
        }
    
    spam_data[user_id_str]["blocked_until"] = current_time + duration_seconds
    spam_data[user_id_str]["ban_reason"] = reason
    spam_data[user_id_str]["banned_by"] = banned_by
    spam_data[user_id_str]["block_level"] = 3
    
    try:
        if duration_seconds >= 3600:
            time_display = f"{int(duration_seconds/3600)} hours"
        elif duration_seconds >= 60:
            time_display = f"{int(duration_seconds/60)} minutes"
        else:
            time_display = f"{duration_seconds} seconds"
        
        bot.send_message(
            int(user_id),
            f"⛔ <b>BANNED</b>\n\nDuration: {time_display}\nReason: {reason}",
            parse_mode="HTML"
        )
    except:
        pass
    
    return True

# ============ PREMIUM BOT CLASS ============
class PremiumBot:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def generate_qr_code(self, upi_id, amount, name):
        try:
            # Format amount to 2 decimal places for UPI standard
            formatted_amount = "{:.2f}".format(float(amount))
            upi_url = f"upi://pay?pa={upi_id}&pn={name.replace(' ', '%20')}&am={formatted_amount}&cu=INR"
            
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(upi_url)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            img_bytes = BytesIO()
            img.save(img_bytes, format='PNG')
            img_bytes.seek(0)
            return img_bytes
        except Exception as e:
            logging.error(f"QR Generation Error: {e}")
            return None

premium_bot = PremiumBot()

# ========== IMPORTANT LOGS ==========
def log_important_event(event_type, user_data=None, plan=None):
    try:
        if not settings.get('log_channel'):
            return
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if event_type == "new_user":
            log_msg = f"""
🆕 <b>NEW USER</b>
👤 Name: {user_data.get('first_name', 'N/A')}
👤 User: @{user_data.get('username' , 'N/A')}
🆔 ID: <code>{user_data.get('id', 'N/A')}</code>
⏰ Time: {timestamp}
📊 Total Users: {len(users_data)}
            """
        elif event_type == "payment_initiated":
            log_msg = f"""
💰 <b>PAYMENT INITIATED</b>
👤 Name: {user_data.get('first_name', 'N/A')}
👤 User: @{user_data.get('username', 'N/A')}
🆔 ID: <code>{user_data.get('id', 'N/A')}</code>
📅 Plan: {plan}
⏰ Time: {timestamp}
            """
        else:
            return
        
        target_chat = settings.get('log_channel')
        if not target_chat:
            target_chat = ADMIN_ID
            
        bot.send_message(target_chat, log_msg, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Log error: {e}")

# ========== /START COMMAND ==========
@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        user_id = message.from_user.id
        
        # 1. NEW: Check Force Join Request System FIRST
        if not is_user_member(user_id):
            send_membership_message(message.chat.id)
            return
            
        spam_result = check_spam(user_id)
        
        is_new_user = str(user_id) not in users_data
        
        users_data[str(user_id)] = {
            'id': user_id,
            'username': message.from_user.username,
            'first_name': message.from_user.first_name,
            'last_name': message.from_user.last_name or "",
            'start_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'is_premium': False
        }
        
        reset_spam_counter(user_id)
        
        if is_new_user:
            log_important_event("new_user", users_data[str(user_id)])
        
        # Check if custom start message exists
        if start_message_data and 'has_media' in start_message_data:
            text = start_message_data.get('text', "")
            
            if start_message_data['has_media']:
                media_type = start_message_data.get('media_type', '')
                file_id = start_message_data.get('file_id', '')
                
                if media_type == 'photo' and file_id:
                    bot.send_photo(
                        message.chat.id,
                        photo=file_id,
                        caption=text,
                        reply_markup=verif.main_menu_keyboard(),
                        parse_mode="HTML"
                    )
                elif media_type == 'video' and file_id:
                    bot.send_video(
                        message.chat.id,
                        video=file_id,
                        caption=text,
                        reply_markup=verif.main_menu_keyboard(),
                        parse_mode="HTML"
                    )
                else:
                    send_default_start(message)
            else:
                bot.send_message(
                    message.chat.id,
                    text,
                    reply_markup=verif.main_menu_keyboard(),
                    parse_mode="HTML"
                )
        else:
            send_default_start(message)
        
    except Exception as e:
        logging.error(f"Start error: {e}")

def send_default_start(message):
    welcome_text = f"""
🔥 <b>PREMIUM CONTENT</b> 🔥

Welcome to the Premium Bot! Access high-quality exclusive content.

👇 <b>Select an option:</b>
    """
    
    bot.send_message(
        message.chat.id,
        welcome_text,
        reply_markup=verif.main_menu_keyboard(),
        parse_mode="HTML"
    )

# ========== CHECK JOINED CALLBACK ==========
@bot.callback_query_handler(func=lambda call: call.data == "check_joined")
def handle_check_joined(call):
    user_id = call.from_user.id
    if is_user_member(user_id):
        bot.answer_callback_query(call.id, "✅ Thank you! Access granted.")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        # Trigger start command
        handle_start(call.message)
    else:
        bot.answer_callback_query(call.id, "❌ Request not found! Please click the 'Request to Join' button first.", show_alert=True)

# ========== GET MEMBERSHIP CALLBACK ==========
@bot.callback_query_handler(func=lambda call: call.data == "get_membership")
def handle_get_membership(call):
    user_id = call.from_user.id
    
    # Check membership
    if not is_user_member(user_id):
        send_membership_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
        
    try:
        bot.edit_message_text(
            "👇 <b>Choose a membership channel:</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=verif.plan_selection_keyboard(),
            parse_mode="HTML"
        )
    except:
        bot.send_message(
            call.message.chat.id,
            "👇 <b>Choose a membership channel:</b>",
            reply_markup=verif.plan_selection_keyboard(),
            parse_mode="HTML"
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "main_menu")
def handle_main_menu_callback(call):
    try:
        welcome_text = f"""
🔥 <b>PREMIUM CONTENT</b> 🔥

Welcome to the Premium Bot! Access high-quality exclusive content.

👇 <b>Select an option:</b>
        """
        bot.edit_message_text(
            welcome_text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=verif.main_menu_keyboard(),
            parse_mode="HTML"
        )
    except:
        handle_start(call.message)
    bot.answer_callback_query(call.id)

# ========== PLAN SELECTION ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith('plan_'))
def handle_plan_selection(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # Check membership
    if not is_user_member(user_id):
        send_membership_message(chat_id)
        bot.answer_callback_query(call.id)
        return
        
    spam_result = check_spam(user_id)
    if spam_result:
        bot.send_message(chat_id, spam_result, parse_mode="HTML")
        bot.answer_callback_query(call.id)
        return
    
    reset_spam_counter(user_id)
    
    plan_type = call.data.split('_')[1]  # monthly or lifetime
    plan = config.PLANS[plan_type]
    
    # Store in pending verifications
    pending_verifications[str(user_id)] = {
        'plan': plan_type,
        'amount': plan['amount'],
        'initiated_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'username': call.from_user.username,
        'first_name': call.from_user.first_name
    }
    save_json_file(PENDING_VERIF_FILE, pending_verifications)
    
    # Log payment initiation
    if str(user_id) in users_data:
        log_important_event("payment_initiated", users_data[str(user_id)], plan['name'])
    
    # Delete previous message
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass
    
    # Generate QR code
    qr_image = premium_bot.generate_qr_code(settings['upi_id'], plan['amount'], settings['upi_name'])
    
    if qr_image:
        caption = f"""
<b>💰 PAY ₹{plan['amount']} FOR {plan['name'].upper()}</b>

<b>UPI Details:</b>
└ ID: <code>{settings['upi_id']}</code>
└ Name: {settings['upi_name']}
└ Amount: <b>₹{plan['amount']}</b>

<b>Instructions:</b>
1. Scan QR with any UPI app
2. Pay ₹{plan['amount']}
3. Click "✅ Payment Done" below
        """
        
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        btn1 = types.InlineKeyboardButton("✅ Payment Done", callback_data="payment_done")
        btn2 = types.InlineKeyboardButton("📞 Support", url=f"https://t.me/{settings['support_username']}")
        keyboard.add(btn1, btn2)
        
        bot.send_photo(
            chat_id,
            photo=qr_image,
            caption=caption,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        manual_text = f"""
<b>💰 PAY ₹{plan['amount']} FOR {plan['name'].upper()}</b>

<b>UPI ID:</b> <code>{settings['upi_id']}</code>
<b>Amount:</b> ₹{plan['amount']}

<b>Steps:</b>
1. Send ₹{plan['amount']} to above UPI ID
2. Click "✅ Payment Done" below
        """
        
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        btn1 = types.InlineKeyboardButton("✅ Payment Done", callback_data="payment_done")
        btn2 = types.InlineKeyboardButton("📞 Support", url=f"https://t.me/{settings['support_username']}")
        keyboard.add(btn1, btn2)
        
        bot.send_message(
            chat_id,
            manual_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    bot.answer_callback_query(call.id)

# ========== HOW TO GET ==========
@bot.callback_query_handler(func=lambda call: call.data == "how_to_get")
def handle_how_to_get(call):
    user_id = call.from_user.id
    
    # Check membership
    if not is_user_member(user_id):
        send_membership_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
        
    spam_result = check_spam(user_id)
    if spam_result:
        bot.send_message(call.message.chat.id, spam_result, parse_mode="HTML")
        bot.answer_callback_query(call.id)
        return
    
    reset_spam_counter(user_id)
    
    instructions = f"""
<b>❓ HOW TO BUY PREMIUM:</b>

1. Choose your plan from menu
2. Scan QR code and pay exact amount
3. Click "Payment Done" button
4. Send payment screenshot
5. Admin verifies within few minutes
6. Get unique join link after verification

<b>Support:</b> @{settings.get('support_username', 'N/A')}
    """
    
    try:
        bot.edit_message_text(
            instructions,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=verif.plan_selection_keyboard(),
            parse_mode="HTML"
        )
    except:
        bot.send_message(
            call.message.chat.id,
            instructions,
            reply_markup=verif.plan_selection_keyboard(),
            parse_mode="HTML"
        )
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data in ["demo_not_set", "support_not_set", "proof_not_set"])
def handle_not_set_alerts(call):
    text = "This is not configured by admin yet."
    if call.data == "support_not_set":
        text = "Support username is not configured yet."
    elif call.data == "proof_not_set":
        text = "Payment proof channel link is not configured yet."
    bot.answer_callback_query(call.id, text, show_alert=True)

# ========== GET PREMIUM ==========
@bot.callback_query_handler(func=lambda call: call.data == "get_premium")
def handle_get_premium(call):
    user_id = call.from_user.id
    
    # Check membership
    if not is_user_member(user_id):
        send_membership_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return
        
    spam_result = check_spam(user_id)
    if spam_result:
        bot.send_message(call.message.chat.id, spam_result, parse_mode="HTML")
        bot.answer_callback_query(call.id)
        return
    
    reset_spam_counter(user_id)
    
    try:
        bot.edit_message_text(
            "👇 <b>Choose your membership plan:</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=verif.plan_selection_keyboard(),
            parse_mode="HTML"
        )
    except:
        bot.send_message(
            call.message.chat.id,
            "👇 <b>Choose your membership plan:</b>",
            reply_markup=verif.plan_selection_keyboard(),
            parse_mode="HTML"
        )
    
    bot.answer_callback_query(call.id)

# ========== PAYMENT DONE ==========
@bot.callback_query_handler(func=lambda call: call.data == "payment_done")
def handle_payment_done(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    spam_result = check_spam(user_id)
    if spam_result:
        bot.send_message(chat_id, spam_result, parse_mode="HTML")
        bot.answer_callback_query(call.id)
        return
    
    reset_spam_counter(user_id)
    
    # Check if user has selected a plan
    if str(user_id) not in pending_verifications:
        bot.answer_callback_query(
            call.id, 
            "Please select a plan first!", 
            show_alert=True
        )
        return
    
    # Delete previous message
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass
    
    # Ask for screenshot
    verif.ask_for_screenshot(chat_id, user_id, pending_verifications[str(user_id)]['plan'])
    
    bot.answer_callback_query(call.id)

# ========== HANDLE SCREENSHOTS ==========
@bot.message_handler(content_types=['photo'])
def handle_photos(message):
    # First check if this is a payment screenshot
    if verif.handle_screenshot(message):
        return
    
    # If not payment screenshot, ignore silently
    pass

# ========== VERIFICATION CALLBACKS ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith('verify_'))
def handle_verify(call):
    if str(call.from_user.id) != ADMIN_ID:
        bot.answer_callback_query(call.id, "Admin only!")
        return
    
    user_id = call.data.split('_')[1]
    
    success, msg = verif.verify_payment(user_id, call.from_user.id)
    
    if success:
        bot.answer_callback_query(call.id, "✅ Payment verified! Unique join link sent to user.")
        
        # Update the admin message
        try:
            bot.edit_message_caption(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                caption=call.message.caption + "\n\n✅ <b>VERIFIED - UNIQUE LINK SENT</b>",
                parse_mode="HTML"
            )
        except:
            pass
    else:
        bot.answer_callback_query(call.id, f"❌ Error: {msg}", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_'))
def handle_reject(call):
    if str(call.from_user.id) != ADMIN_ID:
        bot.answer_callback_query(call.id, "Admin only!")
        return
    
    user_id = call.data.split('_')[1]
    
    success, msg = verif.reject_payment(user_id, call.from_user.id)
    
    if success:
        bot.answer_callback_query(call.id, "❌ Payment rejected. User notified.")
        
        # Update the admin message
        try:
            bot.edit_message_caption(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                caption=call.message.caption + "\n\n❌ <b>REJECTED</b>",
                parse_mode="HTML"
            )
        except:
            pass
    else:
        bot.answer_callback_query(call.id, f"❌ Error: {msg}", show_alert=True)

# ========== /VERIFY COMMAND ==========
@bot.message_handler(commands=['verify'])
def handle_manual_verify(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(
            message,
            "Usage: /verify [user_id]\nExample: /verify 123456789"
        )
        return
    
    user_id = args[1]
    
    if user_id not in pending_verifications:
        bot.reply_to(message, "❌ User not in pending verifications")
        return
    
    success, msg = verif.verify_payment(user_id, message.from_user.id)
    bot.reply_to(message, msg)

# ========== /SETTINGS COMMAND (FIXED HTML) ==========
@bot.message_handler(commands=['settings'])
def handle_settings(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    # Premium Channels List
    ch_info = ""
    for ch in settings.get('premium_channels', []):
        ch_info += f"• {ch['id']}: {ch['name']} (₹{ch['amount']}) - <code>{ch['channel_id']}</code>\n"
    
    text = f"""
<b>⚙️ CURRENT SETTINGS</b>

<b>📢 Demo Link:</b> {settings.get('demo_channel_link', 'Not Set')}
<b>🆔 Demo ID:</b> <code>{settings.get('demo_channel_id', 'Not Set')}</code>
<b>💰 Demo Price:</b> ₹{settings.get('demo_amount', '10')}
<b>🔄 Demo Status:</b> {'PAID' if settings.get('demo_paid_status', False) else 'FREE'}

<b>📞 Support:</b> @{settings.get('support_username', 'Not Set')}
<b>📋 Log Channel:</b> {settings.get('log_channel', 'Not Set')}
<b>🛡️ Force Join:</b> {'ON' if settings.get('force_join_status', True) else 'OFF'}
<b>🤖 Auto-Accept:</b> {'ON' if settings.get('auto_accept_requests', False) else 'OFF'}
<b>❓ Buy Guide:</b> {settings.get('how_to_buy_url', 'Not Set')}
<b>🧾 Proof Channel:</b> {settings.get('payment_proof_link', 'Not Set')}
<b>🧾 Proof Status:</b> {'ON' if settings.get('payment_proof_status', False) else 'OFF'}

<b>💰 UPI Settings:</b>
• UPI ID: <code>{settings.get('upi_id', 'Not Set')}</code>
• Name: {settings.get('upi_name', 'Not Set')}

<b>📺 Premium Channels:</b>
{ch_info}
<b>To change settings, use specific commands in /help</b>
    """
    
    bot.reply_to(message, text, parse_mode="HTML")

# ========== /SET COMMAND (FIXED HTML) ==========
@bot.message_handler(commands=['set'])
def handle_set(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        bot.reply_to(message, "Usage: /set [key] [value]\nExample: /set monthly_amount 129")
        return
    
    key = args[1].lower()
    value = args[2]
    
    # Map keys to settings
    key_map = {
        "demo_channel": "demo_channel_link",
        "support": "support_username",
        "log_channel": "log_channel",
        "upi_id": "upi_id",
        "upi_name": "upi_name",
        "monthly_name": "monthly_name",
        "monthly_amount": "monthly_amount",
        "monthly_channel": "monthly_channel_id",
        "lifetime_name": "lifetime_name",
        "lifetime_amount": "lifetime_amount",
        "lifetime_channel": "lifetime_channel_id"
    }
    
    if key not in key_map:
        bot.reply_to(message, f"❌ Invalid key. Available: {', '.join(key_map.keys())}")
        return
    
    settings[key_map[key]] = value
    save_settings()
    
    bot.reply_to(message, f"✅ Updated {key} to: {value}")

@bot.message_handler(commands=['ban'])
def handle_ban(message):
    bot.reply_to(message, "❌ Ban system has been removed from this bot.")

@bot.message_handler(commands=['unban'])
def handle_unban(message):
    bot.reply_to(message, "❌ Ban system has been removed from this bot.")

@bot.message_handler(commands=['banlist'])
def handle_banlist(message):
    bot.reply_to(message, "❌ Ban system has been removed from this bot.")

# ========== /BROADCAST COMMAND ==========
@bot.message_handler(commands=['broadcast'])
def handle_broadcast(message):
    """Broadcast message to all users"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    if not message.reply_to_message:
        help_text = """
<b>📢 BROADCAST COMMAND</b>

<code>Reply to any message with /broadcast</code>

<b>Supported:</b> Text, Photos, Videos, Documents, GIFs

<b>How to use:</b>
1. Send the message you want to broadcast
2. Reply to it with <code>/broadcast</code>
        """
        bot.reply_to(message, help_text, parse_mode="HTML")
        return
    
    replied_msg = message.reply_to_message
    progress_msg = bot.reply_to(message, "📤 <b>Broadcast Starting...</b>", parse_mode="HTML")
    
    total_users = len(users_data)
    if total_users == 0:
        bot.edit_message_text("❌ No users to broadcast", chat_id=message.chat.id, message_id=progress_msg.message_id)
        return
    
    def broadcast_thread():
        sent = 0
        failed = 0
        skipped = 0
        user_ids = list(users_data.keys())
        
        for idx, user_id_str in enumerate(user_ids):
            try:
                user_id = int(user_id_str)
                
                # Skip if blocked
                if user_id_str in spam_data:
                    if spam_data[user_id_str].get("blocked_until", 0) > time.time():
                        skipped += 1
                        continue
                
                # Send based on type
                if replied_msg.photo:
                    bot.send_photo(
                        user_id, 
                        photo=replied_msg.photo[-1].file_id, 
                        caption=replied_msg.caption or "", 
                        parse_mode="HTML"
                    )
                elif replied_msg.video:
                    bot.send_video(
                        user_id, 
                        video=replied_msg.video.file_id, 
                        caption=replied_msg.caption or "", 
                        parse_mode="HTML"
                    )
                elif replied_msg.document:
                    bot.send_document(
                        user_id, 
                        document=replied_msg.document.file_id, 
                        caption=replied_msg.caption or "", 
                        parse_mode="HTML"
                    )
                elif replied_msg.animation:
                    bot.send_animation(
                        user_id, 
                        animation=replied_msg.animation.file_id, 
                        caption=replied_msg.caption or "", 
                        parse_mode="HTML"
                    )
                elif replied_msg.text:
                    bot.send_message(user_id, replied_msg.text, parse_mode="HTML")
                elif replied_msg.caption:
                    bot.send_message(user_id, replied_msg.caption, parse_mode="HTML")
                
                sent += 1
                
                # Update progress every 10 users
                if idx % 10 == 0:
                    percent = int((idx + 1) / total_users * 100)
                    try:
                        bot.edit_message_text(
                            f"📤 Broadcasting... {percent}% ({sent} sent, {failed} failed)", 
                            chat_id=message.chat.id, 
                            message_id=progress_msg.message_id
                        )
                    except:
                        pass
                
                time.sleep(0.1)  # Rate limit protection
                
            except Exception as e:
                failed += 1
        
        final_text = f"""
✅ <b>BROADCAST COMPLETE!</b>

📊 <b>Results:</b>
• ✅ Sent: {sent}
• ❌ Failed: {failed}
• ⏭️ Skipped: {skipped}
• 👥 Total: {total_users}
        """
        
        try:
            bot.edit_message_text(
                final_text, 
                chat_id=message.chat.id, 
                message_id=progress_msg.message_id, 
                parse_mode="HTML"
            )
        except:
            pass
    
    thread = threading.Thread(target=broadcast_thread)
    thread.start()
    
    bot.reply_to(message, f"📢 Broadcast started to {total_users} users!")

# ========== /STATS COMMAND ==========
@bot.message_handler(commands=['stats'])
def handle_stats(message):
    """Show bot statistics"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    current_time = time.time()
    blocked_users = sum(1 for u in spam_data.values() if u.get("blocked_until", 0) > current_time)
    pending_count = len(pending_verifications)
    
    today = datetime.now().strftime('%Y-%m-%d')
    new_today = sum(1 for u in users_data.values() if u.get('start_time', '').startswith(today))
    
    # Count premium users
    premium_users = sum(1 for u in users_data.values() if u.get('is_premium', False))
    
    # Dynamic Pricing Info
    pricing_info = ""
    for ch in settings.get('premium_channels', []):
        pricing_info += f"• {ch['name']}: ₹{ch['amount']}\n"
    if not pricing_info:
        pricing_info = "• No channels configured\n"
        
    stats_text = f"""
<b>📊 BOT STATISTICS</b>

👥 <b>Users:</b>
• Total Users: {len(users_data)}
• Premium Users: {premium_users}
• New Today: {new_today}
• Pending Verification: {pending_count}

🛡️ <b>Spam Protection:</b>
• Currently Blocked: {blocked_users}
• Tracked Users: {len(spam_data)}

📩 <b>Join Requests:</b>
• Pending Tracked: {len(join_requests)}

💰 <b>Pricing Info:</b>
{pricing_info}• Demo: ₹{settings.get('demo_amount', '10')} ({'PAID' if settings.get('demo_paid_status') else 'FREE'})

📁 <b>Storage:</b>
• Data Files: {len(os.listdir(DATA_DIR))}

🚀 <b>Status:</b> ✅ Running
    """
    bot.reply_to(message, stats_text, parse_mode="HTML")

# ========== ADMIN COMMANDS (NEW) ==========
@bot.message_handler(commands=['add_channel'])
def handle_add_channel(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: <code>/add_channel channel_id</code>", parse_mode="HTML")
        return
        
    channel_id = args[1]
    if channel_id not in settings.get('membership_channels', []):
        if 'membership_channels' not in settings:
            settings['membership_channels'] = []
        settings['membership_channels'].append(channel_id)
        save_settings()
        bot.reply_to(message, f"✅ Added {channel_id} to membership channels.")
    else:
        bot.reply_to(message, "Channel already in list.")

@bot.message_handler(commands=['remove_channel'])
def handle_remove_channel(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: <code>/remove_channel channel_id</code>", parse_mode="HTML")
        return
        
    channel_id = args[1]
    if channel_id in settings.get('membership_channels', []):
        settings['membership_channels'].remove(channel_id)
        save_settings()
        bot.reply_to(message, f"✅ Removed {channel_id} from membership channels.")
    else:
        bot.reply_to(message, "Channel not found in list.")

@bot.message_handler(commands=['channels'])
def handle_channels(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    channels = settings.get('membership_channels', [])
    if not channels:
        bot.reply_to(message, "No membership channels set.")
        return
        
    text = "<b>📢 MEMBERSHIP CHANNELS:</b>\n\n"
    for idx, cid in enumerate(channels, 1):
        text += f"{idx}. <code>{cid}</code>\n"
        
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['demo_toggle'])
def handle_demo_toggle(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    current_status = settings.get('demo_paid_status', False)
    new_status = not current_status
    settings['demo_paid_status'] = new_status
    save_settings()
    
    status_text = "PAID" if new_status else "FREE"
    bot.reply_to(message, f"✅ Demo is now <b>{status_text}</b>.", parse_mode="HTML")

@bot.message_handler(commands=['demo_price'])
def handle_demo_price(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: <code>/demo_price amount</code>", parse_mode="HTML")
        return
        
    amount = args[1]
    settings['demo_amount'] = amount
    save_settings()
    bot.reply_to(message, f"✅ Demo price set to <b>₹{amount}</b>.", parse_mode="HTML")

@bot.message_handler(commands=['set_demo_ch'])
def handle_set_demo_ch(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: <code>/set_demo_ch channel_id</code>", parse_mode="HTML")
        return
        
    ch_id = args[1]
    settings['demo_channel_id'] = ch_id
    save_settings()
    bot.reply_to(message, f"✅ Demo Channel ID set to: <code>{ch_id}</code>", parse_mode="HTML")

@bot.message_handler(commands=['set_demo_link'])
def handle_set_demo_link(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: <code>/set_demo_link https://t.me/xxx</code>", parse_mode="HTML")
        return
        
    url = args[1]
    settings['demo_channel_link'] = url
    save_settings()
    bot.reply_to(message, f"✅ Demo Channel Link set to: {url}")

@bot.message_handler(commands=['support_toggle'])
def handle_support_toggle(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    current = settings.get("support_button_status", True)
    settings["support_button_status"] = not current
    save_settings()
    
    status = "ON" if settings["support_button_status"] else "OFF"
    bot.reply_to(message, f"✅ Support button is now <b>{status}</b>.", parse_mode="HTML")

@bot.message_handler(commands=['proof_toggle'])
def handle_proof_toggle(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    current = settings.get("payment_proof_status", False)
    settings["payment_proof_status"] = not current
    save_settings()
    
    status = "ON" if settings["payment_proof_status"] else "OFF"
    bot.reply_to(message, f"✅ Payment Proof button is now <b>{status}</b>.", parse_mode="HTML")

@bot.message_handler(commands=['force_join_toggle'])
def handle_force_join_toggle(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    current = settings.get("force_join_status", True)
    settings["force_join_status"] = not current
    save_settings()
    
    status = "ON" if settings["force_join_status"] else "OFF"
    bot.reply_to(message, f"✅ Force Join system is now <b>{status}</b>.", parse_mode="HTML")

@bot.message_handler(commands=['set_proof_link'])
def handle_set_proof_link(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: <code>/set_proof_link [url]</code>", parse_mode="HTML")
        return
        
    url = args[1]
    settings['payment_proof_link'] = url
    save_settings()
    bot.reply_to(message, f"✅ Payment Proof Link set to: {url}", parse_mode="HTML")

@bot.message_handler(commands=['set_buy_url'])
def handle_set_buy_url(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: <code>/set_buy_url [url]</code>", parse_mode="HTML")
        return
        
    url = args[1]
    settings['how_to_buy_url'] = url
    save_settings()
    bot.reply_to(message, f"✅ How To Buy Tutorial URL set to: {url}", parse_mode="HTML")

@bot.message_handler(commands=['add_premium_ch'])
def handle_add_premium_ch(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    try:
        # Split by spaces
        args = message.text.split()
        if len(args) < 5:
            bot.reply_to(message, "Usage: <code>/add_premium_ch id Full Name price channel_id</code>\nExample: <code>/add_premium_ch ch1 Randi Ki Dukan 99 -100xxx</code>", parse_mode="HTML")
            return
            
        # ID is always 2nd element
        ch_id = args[1]
        
        # Last two elements are always price and channel_id
        telegram_id = args[-1]
        price = args[-2]
        
        # Everything in between is the name
        name = " ".join(args[2:-2])
        
        if 'premium_channels' not in settings:
            settings['premium_channels'] = []
            
        # Check if id already exists
        for ch in settings['premium_channels']:
            if ch['id'] == ch_id:
                bot.reply_to(message, f"❌ ID {ch_id} already exists.")
                return
                
        settings['premium_channels'].append({
            "id": ch_id,
            "name": name,
            "amount": price,
            "channel_id": telegram_id,
            "duration": "30 Days"
        })
        save_settings()
        bot.reply_to(message, f"✅ Added <b>{name}</b> (₹{price}) to membership list.", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['remove_premium_ch'])
def handle_remove_premium_ch(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: <code>/remove_premium_ch id</code>", parse_mode="HTML")
        return
        
    ch_id = args[1]
    found = False
    for i, ch in enumerate(settings.get('premium_channels', [])):
        if ch['id'] == ch_id:
            settings['premium_channels'].pop(i)
            found = True
            break
            
    if found:
        save_settings()
        bot.reply_to(message, f"✅ Removed channel {ch_id}.")
    else:
        bot.reply_to(message, f"❌ Channel ID {ch_id} not found.")

@bot.message_handler(commands=['edit_premium_ch'])
def handle_edit_premium_ch(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    try:
        args = message.text.split()
        if len(args) < 4:
            bot.reply_to(message, "Usage: <code>/edit_premium_ch id key New Value</code>\nKeys: name, amount, channel_id", parse_mode="HTML")
            return
            
        ch_id = args[1]
        key = args[2]
        
        # Everything after key is the new value
        value = " ".join(args[3:])
        
        found = False
        for ch in settings.get('premium_channels', []):
            if ch['id'] == ch_id:
                if key in ch:
                    ch[key] = value
                    found = True
                    break
                    
        if found:
            save_settings()
            bot.reply_to(message, f"✅ Updated <b>{key}</b> for <b>{ch_id}</b> to: <code>{value}</code>", parse_mode="HTML")
        else:
            bot.reply_to(message, f"❌ Channel ID {ch_id} not found.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['set_price'])
def handle_set_price(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Usage: <code>/set_price single/all amount</code>", parse_mode="HTML")
        return
        
    type_ = args[1].lower()
    amount = args[2]
    
    if type_ == "single":
        settings['ch_price'] = amount
        bot.reply_to(message, f"✅ Single channel price set to <b>₹{amount}</b>.", parse_mode="HTML")
    elif type_ == "all":
        settings['all_price'] = amount
        bot.reply_to(message, f"✅ All channels price set to <b>₹{amount}</b>.", parse_mode="HTML")
    else:
        bot.reply_to(message, "Invalid type. Use 'single' or 'all'.")
        return
        
    save_settings()

@bot.message_handler(commands=['set_ch'])
def handle_set_ch(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Usage: <code>/set_ch 1-7 channel_id</code>", parse_mode="HTML")
        return
        
    ch_num = args[1]
    ch_id = args[2]
    
    if ch_num not in [str(i) for i in range(1, 8)]:
        bot.reply_to(message, "Invalid channel number. Use 1-7.")
        return
        
    settings[f'ch{ch_num}_id'] = ch_id
    save_settings()
    bot.reply_to(message, f"✅ Channel {ch_num} ID set to <code>{ch_id}</code>.", parse_mode="HTML")

@bot.message_handler(commands=['imp_to_mongo'])
def handle_imp_to_mongo(message):
    """Import data from a JSON file directly to MongoDB via reply with merging"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    if not message.reply_to_message or not message.reply_to_message.document:
        bot.reply_to(message, "❌ <b>Usage:</b> Reply to a JSON file with <code>/imp_to_mongo</code>", parse_mode="HTML")
        return
    
    try:
        status_msg = bot.reply_to(message, "⏳ <b>Processing file...</b>", parse_mode="HTML")
        
        file_info = bot.get_file(message.reply_to_message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Parse JSON
        imported_data = json.loads(downloaded_file.decode('utf-8'))
        filename = message.reply_to_message.document.file_name.lower()
        
        collection_name = ""
        # Determine collection name based on filename OR content
        if "user" in filename or (isinstance(imported_data, dict) and any(k.isdigit() for k in list(imported_data.keys())[:5]) and "username" in str(list(imported_data.values())[0])):
            collection_name = "users_data"
        elif "setting" in filename or (isinstance(imported_data, dict) and "upi_id" in imported_data):
            collection_name = "settings"
        elif "spam" in filename or (isinstance(imported_data, dict) and any(k.isdigit() for k in list(imported_data.keys())[:5]) and "spam_count" in str(list(imported_data.values())[0])):
            collection_name = "spam_data"
        elif "request" in filename or (isinstance(imported_data, list) and all(isinstance(i, int) for i in imported_data[:5])):
            collection_name = "join_requests"
        elif "pending" in filename or (isinstance(imported_data, dict) and any(k.isdigit() for k in list(imported_data.keys())[:5]) and "screenshot_file_id" in str(list(imported_data.values())[0])):
            collection_name = "pending_verifications"
        elif "link" in filename or (isinstance(imported_data, dict) and any(k.isdigit() for k in list(imported_data.keys())[:5]) and isinstance(list(imported_data.values())[0], (list, str))):
            collection_name = "invite_links"
        elif "start" in filename or (isinstance(imported_data, dict) and "custom_text" in imported_data):
            collection_name = "start_message"

        # 1. Handle Full Export File
        if not collection_name and "users" in imported_data and isinstance(imported_data["users"], dict):
            merged_count = 0
            for key in ["users", "spam_data", "pending", "settings", "join_requests"]:
                val = imported_data.get(key)
                if val:
                    col_map = {"users": "users_data", "pending": "pending_verifications"}
                    target_col = col_map.get(key, key)
                    
                    # Merge Logic
                    current_db_data = db_load(target_col, {} if isinstance(val, dict) else [])
                    if isinstance(val, dict):
                        current_db_data.update(val)
                    elif isinstance(val, list):
                        current_db_data = list(set(current_db_data + val))
                    
                    db_save(target_col, current_db_data)
                    merged_count += 1
            
            bot.edit_message_text(f"✅ <b>Full Export Merged!</b>\nMerged {merged_count} modules into MongoDB successfully.", chat_id=message.chat.id, message_id=status_msg.message_id, parse_mode="HTML")
            return

        # 2. Handle Single Module File
        if not collection_name:
            bot.edit_message_text("❌ <b>Error:</b> Could not determine data type from filename. Rename file to <code>users_data.json</code> etc.", chat_id=message.chat.id, message_id=status_msg.message_id, parse_mode="HTML")
            return

        # Merge Logic for Single File
        current_db_data = db_load(collection_name, {} if isinstance(imported_data, dict) else [])
        item_count = 0
        
        if isinstance(imported_data, dict):
            current_db_data.update(imported_data)
            item_count = len(imported_data)
            # Special handling for globals
            if collection_name == "settings":
                global settings
                settings.update(imported_data)
            elif collection_name == "users_data":
                global users_data
                users_data.update(imported_data)
        elif isinstance(imported_data, list):
            current_db_data = list(set(current_db_data + imported_data))
            item_count = len(imported_data)
            if collection_name == "join_requests":
                global join_requests
                join_requests = current_db_data

        if db_save(collection_name, current_db_data):
            bot.edit_message_text(f"✅ <b>Import & Merge Successful!</b>\n<b>Collection:</b> <code>{collection_name}</code>\n<b>New Items Merged:</b> {item_count}", chat_id=message.chat.id, message_id=status_msg.message_id, parse_mode="HTML")
        else:
            bot.edit_message_text("❌ <b>MongoDB Merge Failed!</b>", chat_id=message.chat.id, message_id=status_msg.message_id, parse_mode="HTML")
            
    except Exception as e:
        bot.edit_message_text(f"❌ <b>Error:</b> {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id, parse_mode="HTML")

@bot.message_handler(commands=['migrate_to_mongo'])
def handle_migrate_to_mongo(message):
    """Manually migrate all local JSON data to MongoDB"""
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    msg = bot.reply_to(message, "⏳ <b>Migration started...</b>", parse_mode="HTML")
    
    success, result = force_migrate_to_mongodb()
    
    if success:
        files_str = ", ".join(result) if result else "None"
        bot.edit_message_text(
            f"✅ <b>Migration Successful!</b>\n\n<b>Migrated:</b> {files_str}\n\nData is now synced with MongoDB.",
            chat_id=message.chat.id,
            message_id=msg.message_id,
            parse_mode="HTML"
        )
    else:
        bot.edit_message_text(
            f"❌ <b>Migration Failed:</b> {result}",
            chat_id=message.chat.id,
            message_id=msg.message_id,
            parse_mode="HTML"
        )

@bot.message_handler(commands=['auto_accept'])
def handle_auto_accept_toggle(message):
    """Toggle auto-accept of join requests"""
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    current = settings.get('auto_accept_requests', False)
    settings['auto_accept_requests'] = not current
    save_settings()
    
    status = "ON (Auto-Accepting)" if settings['auto_accept_requests'] else "OFF (Manual Approval)"
    bot.reply_to(message, f"✅ Auto-accept join requests is now <b>{status}</b>.", parse_mode="HTML")

@bot.message_handler(commands=['approve_all'])
def handle_approve_all_requests(message):
    """Approve all currently pending join requests in the list"""
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    force_ch = settings.get('force_request_channel', '')
    if not force_ch:
        bot.reply_to(message, "❌ Force join channel not set. Use <code>/set_force_ch</code> first.", parse_mode="HTML")
        return
        
    if not join_requests:
        bot.reply_to(message, "✅ No pending join requests to approve.")
        return
        
    status_msg = bot.reply_to(message, f"⏳ <b>Approving {len(join_requests)} requests...</b>", parse_mode="HTML")
    
    success_count = 0
    failed_count = 0
    
    # We iterate a copy to avoid modification issues
    pending_list = list(join_requests)
    for uid in pending_list:
        try:
            bot.approve_chat_join_request(force_ch, uid)
            join_requests.remove(uid)
            success_count += 1
            # Optional: Notify user
            try: bot.send_message(uid, "✅ <b>Access Granted!</b>\nYour request has been accepted. You can now use the bot.", parse_mode="HTML")
            except: pass
        except Exception as e:
            logging.error(f"Approve failed for {uid}: {e}")
            failed_count += 1
            
    save_json_file(JOIN_REQUESTS_FILE, join_requests)
    
    bot.edit_message_text(
        f"✅ <b>Process Complete!</b>\n\n<b>Success:</b> {success_count}\n<b>Failed:</b> {failed_count}",
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        parse_mode="HTML"
    )

@bot.message_handler(commands=['set_force_ch'])
def handle_set_force_ch(message):
    """Set the channel for force join request"""
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: <code>/set_force_ch channel_id</code>", parse_mode="HTML")
        return
        
    ch_id = args[1]
    settings['force_request_channel'] = ch_id
    save_settings()
    bot.reply_to(message, f"✅ Force join channel set to: <code>{ch_id}</code>", parse_mode="HTML")

@bot.message_handler(commands=['set_force_msg'])
def handle_set_force_msg(message):
    """Set the message sent after join request"""
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    if not message.reply_to_message:
        # If not reply, use text from command
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            bot.reply_to(message, "Usage: Reply to a message with /set_force_msg OR use <code>/set_force_msg Your Message</code>", parse_mode="HTML")
            return
        new_msg = args[1]
    else:
        new_msg = message.reply_to_message.text or message.reply_to_message.caption or ""
        
    settings['force_request_msg'] = new_msg
    save_settings()
    bot.reply_to(message, "✅ Force join request message updated!")

# ========== /EXPORTDATA COMMAND ==========
@bot.message_handler(commands=['exportdata'])
def handle_export_data(message):
    """Export all data as JSON"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    try:
        status_msg = bot.reply_to(message, "📥 Preparing export...", parse_mode="HTML")
        
        export_data = {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_users": len(users_data),
            "users": users_data,
            "spam_data": spam_data,
            "pending": pending_verifications
        }
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"export_{timestamp}.json"
        filepath = os.path.join(DATA_DIR, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=4)
        
        with open(filepath, 'rb') as f:
            bot.send_document(
                message.chat.id,
                f,
                caption=f"📊 Export: {len(users_data)} users\n⏰ {timestamp}"
            )
        
        bot.delete_message(message.chat.id, status_msg.message_id)
        
    except Exception as e:
        bot.reply_to(message, f"❌ Export failed: {str(e)}")

# ========== /IMPDATA COMMAND ==========
@bot.message_handler(commands=['impdata'])
def handle_impdata(message):
    """Import data from JSON file"""
    if str(message.from_user.id) != ADMIN_ID:
        bot.reply_to(message, "⛔ Admin access required!")
        return
    
    if not message.reply_to_message or not message.reply_to_message.document:
        bot.reply_to(message, "❌ Reply to a JSON file with /impdata")
        return
    
    try:
        status_msg = bot.reply_to(message, "📥 Downloading file...", parse_mode="HTML")
        
        file_info = bot.get_file(message.reply_to_message.document.file_id)
        file_name = message.reply_to_message.document.file_name
        
        if not file_name.lower().endswith('.json'):
            bot.edit_message_text("❌ File must be JSON", chat_id=message.chat.id, message_id=status_msg.message_id)
            return
        
        downloaded_file = bot.download_file(file_info.file_path)
        
        temp_path = f"/tmp/{file_name}"
        with open(temp_path, 'wb') as f:
            f.write(downloaded_file)
        
        with open(temp_path, 'r', encoding='utf-8') as f:
            imported_data = json.load(f)
        
        users_before = len(users_data)
        imported_count = 0
        updated_count = 0
        
        # Handle different formats
        if "users" in imported_data:
            data_to_import = imported_data["users"]
        else:
            data_to_import = imported_data
        
        for user_id_str, user_data in data_to_import.items():
            if user_id_str in users_data:
                users_data[user_id_str].update(user_data)
                updated_count += 1
            else:
                users_data[user_id_str] = user_data
                imported_count += 1
        
        save_users_data()
        os.remove(temp_path)
        
        success_msg = f"""
✅ <b>IMPORT COMPLETE!</b>

• Before: {users_before}
• After: {len(users_data)}
• New: {imported_count}
• Updated: {updated_count}
        """
        
        bot.edit_message_text(
            success_msg, 
            chat_id=message.chat.id, 
            message_id=status_msg.message_id, 
            parse_mode="HTML"
        )
        
    except Exception as e:
        bot.edit_message_text(
            f"❌ Error: {str(e)}", 
            chat_id=message.chat.id, 
            message_id=status_msg.message_id
        )

# ========== /BACKUP COMMAND ==========
@bot.message_handler(commands=['backup'])
def handle_backup(message):
    """Create data backup"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    try:
        backup_data = {
            "users": users_data,
            "spam": spam_data,
            "pending": pending_verifications,
            "backup_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"backup_{timestamp}.json"
        backup_path = os.path.join(DATA_DIR, backup_file)
        
        with open(backup_path, 'w') as f:
            json.dump(backup_data, f, indent=4)
        
        with open(backup_path, 'rb') as f:
            bot.send_document(
                message.chat.id, 
                f, 
                caption=f"📦 Backup: {len(users_data)} users\n⏰ {timestamp}"
            )
        
    except Exception as e:
        bot.reply_to(message, f"❌ Backup failed: {str(e)}")

# ========== /SAVEDATA COMMAND ==========
@bot.message_handler(commands=['savedata'])
def handle_save_data(message):
    """Force save all data"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    try:
        save_all_data()
        bot.reply_to(
            message, 
            f"✅ All data saved!\n👥 Users: {len(users_data)}\n💾 Location: {DATA_DIR}",
            parse_mode="HTML"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Save failed: {str(e)}")

# ========== /CLEANBACKUPS COMMAND ==========
@bot.message_handler(commands=['cleanbackups'])
def handle_clean_backups(message):
    """Clean old backup files"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    try:
        backup_files = [f for f in os.listdir(DATA_DIR) if f.startswith('backup_') and f.endswith('.json')]
        backup_files.sort(key=lambda x: os.path.getmtime(os.path.join(DATA_DIR, x)))
        
        if len(backup_files) <= 5:
            bot.reply_to(message, f"✅ Only {len(backup_files)} backups found (keeping all)")
            return
        
        files_to_delete = backup_files[:-5]
        deleted_count = 0
        deleted_size = 0
        
        for filename in files_to_delete:
            filepath = os.path.join(DATA_DIR, filename)
            file_size = os.path.getsize(filepath)
            os.remove(filepath)
            deleted_count += 1
            deleted_size += file_size
        
        result_msg = f"""
🧹 <b>CLEANUP COMPLETE</b>

📁 Deleted: {deleted_count} files
💾 Freed: {deleted_size//1024} KB
📊 Remaining: {len(backup_files) - deleted_count} backups
        """
        
        bot.reply_to(message, result_msg, parse_mode="HTML")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Cleanup failed: {str(e)}")

# ========== /SETSTARTMSG COMMAND ==========
@bot.message_handler(commands=['setstartmsg'])
def handle_set_start_message(message):
    """Set custom start message"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ Reply to a message with /setstartmsg")
        return
    
    replied_msg = message.reply_to_message
    
    start_message_data['text'] = replied_msg.caption or replied_msg.text or ""
    start_message_data['has_media'] = False
    
    if replied_msg.photo:
        start_message_data['media_type'] = 'photo'
        start_message_data['file_id'] = replied_msg.photo[-1].file_id
        start_message_data['has_media'] = True
    elif replied_msg.video:
        start_message_data['media_type'] = 'video'
        start_message_data['file_id'] = replied_msg.video.file_id
        start_message_data['has_media'] = True
    elif replied_msg.document:
        start_message_data['media_type'] = 'document'
        start_message_data['file_id'] = replied_msg.document.file_id
        start_message_data['has_media'] = True
    
    save_start_message()
    bot.reply_to(message, "✅ Start message updated!")

# ========== /GETSTARTMSG COMMAND ==========
@bot.message_handler(commands=['getstartmsg'])
def handle_get_start_message(message):
    """View current start message"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    if not start_message_data:
        bot.reply_to(message, "❌ No custom start message set")
        return
    
    media_type = start_message_data.get('media_type', 'text')
    has_media = start_message_data.get('has_media', False)
    text_preview = start_message_data.get('text', '')[:100]
    if len(start_message_data.get('text', '')) > 100:
        text_preview += "..."
    
    info_msg = f"""
<b>📋 CURRENT START MESSAGE</b>

<b>Type:</b> {media_type if has_media else 'Text Only'}
<b>Has Media:</b> {'✅ Yes' if has_media else '❌ No'}
<b>Preview:</b> {text_preview}
    """
    
    bot.reply_to(message, info_msg, parse_mode="HTML")

# ========== /CLEARSTARTMSG COMMAND ==========
@bot.message_handler(commands=['clearstartmsg'])
def handle_clear_start_message(message):
    """Clear custom start message"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    global start_message_data
    start_message_data = {}
    save_start_message()
    bot.reply_to(message, "✅ Custom start message cleared")

# ========== /PENDING COMMAND ==========
@bot.message_handler(commands=['pending'])
def handle_pending(message):
    """Show pending verifications"""
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    if not pending_verifications:
        bot.reply_to(message, "✅ No pending verifications")
        return
    
    text = "<b>⏳ PENDING VERIFICATIONS:</b>\n\n"
    for uid, data in pending_verifications.items():
        plan_id = data['plan']
        plan_name = config.PLANS[plan_id]['name'] if plan_id in config.PLANS else plan_id
        text += f"👤 ID: <code>{uid}</code>\n"
        text += f"📅 Plan: {plan_name}\n"
        text += f"💰 Amount: ₹{data['amount']}\n"
        text += f"⏰ Time: {data['initiated_at']}\n"
        text += f"📸 Screenshot: {'✅' if 'screenshot_file_id' in data else '❌'}\n"
        text += "───────────────\n"
    
    # Split if too long
    if len(text) > 4000:
        parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for part in parts:
            bot.send_message(message.chat.id, part, parse_mode="HTML")
    else:
        bot.reply_to(message, text, parse_mode="HTML")

# ========== /HELP COMMAND (FIXED HTML) ==========
@bot.message_handler(commands=['help'])
def handle_help(message):
    """Show help message"""
    if str(message.from_user.id) != ADMIN_ID:
        # User help
        user_help = f"""
<b>🤖 Bot Commands:</b>

/start - Start the bot
/help - Show this help

For premium: Click "Get Premium" button

<b>Demo Channel:</b> {settings['demo_channel_link']}
<b>Support:</b> @{settings['support_username']}
        """
        bot.reply_to(message, user_help, parse_mode="HTML")
        return
    
    # Admin help
    admin_help = """
<b>👮 ADMIN COMMANDS</b>

<b>📋 VERIFICATION:</b>
/pending - Show pending verifications
/verify [user_id] - Manual verify

<b>⚙️ SETTINGS:</b>
/settings - View all settings
/set [key] [value] - Change setting

<b>� PRICE MANAGEMENT:</b>
/set_price single [amount] - Set single channel price (e.g. /set_price single 99)
/set_price all [amount] - Set all channels price (e.g. /set_price all 299)
/demo_price [amount] - Set demo price
/set_demo_ch [channel_id] - Set demo channel ID
/set_demo_link [url] - Set demo link
/demo_toggle - Toggle demo between FREE and PAID
/set_buy_url [url] - Set how to buy guide link
/set_proof_link [url] - Set payment proof channel link
/proof_toggle - Toggle payment proof button ON/OFF

<b>📺 CHANNEL MANAGEMENT:</b>
/add_premium_ch id Full Name price channel_id - Add new channel
/remove_premium_ch id - Remove channel
/edit_premium_ch id key New Value - Edit channel
/force_join_toggle - Toggle force join ON/OFF
/support_toggle - Toggle support button ON/OFF
/auto_accept - Toggle Auto-Accept Join Requests ON/OFF
/approve_all - Approve all pending join requests
/set_force_ch channel_id - Set force join (Request Join)
/set_force_msg Your Message - Set message after request
/set_ch [1-7] [channel_id] - Set channel ID (Legacy)
/add_channel [channel_id] - Add channel to force join list
/remove_channel [channel_id] - Remove channel from force join list
/channels - List all force join channels

<b>📢 BROADCAST:</b>
/broadcast (reply) - Broadcast message

<b>📊 DATA:</b>
/stats - Bot statistics
/migrate_to_mongo - Force sync JSON files to MongoDB
/imp_to_mongo (reply) - Import specific JSON file to MongoDB
/exportdata - Export users data
/impdata (reply) - Import data
/backup - Create backup
/savedata - Force save
/cleanbackups - Clean old backups

<b>✏️ START MESSAGE:</b>
/setstartmsg (reply) - Set custom start
/getstartmsg - View current
/clearstartmsg - Clear custom

<b>ℹ️ OTHER:</b>
/help - Show this help
    """
    
    # Split if too long
    if len(admin_help) > 4000:
        parts = [admin_help[i:i+4000] for i in range(0, len(admin_help), 4000)]
        for part in parts:
            bot.send_message(message.chat.id, part, parse_mode="HTML")
    else:
        bot.reply_to(message, admin_help, parse_mode="HTML")

# ========== SILENT HANDLER ==========
@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    # Ignore all other messages
    pass

# ========== START BOT ==========
if __name__ == "__main__":
    print("=" * 60)
    print("🤖 PREMIUM BOT - TWO CHANNELS + DYNAMIC CONFIG")
    print("=" * 60)
    
    print(f"✅ Bot Token: {BOT_TOKEN[:15]}...")
    print(f"✅ Admin ID: {ADMIN_ID}")
    print(f"✅ Users Loaded: {len(users_data)}")
    print(f"✅ Pending: {len(pending_verifications)}")
    print(f"✅ Single Channel Price: ₹{settings.get('ch_price', '99')}")
    print(f"✅ All Channels Price: ₹{settings.get('all_price', '299')}")
    print("=" * 60)
    print("📋 Type /help for all commands")
    print("📋 Type /settings to view/edit config")
    print("=" * 60)
    
    try:
        # Include chat_member and chat_join_request in allowed_updates
        bot.infinity_polling(allowed_updates=["message", "callback_query", "chat_member", "chat_join_request"])
    except Exception as e:
        print(f"❌ Bot Error: {e}")
        time.sleep(10)
        sys.exit(1)
