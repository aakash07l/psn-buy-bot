"""
PlayStation Sniper & Auto-Buy Bot (Bot 2) - Main Entry Point.
Listens on local webhook (port 5000) for Bot 1 triggers and manages Telegram interface.
ZERO polling on Rooter while idle to prevent account bans!
"""

import sys
import os
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncio
import logging
import warnings
from aiohttp import web
from telegram.warnings import PTBUserWarning
warnings.filterwarnings("ignore", category=PTBUserWarning)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

import config
from bot.states import BotState
from bot.handlers import (
    start_command,
    status_command,
    toggle_sim_handler,
    balance_command,
    manual_buy_command,
    set_target_command,
    set_email_command,
    login_start_handler,
    handle_phone_received,
    handle_otp_received,
    cancel_flow,
)
from services.webhook_server import create_webhook_app
from services.browser_manager import browser_manager

# Logging setup
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("PSNBuyBot")

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def build_telegram_application():
    """Constructs the Telegram bot application with all command and conversation handlers."""
    if not config.BOT_TOKEN or config.BOT_TOKEN == "your_telegram_bot_token_here":
        logger.error("FATAL: BOT_TOKEN is not configured! Please set BOT_TOKEN in .env")
        sys.exit(1)

    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # 1. Interactive Phone + OTP Login Conversation
    login_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("login", login_start_handler),
            CallbackQueryHandler(login_start_handler, pattern="^start_login_flow$"),
        ],
        states={
            BotState.AWAITING_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_received),
            ],
            BotState.AWAITING_OTP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_otp_received),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_flow),
            CallbackQueryHandler(cancel_flow, pattern="^cancel_action$"),
        ],
        per_chat=True,
    )
    app.add_handler(login_conversation)

    # 2. Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("sim", toggle_sim_handler))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("buy", manual_buy_command))
    app.add_handler(CommandHandler("set_target", set_target_command))
    app.add_handler(CommandHandler("set_email", set_email_command))

    # 3. Callback Query Handlers
    app.add_handler(CallbackQueryHandler(status_command, pattern="^cmd_status$"))
    app.add_handler(CallbackQueryHandler(balance_command, pattern="^cmd_balance$"))
    app.add_handler(CallbackQueryHandler(toggle_sim_handler, pattern="^toggle_sim$"))
    app.add_handler(CallbackQueryHandler(manual_buy_command, pattern="^cmd_manual_buy$"))
    app.add_handler(CallbackQueryHandler(status_command, pattern="^show_target_info$"))

    return app


async def start_services():
    """Initializes and runs Telegram Bot Polling and internal Webhook Server concurrently."""
    logger.info("Initializing PlayStation Sniper Bot (Bot 2)...")

    # 1. Initialize Telegram Application
    tg_app = build_telegram_application()
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)
    logger.info("🤖 Telegram command handler running!")

    # 2. Start Internal Webhook Server (aiohttp) on port 5000
    webhook_app = create_webhook_app(tg_app.bot)
    runner = web.AppRunner(webhook_app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEBHOOK_HOST, config.WEBHOOK_PORT)
    await site.start()
    logger.info(f"⚡ Internal Webhook Server listening on {config.WEBHOOK_HOST}:{config.WEBHOOK_PORT}")

    # Boot Notification to Telegram
    for cid in config.ADMIN_CHAT_IDS:
        try:
            await tg_app.bot.send_message(
                chat_id=cid,
                text=(
                    f"🤖 <b>PlayStation Buy Bot (Bot 2) Started!</b>\n\n"
                    f"📡 <b>Status:</b> 🟢 <b>STANDBY MODE ACTIVE</b>\n"
                    f"🎯 <b>Target:</b> PlayStation ₹{config.TARGET_DENOMINATION:,} x {config.TARGET_QUANTITY} cards\n"
                    f"⚡ <b>Internal Webhook:</b> Listening on port {config.WEBHOOK_PORT}\n"
                    f"🛡️ <b>Anti-Ban Zero-Polling:</b> Active (0 Rooter requests until trigger)\n\n"
                    f"<i>Run /login to authenticate your Rooter account (Phone + OTP).</i>"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass

    # Keep server running 24/7
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down Buy Bot...")
        await site.stop()
        await runner.cleanup()
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        await browser_manager.close()


if __name__ == "__main__":
    try:
        asyncio.run(start_services())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Buy Bot terminated.")
