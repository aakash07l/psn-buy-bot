"""
Telegram Bot Handlers for PlayStation Sniper & Auto-Buy Bot (Bot 2).
Handles interactive Phone+OTP login, wallet balance, manual triggers, and system status.
"""

import os
import re
import html
import asyncio
import logging
from typing import Dict, Any, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

import config
from bot.states import BotState
from bot.keyboards import get_main_menu_keyboard, get_cancel_keyboard
from services.browser_manager import browser_manager
from services.rooter_service import rooter_service
from services.webhook_server import trigger_instant_snipe, PURCHASE_LOCK

logger = logging.getLogger("BuyBotHandlers")

# In-memory store for active login sessions
ACTIVE_LOGIN_PAGES: Dict[int, Any] = {}


def is_authorized(user_id: int) -> bool:
    """Checks if Telegram user is in ADMIN_CHAT_IDS."""
    if not config.ADMIN_CHAT_IDS:
        return True
    return user_id in config.ADMIN_CHAT_IDS


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    session_status = "🟢 Logged In" if browser_manager.has_session() else "🔴 Not Logged In (/login)"
    sim_status = "🧪 Simulation (Dry Run)" if config.SIMULATION_MODE else "⚡ Live Purchasing"

    msg = (
        f"🤖 <b>PlayStation Sniper & Auto-Buy Bot (Bot 2)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Status:</b> 🟢 <b>STANDBY MODE ACTIVE</b>\n"
        f"• <b>Session:</b> {session_status}\n"
        f"• <b>Target:</b> 🎮 <b>PlayStation ₹{config.TARGET_DENOMINATION:,} x {config.TARGET_QUANTITY} cards</b> (₹{config.TARGET_DENOMINATION * config.TARGET_QUANTITY:,})\n"
        f"• <b>Mode:</b> {sim_status}\n"
        f"• <b>Zero-Polling:</b> Active (0 Rooter requests while idle to prevent bans)\n"
        f"• <b>Internal Webhook:</b> Listening on port <code>{config.WEBHOOK_PORT}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💡 <i>This bot stays completely idle until Stock Monitor Bot (Bot 1) sends a restock signal!</i>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /status command."""
    if not is_authorized(update.effective_user.id):
        return

    session_status = "🟢 Logged In" if browser_manager.has_session() else "🔴 Not Logged In (/login)"
    sim_status = "🧪 Simulation Mode (Safe Dry Run)" if config.SIMULATION_MODE else "⚡ Live Purchasing (Real Funds)"

    msg = (
        f"📊 <b>Buy Bot System Status</b>\n\n"
        f"• <b>Rooter Session:</b> {session_status}\n"
        f"• <b>Target Voucher:</b> PlayStation ₹{config.TARGET_DENOMINATION:,}\n"
        f"• <b>Target Quantity:</b> {config.TARGET_QUANTITY} cards (Total: ₹{config.TARGET_DENOMINATION * config.TARGET_QUANTITY:,})\n"
        f"• <b>Execution Mode:</b> {sim_status}\n"
        f"• <b>Delivery Email:</b> {config.DELIVERY_EMAIL if config.DELIVERY_EMAIL else '<i>Default / Not Set</i>'}\n"
        f"• <b>Webhook Listener:</b> Port {config.WEBHOOK_PORT} (Localhost <2ms)\n\n"
        f"✅ <i>Ready and waiting on standby for restock trigger.</i>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())


async def toggle_sim_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles Simulation / Dry-run Mode."""
    if not is_authorized(update.effective_user.id):
        return

    config.SIMULATION_MODE = not config.SIMULATION_MODE
    status_str = "🟢 ON (Safe Mode)" if config.SIMULATION_MODE else "🔴 OFF (Real Orders)"

    text = f"⚙️ <b>Simulation Mode is now: {status_str}</b>"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())


# ==========================================
# Interactive Phone + OTP Login Conversation
# ==========================================
async def login_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the /login conversation."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Unauthorized.")
        return ConversationHandler.END

    msg = (
        f"🔐 <b>Rooter Account Login (Phone + OTP)</b>\n\n"
        f"No static cookies needed! Please reply with your <b>10-digit Rooter Mobile Number</b>:\n"
        f"<i>(Example: 9876543210)</i>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_cancel_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_cancel_keyboard())

    return BotState.AWAITING_PHONE


async def handle_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives phone number and requests OTP from Rooter."""
    user_id = update.effective_user.id
    raw_phone = update.message.text.strip()
    clean_phone = re.sub(r"\D", "", raw_phone)

    if len(clean_phone) < 10:
        await update.message.reply_text(
            "❌ Invalid mobile number. Please enter a valid 10-digit number (or /cancel):",
            reply_markup=get_cancel_keyboard()
        )
        return BotState.AWAITING_PHONE

    phone_10 = clean_phone[-10:]
    status_msg = await update.message.reply_text(
        f"⏳ Opening Rooter and requesting OTP for <b>+91 {phone_10}</b>...",
        parse_mode=ParseMode.HTML
    )

    try:
        # Launch browser to request OTP
        browser_ctx = await browser_manager.create_context(apply_storage_state=False)
        page = await browser_ctx.new_page()

        await page.goto(config.ROOTER_BASE_URL, timeout=config.BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        # 1. Dismiss any location or region modals
        await rooter_service.dismiss_location_popup(page)

        # 2. Click Login button (Try specific test-id first, then class, then text)
        login_btn = page.locator(
            "button[data-testid='header-login-btn'], "
            "button[class*='loginButton'], "
            "button:has-text('Login'), "
            "button:has-text('Sign In'), "
            "div:has-text('Login')"
        ).first

        if await login_btn.is_visible(timeout=5000):
            try:
                await login_btn.click(force=True, timeout=2000)
            except Exception:
                await login_btn.dispatch_event("click")
            await page.wait_for_timeout(1500)

        # 3. Dismiss location popup again in case it appeared
        await rooter_service.dismiss_location_popup(page)

        # 4. Search for phone input
        phone_selectors = [
            "input[placeholder*='Mobile' i]",
            "input[placeholder*='number' i]",
            "input[type='tel']",
            "input[name*='phone']",
            "input[name*='mobile']",
            ".mantine-Drawer-content input",
            "div[role='dialog'] input",
        ]
        phone_input = None
        for sel in phone_selectors:
            cand = page.locator(sel).first
            if await cand.is_visible(timeout=1000):
                phone_input = cand
                break

        # If not immediately visible, wait up to 4s for drawer animation
        if not phone_input:
            try:
                phone_input = page.locator("input[placeholder*='Mobile' i], input[type='tel'], .mantine-Drawer-content input").first
                await phone_input.wait_for(state="visible", timeout=4000)
            except Exception:
                phone_input = None

        if not phone_input or not await phone_input.is_visible(timeout=500):
            # Capture debug screenshot to show user what Rooter is displaying
            shot_path = await browser_manager.capture_screenshot(page, name_prefix="login_error")
            await status_msg.edit_text("❌ Login form not found on Rooter. Debug screenshot below:")
            if shot_path and os.path.exists(shot_path):
                with open(shot_path, "rb") as f:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=f,
                        caption="📸 Screenshot of Rooter screen during login attempt"
                    )
            await page.close()
            await browser_ctx.close()
            return ConversationHandler.END

        await phone_input.fill(phone_10)
        await page.wait_for_timeout(500)

        # Click Continue / Get OTP
        get_otp_btn = page.locator(
            "button:has-text('Get OTP'), "
            "button:has-text('Continue'), "
            "button:has-text('Next'), "
            ".mantine-Drawer-content button[type='submit'], "
            "div[role='dialog'] button[type='submit']"
        ).first
        if await get_otp_btn.is_visible(timeout=3000):
            await get_otp_btn.click(force=True)
        else:
            await phone_input.press("Enter")

        await page.wait_for_timeout(1500)

        # Store session in memory for the OTP step
        ACTIVE_LOGIN_PAGES[user_id] = {
            "context": browser_ctx,
            "page": page,
            "phone": phone_10,
        }

        await status_msg.edit_text(
            f"📩 <b>OTP Sent!</b>\n\n"
            f"Rooter has sent an SMS OTP to <b>+91 {phone_10}</b>.\n"
            f"Please reply with the <b>4 or 6-digit OTP</b> here (or /cancel):",
            parse_mode=ParseMode.HTML,
            reply_markup=get_cancel_keyboard(),
        )
        return BotState.AWAITING_OTP

    except Exception as e:
        logger.error(f"Error requesting OTP: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Failed to request OTP: <code>{html.escape(str(e))}</code>")
        return ConversationHandler.END


async def handle_otp_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives OTP, completes authentication, and persists storage state."""
    user_id = update.effective_user.id
    raw_otp = update.message.text.strip()
    clean_otp = re.sub(r"\D", "", raw_otp)

    session_data = ACTIVE_LOGIN_PAGES.get(user_id)
    if not session_data:
        await update.message.reply_text("❌ Login session expired. Please type /login to restart.")
        return ConversationHandler.END

    browser_ctx = session_data["context"]
    page = session_data["page"]
    phone_10 = session_data["phone"]

    status_msg = await update.message.reply_text("⏳ Verifying OTP with Rooter...", parse_mode=ParseMode.HTML)

    try:
        # Fill OTP inputs
        otp_inputs = page.locator("input[type='number'], input[inputmode='numeric'], input[class*='pinInput'], .mantine-PinInput-input")
        count = await otp_inputs.count()
        if count > 1:
            for i in range(min(count, len(clean_otp))):
                await otp_inputs.nth(i).fill(clean_otp[i])
        else:
            single_input = page.locator("input[placeholder*='OTP'], input[name*='otp'], input[type='tel'], input[inputmode='numeric']").first
            await single_input.fill(clean_otp)

        await page.wait_for_timeout(800)

        # Click Verify / Submit button
        verify_btn = page.locator(
            "button:has-text('Verify'), "
            "button:has-text('Submit'), "
            "button:has-text('Done'), "
            "button:has-text('Continue'), "
            ".mantine-Drawer-content button[type='submit'], "
            "div[role='dialog'] button[type='submit']"
        ).first
        if await verify_btn.is_visible(timeout=3000):
            await verify_btn.click(force=True)

        await page.wait_for_timeout(3500)

        # Save session storage state
        await browser_ctx.storage_state(path=str(config.STORAGE_STATE_FILE))
        logger.info(f"✅ Rooter session saved for {phone_10} into {config.STORAGE_STATE_FILE}")

        await page.close()
        await browser_ctx.close()
        ACTIVE_LOGIN_PAGES.pop(user_id, None)

        await status_msg.edit_text(
            f"✅ <b>Rooter Login Successful!</b>\n\n"
            f"• <b>Account:</b> +91 {phone_10}\n"
            f"• <b>Session:</b> Saved permanently to <code>{config.STORAGE_STATE_FILE.name}</code>\n"
            f"• <b>Target:</b> PlayStation ₹{config.TARGET_DENOMINATION:,} x {config.TARGET_QUANTITY} cards\n\n"
            f"🤖 <b>Bot is now on STANDBY.</b> It will sleep silently until Stock Monitor Bot signals a restock!",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu_keyboard(),
        )
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error verifying OTP: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ OTP verification failed: <code>{html.escape(str(e))}</code>")
        if user_id in ACTIVE_LOGIN_PAGES:
            try:
                await ACTIVE_LOGIN_PAGES[user_id]["page"].close()
                await ACTIVE_LOGIN_PAGES[user_id]["context"].close()
            except Exception:
                pass
            ACTIVE_LOGIN_PAGES.pop(user_id, None)
        return ConversationHandler.END


async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels active conversation."""
    user_id = update.effective_user.id
    if user_id in ACTIVE_LOGIN_PAGES:
        try:
            await ACTIVE_LOGIN_PAGES[user_id]["page"].close()
            await ACTIVE_LOGIN_PAGES[user_id]["context"].close()
        except Exception:
            pass
        ACTIVE_LOGIN_PAGES.pop(user_id, None)

    msg = "❌ Action cancelled. Returning to main menu."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(msg, reply_markup=get_main_menu_keyboard())

    return ConversationHandler.END


# ==========================================
# Manual Buy & Target Settings
# ==========================================
async def manual_buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executes an instant manual buy on demand."""
    if not is_authorized(update.effective_user.id):
        return

    if not browser_manager.has_session() and not config.SIMULATION_MODE:
        await update.message.reply_text(
            "⚠️ You must login first! Run /login to authenticate your Rooter account, or enable /sim mode to test.",
            parse_mode=ParseMode.HTML
        )
        return

    denom = config.TARGET_DENOMINATION
    qty = config.TARGET_QUANTITY

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(f"🚀 Triggering manual purchase for PlayStation ₹{denom:,} x {qty}...")
    else:
        await update.message.reply_text(f"🚀 Triggering manual purchase for PlayStation ₹{denom:,} x {qty}...")

    # Execute purchase workflow
    asyncio.create_task(trigger_instant_snipe(denom, qty, context.bot))


async def set_target_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /set_target <denomination> [quantity]."""
    if not is_authorized(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: <code>/set_target 1000 2</code>", parse_mode=ParseMode.HTML)
        return

    try:
        denom = int(context.args[0])
        qty = int(context.args[1]) if len(context.args) > 1 else config.TARGET_QUANTITY
        config.TARGET_DENOMINATION = denom
        config.TARGET_QUANTITY = qty
        await update.message.reply_text(
            f"✅ Target updated to <b>PlayStation ₹{denom:,} x {qty} cards</b> (Total: ₹{denom * qty:,})!",
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await update.message.reply_text("❌ Please enter valid numbers. Example: <code>/set_target 1000 2</code>", parse_mode=ParseMode.HTML)


async def set_email_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /set_email <email> for delivery details."""
    if not is_authorized(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: <code>/set_email user@example.com</code>", parse_mode=ParseMode.HTML)
        return

    email = context.args[0].strip()
    config.DELIVERY_EMAIL = email
    await update.message.reply_text(f"✅ Delivery email set to <code>{email}</code>", parse_mode=ParseMode.HTML)


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Checks live wallet balance."""
    if not is_authorized(update.effective_user.id):
        return

    msg = (
        f"💰 <b>Rooter Wallet Balance</b>\n\n"
        f"• <b>Session Status:</b> {'🟢 Ready' if browser_manager.has_session() else '🔴 Not Logged In (/login)'}\n"
        f"• <b>Current Target:</b> PlayStation ₹{config.TARGET_DENOMINATION:,} x {config.TARGET_QUANTITY} cards (₹{config.TARGET_DENOMINATION * config.TARGET_QUANTITY:,})\n\n"
        f"<i>Funds will only be utilized when stock arrives.</i>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
