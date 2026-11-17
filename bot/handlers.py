"""
Telegram Bot Handlers for PlayStation Sniper & Auto-Buy Bot (Bot 2).
Handles multi-account management (up to 50 accounts), Cookie/Token and Phone+OTP login,
sniper arming/pausing (/snipe, /stop), live balance synchronization, and system status.
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
)

import config
from bot.states import BotState
from bot.keyboards import (
    get_main_menu_keyboard,
    get_cancel_keyboard,
    get_login_options_keyboard,
    get_accounts_keyboard,
)
from services.browser_manager import browser_manager
from services.rooter_service import rooter_service
from services.account_manager import account_manager, extract_user_id
from services.webhook_server import (
    trigger_instant_snipe,
    is_sniper_active,
    set_sniper_active,
    PURCHASE_LOCK,
)

logger = logging.getLogger("BuyBotHandlers")

# In-memory store for active interactive login browser sessions
ACTIVE_LOGIN_PAGES: Dict[int, Any] = {}


def is_authorized(user_id: int) -> bool:
    """Checks if Telegram user is in ADMIN_CHAT_IDS."""
    if not config.ADMIN_CHAT_IDS:
        return True
    return user_id in config.ADMIN_CHAT_IDS


# ==========================================
# Main Menu & Status Handlers
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command and main menu display."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    sniper_status = "🟢 <b>ARMED & ACTIVE</b>" if is_sniper_active() else "⏸️ <b>PAUSED</b> (/snipe to arm)"
    sim_status = "🧪 Simulation (Dry Run)" if config.SIMULATION_MODE else "⚡ Live Purchasing (Real Funds)"

    active_accs = account_manager.get_active()
    total_accs = account_manager.get_all()
    total_bal = account_manager.get_total_balance()

    msg = (
        f"🤖 <b>PlayStation Sniper & Auto-Buy Bot (Bot 2)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Sniper Status:</b> {sniper_status}\n"
        f"• <b>Connected Accounts:</b> <b>{len(active_accs)}</b> active / {len(total_accs)} total\n"
        f"• <b>Active Wallet Balance:</b> <b>₹{total_bal:,.2f}</b>\n"
        f"• <b>Target:</b> 🎮 <b>PlayStation ₹{config.TARGET_DENOMINATION:,} x {config.TARGET_QUANTITY} cards</b> (₹{config.TARGET_DENOMINATION * config.TARGET_QUANTITY:,}/acc)\n"
        f"• <b>Execution Mode:</b> {sim_status}\n"
        f"• <b>Zero-Polling:</b> Active (0 Rooter requests while idle to prevent bans)\n"
        f"• <b>Internal Webhook:</b> Listening on port <code>{config.WEBHOOK_PORT}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💡 <i>This bot waits silently on standby. The moment Stock Monitor Bot (Bot 1) detects stock, all active accounts execute simultaneous purchases!</i>"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /status command."""
    if not is_authorized(update.effective_user.id):
        return

    sniper_status = "🟢 ARMED (Will auto-buy on restock)" if is_sniper_active() else "⏸️ PAUSED (Will NOT buy)"
    sim_status = "🧪 Simulation Mode (Safe Dry Run)" if config.SIMULATION_MODE else "⚡ Live Purchasing (Real Funds)"

    active_accs = account_manager.get_active()
    total_accs = account_manager.get_all()
    total_bal = account_manager.get_total_balance()

    msg = (
        f"📊 <b>Buy Bot System Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Sniper State:</b> {sniper_status}\n"
        f"• <b>Target Voucher:</b> PlayStation ₹{config.TARGET_DENOMINATION:,}\n"
        f"• <b>Target Quantity:</b> {config.TARGET_QUANTITY} cards (Total: ₹{config.TARGET_DENOMINATION * config.TARGET_QUANTITY:,}/acc)\n"
        f"• <b>Active Accounts:</b> {len(active_accs)} of {len(total_accs)}\n"
        f"• <b>Combined Active Balance:</b> ₹{total_bal:,.2f}\n"
        f"• <b>Execution Mode:</b> {sim_status}\n"
        f"• <b>Delivery Email:</b> {config.DELIVERY_EMAIL if config.DELIVERY_EMAIL else '<i>Default / Not Set</i>'}\n"
        f"• <b>Webhook Listener:</b> Port {config.WEBHOOK_PORT} (Localhost <2ms latency)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ <i>System running 24/7 on AWS Mumbai.</i>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())


# ==========================================
# Sniper Controls: /snipe and /stop
# ==========================================
async def snipe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /snipe [amount] command.
    Arms the sniper for auto-buying when restock signal arrives.
    """
    if not is_authorized(update.effective_user.id):
        return

    if context.args:
        try:
            target_amount = int(context.args[0])
            config.TARGET_DENOMINATION = target_amount
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number. Example: <code>/snipe 1000</code>", parse_mode=ParseMode.HTML)
            return

    set_sniper_active(True)
    active_accs = account_manager.get_active()
    total_bal = account_manager.get_total_balance()
    cost = config.TARGET_DENOMINATION * config.TARGET_QUANTITY

    msg = (
        f"🎯 <b>PLAYSTATION SNIPER ARMED & ACTIVE!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Target:</b> PlayStation ₹{config.TARGET_DENOMINATION:,} x {config.TARGET_QUANTITY} cards (₹{cost:,}/acc)\n"
        f"• <b>Active Accounts:</b> {len(active_accs)} account(s)\n"
        f"• <b>Total Balance:</b> ₹{total_bal:,.2f}\n"
        f"• <b>Status:</b> 🟢 <b>ARMED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚡ <i>When Stock Notify Bot detects stock, all {len(active_accs)} active account(s) will execute checkout simultaneously!</i>\n"
        f"<i>To pause auto-purchasing at any time, run /stop.</i>"
    )
    if update.callback_query:
        await update.callback_query.answer("🎯 Sniper Armed!")
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /stop command.
    Pauses auto-purchasing while Stock Notify Bot continues running 24/7.
    """
    if not is_authorized(update.effective_user.id):
        return

    set_sniper_active(False)
    msg = (
        f"⏸️ <b>PLAYSTATION SNIPER PAUSED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Auto-Buy:</b> 🔴 <b>OFF</b>\n"
        f"• <b>Stock Monitor (Bot 1):</b> 🟢 <b>RUNNING 24/7</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<i>Restock signals received from Stock Monitor Bot will NOT trigger purchases while paused.</i>\n"
        f"<i>Run /snipe or tap the Sniper button to re-arm at any time!</i>"
    )
    if update.callback_query:
        await update.callback_query.answer("⏸️ Sniper Paused")
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())


async def toggle_sniper_arm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline button callback to toggle sniper between armed and paused."""
    if not is_authorized(update.effective_user.id):
        return
    query = update.callback_query
    await query.answer()

    new_state = not is_sniper_active()
    set_sniper_active(new_state)

    if new_state:
        await snipe_command(update, context)
    else:
        await stop_command(update, context)


# ==========================================
# Multi-Account Management (/accounts)
# ==========================================
async def accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays registered accounts with balance and active/paused state."""
    if not is_authorized(update.effective_user.id):
        return

    accounts = account_manager.get_all()
    active_accs = account_manager.get_active()
    total_bal = account_manager.get_total_balance()

    lines = [
        f"👥 <b>Rooter Accounts ({len(active_accs)}/{len(accounts)} Active)</b>",
        f"💰 <b>Total Active Balance:</b> <b>₹{total_bal:,.2f}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not accounts:
        lines.append("<i>No accounts connected yet! Tap below to add your accounts.</i>")
    else:
        for idx, acc in enumerate(accounts, start=1):
            badge = "🟢 <b>Active</b>" if acc.get("is_active", True) else "⏸️ <i>Paused</i>"
            bal = float(acc.get("wallet_balance", 0.0) or 0.0)
            uid = acc.get("user_id") or "N/A"
            phone = f" (+91 {acc.get('phone')})" if acc.get("phone") else ""
            label = acc.get("label", f"Account {idx}")
            lines.append(f"{idx}. {badge} <b>{label}{phone}</b>\n   • Balance: ₹{bal:,.2f} | UID: <code>{uid}</code>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>Tap an account button below to toggle Active/Paused or delete it.</i>")
    text = "\n".join(lines)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=get_accounts_keyboard(accounts))
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_accounts_keyboard(accounts))


async def toggle_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles toggle_acc_<aid> inline button click."""
    if not is_authorized(update.effective_user.id):
        return
    query = update.callback_query
    await query.answer()

    acc_id = query.data.replace("toggle_acc_", "").strip()
    new_status = account_manager.toggle_active(acc_id)
    logger.info(f"Toggled account {acc_id} -> {new_status}")
    await accounts_command(update, context)


async def delete_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles del_acc_<aid> inline button click."""
    if not is_authorized(update.effective_user.id):
        return
    query = update.callback_query
    acc_id = query.data.replace("del_acc_", "").strip()

    removed = account_manager.remove_account(acc_id)
    if removed:
        await query.answer("Account removed!")
    else:
        await query.answer("Could not remove account.")
    await accounts_command(update, context)


async def refresh_all_balances_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refreshes live wallet balance for all registered accounts concurrently."""
    if not is_authorized(update.effective_user.id):
        return
    query = update.callback_query
    await query.answer("Refreshing live balances from Rooter API...")

    await account_manager.sync_all_balances()
    await accounts_command(update, context)


# ==========================================
# Login Flows: Cookie / Token vs Phone + OTP
# ==========================================
async def choose_login_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts user to select between Cookie/Token paste or Phone+OTP login."""
    if not is_authorized(update.effective_user.id):
        return

    msg = (
        f"🔐 <b>Add Rooter Account</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Choose how you would like to connect your account:\n\n"
        f"1️⃣ <b>Paste Cookie / JWT Token:</b> Instant! Paste your Rooter Bearer token or cookie string.\n"
        f"2️⃣ <b>Phone + SMS OTP:</b> Interactive login via your 10-digit mobile number and SMS OTP.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_login_options_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_login_options_keyboard())


# Conversation: Cookie / Token
async def start_cookie_login_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates token/cookie paste conversation."""
    if not is_authorized(update.effective_user.id):
        return ConversationHandler.END

    msg = (
        f"🔑 <b>Paste Rooter Cookie / JWT Bearer Token</b>\n\n"
        f"Please reply with your Rooter Bearer token or cookie string:\n"
        f"<i>(Example: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...)</i>\n\n"
        f"<i>Type /cancel to abort.</i>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_cancel_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_cancel_keyboard())

    return BotState.AWAITING_COOKIE_DATA


async def handle_cookie_data_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives pasted token/cookie, validates, fetches balance, and stores account."""
    raw_text = update.message.text.strip()
    status_msg = await update.message.reply_text("⏳ Validating token and fetching live balance from Rooter API...", parse_mode=ParseMode.HTML)

    clean_tok = raw_text
    if clean_tok.lower().startswith("bearer "):
        clean_tok = clean_tok[7:].strip()

    if len(clean_tok) < 20:
        await status_msg.edit_text("❌ Token too short or invalid. Please reply with a valid Rooter Bearer token (or /cancel):", reply_markup=get_cancel_keyboard())
        return BotState.AWAITING_COOKIE_DATA

    try:
        new_acc = await account_manager.add_account_token(token=clean_tok)
        bal = float(new_acc.get("wallet_balance", 0.0) or 0.0)
        uid = new_acc.get("user_id") or "N/A"

        success_msg = (
            f"✅ <b>Account Added Successfully via Token!</b>\n\n"
            f"• <b>Account ID:</b> <code>{new_acc['id']}</code>\n"
            f"• <b>Label:</b> {new_acc.get('label')}\n"
            f"• <b>Rooter UID:</b> <code>{uid}</code>\n"
            f"• <b>Live Wallet Balance:</b> <b>₹{bal:,.2f}</b>\n"
            f"• <b>Status:</b> 🟢 <b>Active for Sniping</b>\n\n"
            f"<i>Type /accounts to see all connected accounts or /snipe to arm!</i>"
        )
        await status_msg.edit_text(success_msg, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error adding account token: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Failed to add account: <code>{html.escape(str(e))}</code>")
        return ConversationHandler.END


# Conversation: Phone + OTP
async def start_otp_login_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates Phone + SMS OTP login conversation."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Unauthorized.")
        return ConversationHandler.END

    msg = (
        f"📱 <b>Rooter Account Login (Phone + OTP)</b>\n\n"
        f"Please reply with your <b>10-digit Rooter Mobile Number</b>:\n"
        f"<i>(Example: 9876543210)</i>\n\n"
        f"<i>Type /cancel to abort.</i>"
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
        browser_ctx = await browser_manager.create_context(apply_storage_state=False)
        page = await browser_ctx.new_page()

        await page.goto(config.ROOTER_BASE_URL, timeout=config.BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        await rooter_service.dismiss_location_popup(page)

        # Click login button
        login_btn = page.locator("button[data-testid='header-login-btn']").first
        if not await login_btn.is_visible(timeout=3000):
            login_btn = page.locator("button:has-text('Login'), button[class*='loginButton']").first

        if await login_btn.is_visible(timeout=4000):
            await login_btn.scroll_into_view_if_needed()
            await login_btn.click(force=True)
            await page.wait_for_timeout(1500)

        # Fallback if drawer did not open: open product page and trigger buy
        drawer_visible = await page.locator(".mantine-Drawer-content, [role='dialog'], input[placeholder*='Mobile' i]").first.is_visible(timeout=2000)
        if not drawer_visible:
            await page.goto(config.ROOTER_PLAYSTATION_URL, timeout=config.BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            await rooter_service.dismiss_location_popup(page)
            buy_trigger = page.locator("button:has-text('Proceed to Pay'), button:has-text('Pay via wallet'), button:has-text('Buy Now'), button[data-testid='header-login-btn']").first
            if await buy_trigger.is_visible(timeout=4000):
                await buy_trigger.click(force=True)
                await page.wait_for_timeout(1500)

        # Find phone input
        phone_selectors = [
            "input[placeholder*='Mobile' i]",
            "input[placeholder*='Enter Mobile' i]",
            "input[placeholder*='number' i]",
            "input[type='tel']",
            ".mantine-Drawer-content input",
            "div[role='dialog'] input",
        ]
        phone_input = None
        for sel in phone_selectors:
            cand = page.locator(sel).first
            if await cand.is_visible(timeout=1500):
                phone_input = cand
                break

        if not phone_input or not await phone_input.is_visible(timeout=500):
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

        # Click Get OTP
        get_otp_btn = page.locator(
            "button:has-text('Get OTP'), button:has-text('Continue'), button:has-text('Next'), "
            ".mantine-Drawer-content button[type='submit'], div[role='dialog'] button[type='submit']"
        ).first
        if await get_otp_btn.is_visible(timeout=3000):
            await get_otp_btn.click(force=True)
        else:
            await phone_input.press("Enter")

        await page.wait_for_timeout(1500)

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
    """Receives OTP, completes authentication, and registers the account into account_manager."""
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

        # Click Verify
        verify_btn = page.locator(
            "button:has-text('Verify'), button:has-text('Submit'), button:has-text('Done'), "
            "button:has-text('Continue'), .mantine-Drawer-content button[type='submit'], div[role='dialog'] button[type='submit']"
        ).first
        if await verify_btn.is_visible(timeout=3000):
            await verify_btn.click(force=True)

        await page.wait_for_timeout(3500)

        # Register account with account_manager
        new_acc = await account_manager.add_account_from_context(browser_ctx, phone=phone_10)

        await page.close()
        await browser_ctx.close()
        ACTIVE_LOGIN_PAGES.pop(user_id, None)

        bal = float(new_acc.get("wallet_balance", 0.0) or 0.0)

        await status_msg.edit_text(
            f"✅ <b>Rooter Login Successful!</b>\n\n"
            f"• <b>Account:</b> +91 {phone_10}\n"
            f"• <b>Account ID:</b> <code>{new_acc['id']}</code>\n"
            f"• <b>Live Wallet Balance:</b> <b>₹{bal:,.2f}</b>\n"
            f"• <b>Status:</b> 🟢 <b>Active & Ready for Sniping</b>\n\n"
            f"<i>Type /accounts to see all accounts or /snipe to arm!</i>",
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
# Balances & Target Configuration
# ==========================================
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Checks and displays live wallet balances for all accounts."""
    if not is_authorized(update.effective_user.id):
        return

    # Sync live balances from payments API
    await account_manager.sync_all_balances()
    await accounts_command(update, context)


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


async def manual_buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executes an instant manual buy across active accounts."""
    if not is_authorized(update.effective_user.id):
        return

    active_accs = account_manager.get_active()
    if not active_accs and not browser_manager.has_session() and not config.SIMULATION_MODE:
        await update.message.reply_text(
            "⚠️ You must connect at least one account first! Use /login or enable /sim mode to test.",
            parse_mode=ParseMode.HTML
        )
        return

    denom = config.TARGET_DENOMINATION
    qty = config.TARGET_QUANTITY

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(f"🚀 Triggering instant manual purchase for PlayStation ₹{denom:,} x {qty}...")
    else:
        await update.message.reply_text(f"🚀 Triggering instant manual purchase for PlayStation ₹{denom:,} x {qty}...")

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
            f"✅ Target updated to <b>PlayStation ₹{denom:,} x {qty} cards</b> (Total: ₹{denom * qty:,}/acc)!",
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
