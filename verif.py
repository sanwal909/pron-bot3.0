import telebot
from telebot import types
import time
import threading
from datetime import datetime, timedelta
import logging

# Import config FIRST
import config
from config import *

logger = logging.getLogger(__name__)

class VerificationSystem:
    def __init__(self, bot):
        self.bot = bot
        self.pending = pending_verifications
    
    def save_pending(self):
        """Save pending verifications"""
        save_json_file(PENDING_VERIF_FILE, self.pending)
    
    def create_invite_link(self, user_id, plan_type):
        """Create unique invite link(s) for specific channel(s) based on plan"""
        global invite_links
        try:
            plan = config.PLANS[plan_type]
            
            # Special case for "all" channels
            if plan_type == "all":
                channel_ids = plan.get('channel_ids', [])
                valid_links = []
                for idx, cid in enumerate(channel_ids, 1):
                    if not cid: continue
                    try:
                        invite = self.bot.create_chat_invite_link(
                            chat_id=int(cid),
                            member_limit=1,
                            expire_date=datetime.now() + timedelta(days=365)
                        )
                        valid_links.append(f"Channel {idx}: {invite.invite_link}")
                    except Exception as e:
                        logger.error(f"Error creating invite for {cid}: {e}")
                
                if not valid_links:
                    return "Error: No channel IDs configured for All Channels. Contact admin."
                
                # Store links
                user_id_str = str(user_id)
                if user_id_str not in invite_links:
                    invite_links[user_id_str] = []
                
                link_data = {
                    'plan': plan_type,
                    'links': valid_links,
                    'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                invite_links[user_id_str].append(link_data)
                save_json_file(INVITE_LINKS_FILE, invite_links)
                
                return "\n".join(valid_links)

            # Standard case for single channel
            channel_id = plan.get('channel_id', '')
            if not channel_id:
                # Special fallback for demo if ID is missing but link exists
                if plan_type == "demo" and settings.get('demo_channel_link'):
                    return settings.get('demo_channel_link')
                return f"Error: Channel ID not configured for {plan['name']}. Contact admin."
            
            expire_date = datetime.now() + timedelta(days=365)
            invite = self.bot.create_chat_invite_link(
                chat_id=int(channel_id),
                member_limit=1,
                expire_date=expire_date
            )
            
            user_id_str = str(user_id)
            if user_id_str not in invite_links:
                invite_links[user_id_str] = []
            
            invite_links[user_id_str].append({
                'plan': plan_type,
                'link': invite.invite_link,
                'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            save_json_file(INVITE_LINKS_FILE, invite_links)
            
            return invite.invite_link
        except Exception as e:
            logger.error(f"Invite Link Error: {e}")
            return f"Error creating link: {str(e)}"

    def plan_selection_keyboard(self):
        """Dynamic Membership keyboard"""
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        
        channels = settings.get("premium_channels", [])
        for ch in channels:
            keyboard.add(types.InlineKeyboardButton(f"🔗 {ch['name']} - ₹{ch['amount']}", callback_data=f"plan_{ch['id']}"))
            
        keyboard.add(types.InlineKeyboardButton("⬅️ Back to Menu", callback_data="main_menu"))
        return keyboard

    def main_menu_keyboard(self):
        """Main menu with 3 main buttons"""
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        
        # 1. Premium Demo
        is_paid = settings.get('demo_paid_status', False)
        demo_amount = settings.get('demo_amount', '10')
        demo_link = settings.get('demo_channel_link', '')
        
        if is_paid:
            keyboard.add(types.InlineKeyboardButton(f"📢 Premium Demo (₹{demo_amount})", callback_data="plan_demo"))
        elif demo_link:
            keyboard.add(types.InlineKeyboardButton("📢 Premium Demo", url=demo_link))
        else:
            keyboard.add(types.InlineKeyboardButton("📢 Premium Demo (Not Set)", callback_data="demo_not_set"))
            
        # 2. Get Membership
        keyboard.add(types.InlineKeyboardButton("💰 Get Membership", callback_data="get_membership"))
        
        # 3. How To Buy
        how_to_buy_url = settings.get('how_to_buy_url', '')
        if how_to_buy_url:
            keyboard.add(types.InlineKeyboardButton("❓ How To Buy", url=how_to_buy_url))
        else:
            keyboard.add(types.InlineKeyboardButton("❓ How To Buy", callback_data="how_to_get"))
        
        # 4. Payment Proof Channel
        if settings.get("payment_proof_status", True):
            proof_link = settings.get('payment_proof_link', '')
            if proof_link:
                keyboard.add(types.InlineKeyboardButton("🧾 Payment Proofs", url=proof_link))
            else:
                keyboard.add(types.InlineKeyboardButton("🧾 Payment Proofs (Not Set)", callback_data="proof_not_set"))

        # Optional: Support Button
        if settings.get("support_button_status", True):
            support_user = settings.get('support_username', '')
            if support_user:
                keyboard.add(types.InlineKeyboardButton("📞 Support", url=f"https://t.me/{support_user}"))
            else:
                keyboard.add(types.InlineKeyboardButton("📞 Support (Not Set)", callback_data="support_not_set"))
        
        return keyboard
    
    def ask_for_screenshot(self, chat_id, user_id, plan_type):
        """Ask user to send payment screenshot"""
        plan = config.PLANS[plan_type]
        
        msg = self.bot.send_message(
            chat_id,
            f"""
<b>📸 SEND PAYMENT SCREENSHOT</b>

<b>Plan Selected:</b> {plan['name']}
<b>Amount to Pay:</b> ₹{plan['amount']}
<b>UPI ID:</b> <code>{settings['upi_id']}</code>

✅ <b>Payment Done!</b>

Now please send the <b>payment screenshot</b> for verification.

<b>Instructions:</b>
1. Take screenshot of UPI payment
2. Send it here as photo
3. Admin will verify within few minutes
4. You'll receive unique join link after verification

⏳ <i>Please wait for admin verification...</i>
            """,
            parse_mode="HTML"
        )
        return msg
    
    def handle_screenshot(self, message):
        """Handle payment screenshot from user"""
        user_id = str(message.from_user.id)
        
        # Check if user has pending verification
        if user_id not in self.pending:
            return False
        
        if not message.photo:
            self.bot.reply_to(
                message,
                "❌ Please send a PHOTO (screenshot) of your payment."
            )
            return True
        
        pending_data = self.pending[user_id]
        plan_type = pending_data['plan']
        plan = config.PLANS[plan_type]
        
        # Get the largest photo
        photo = message.photo[-1]
        file_id = photo.file_id
        
        # Store screenshot info
        pending_data['screenshot_file_id'] = file_id
        pending_data['screenshot_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pending_data['screenshot_msg_id'] = message.message_id
        self.save_pending()
        
        # Create verification buttons for admin
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        verify_btn = types.InlineKeyboardButton(
            "✅ Verify Payment", 
            callback_data=f"verify_{user_id}"
        )
        reject_btn = types.InlineKeyboardButton(
            "❌ Reject", 
            callback_data=f"reject_{user_id}"
        )
        keyboard.add(verify_btn, reject_btn)
        
        # Forward screenshot to admin log channel
        caption = f"""
📸 <b>PAYMENT SCREENSHOT RECEIVED</b>

👤 User: @{message.from_user.username or 'N/A'}
🆔 User ID: <code>{user_id}</code>
📅 Plan: {plan['name']}
💰 Amount: ₹{plan['amount']}
⏰ Time: {pending_data['screenshot_time']}

<b>Verify payment and send join link:</b>
        """
        
        try:
            # Send screenshot to log channel or fallback to Admin ID
            target_chat = settings.get('log_channel')
            if not target_chat:
                target_chat = config.ADMIN_ID
                
            sent_msg = self.bot.send_photo(
                target_chat,
                photo=file_id,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
            # Store admin message ID
            pending_data['admin_msg_id'] = sent_msg.message_id
            pending_data['admin_chat_id'] = target_chat
            self.save_pending()
            
            # Notify user
            self.bot.reply_to(
                message,
                f"""
✅ <b>Screenshot received!</b>

Admin will verify your payment soon.
You'll receive unique join link within few minutes.

⏳ <i>Thank you for your patience!</i>
                """,
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Error forwarding screenshot: {e}")
            self.bot.reply_to(
                message,
                f"❌ Error sending screenshot. Please contact @{settings['support_username']}"
            )
        
        return True
    
    def verify_payment(self, user_id, admin_id):
        """Verify payment and send unique invite link"""
        user_id = str(user_id)
        
        if user_id not in self.pending:
            return False, "User not found in pending verifications"
        
        pending_data = self.pending[user_id]
        plan_type = pending_data['plan']
        plan = config.PLANS[plan_type]
        
        # Create unique invite link for specific channel
        invite_link = self.create_invite_link(user_id, plan_type)
        
        # Check if link creation failed
        if "Error" in invite_link:
            return False, invite_link
        
        # Send join link to user
        try:
            if plan_type == "demo":
                join_msg = f"""
🎉 <b>DEMO ACCESS VERIFIED!</b>

<b>Plan:</b> {plan['name']}
<b>Amount Paid:</b> ₹{plan['amount']}

<b>👇 Your Unique Demo Invite Link (Single Use):</b>
{invite_link}

⚠️ <b>Note:</b> This link can only be used ONCE.
📅 <b>Access Duration:</b> {plan.get('duration', '30 Days')}

<b>Enjoy your demo! 🍿</b>
                """
            else:
                join_msg = f"""
🎉 <b>PAYMENT VERIFIED SUCCESSFULLY!</b>

<b>Plan:</b> {plan['name']}
<b>Amount Paid:</b> ₹{plan['amount']}

<b>👇 Your Unique Invite Link (Single Use):</b>
{invite_link}

⚠️ <b>Note:</b> This link can only be used ONCE and is personal to you.
📅 <b>Access Duration:</b> {plan.get('duration', '30 Days')}

<b>Welcome to Premium Family! 🎊</b>
                """
            
            self.bot.send_message(
                int(user_id),
                join_msg,
                parse_mode="HTML"
            )
            
            # Log verification
            log_msg = f"""
✅ <b>PAYMENT VERIFIED</b>

👤 User ID: <code>{user_id}</code>
📅 Plan: {plan['name']}
💰 Amount: ₹{plan['amount']}
👮 Verified By: Admin
🔗 Invite Link: {invite_link}
⏰ Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            """
            
            target_chat = settings.get('log_channel')
            if not target_chat:
                target_chat = config.ADMIN_ID
                
            self.bot.send_message(
                target_chat,
                log_msg,
                parse_mode="HTML"
            )
            
            # Update user data to mark as premium
            if user_id in users_data:
                users_data[user_id]['is_premium'] = True
                users_data[user_id]['premium_plan'] = plan_type
                users_data[user_id]['premium_until'] = (
                    "lifetime" if plan_type == "lifetime" 
                    else (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                )
                users_data[user_id]['invite_link'] = invite_link
                save_users_data()
            
            # Remove from pending
            del self.pending[user_id]
            self.save_pending()
            
            return True, "User verified and unique join link sent"
            
        except Exception as e:
            logger.error(f"Error sending join link: {e}")
            return False, f"Error sending message: {str(e)}"
    
    def reject_payment(self, user_id, admin_id):
        """Reject payment and notify user"""
        user_id = str(user_id)
        
        if user_id not in self.pending:
            return False, "User not found in pending verifications"
        
        pending_data = self.pending[user_id]
        
        # Notify user
        try:
            reject_msg = f"""
❌ <b>PAYMENT VERIFICATION FAILED</b>

Your payment screenshot could not be verified.

<b>Possible reasons:</b>
• Screenshot not clear
• Wrong amount paid
• Payment not received

<b>Please try again or contact support:</b>
📞 @{settings['support_username']}
            """
            
            self.bot.send_message(
                int(user_id),
                reject_msg,
                parse_mode="HTML"
            )
            
            # Log rejection
            log_msg = f"""
❌ <b>PAYMENT REJECTED</b>

👤 User ID: <code>{user_id}</code>
👮 Rejected By: Admin
⏰ Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            """
            
            target_chat = settings.get('log_channel')
            if not target_chat:
                target_chat = config.ADMIN_ID
                
            self.bot.send_message(
                target_chat,
                log_msg,
                parse_mode="HTML"
            )
            
            # Remove from pending
            del self.pending[user_id]
            self.save_pending()
            
            return True, "Payment rejected and user notified"
            
        except Exception as e:
            logger.error(f"Error rejecting payment: {e}")
            return False, f"Error: {str(e)}"

# Initialize verification system
verification = None

def init_verification(bot_instance):
    global verification
    verification = VerificationSystem(bot_instance)
    return verification
