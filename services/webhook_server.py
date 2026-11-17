"""
Internal Webhook Server for PlayStation Sniper Bot (Bot 2).
Listens on localhost (port 5000) for instant restock triggers from Stock Monitor Bot (Bot 1).
"""

import sys
import os
import time
import asyncio
import logging
from typing import Optional, Dict, Any

from aiohttp import web
from telegram.constants import ParseMode

import config
from services.rooter_service import rooter_service
from services.browser_manager import browser_manager

logger = logging.getLogger("BuyBotWebhook")

# Concurrency lock to prevent duplicate orders if restock triggers fire repeatedly
PURCHASE_LOCK = asyncio.Lock()
LAST_PURCHASE_TIME: float = 0.0


async def dispatch_telegram_broadcast(bot, message_html: str, photo_path: Optional[str] = None):
    """Sends a message or photo to all configured admin chat IDs."""
    for chat_id in config.ADMIN_CHAT_IDS:
        try:
            if photo_path and os.path.exists(photo_path):
                with open(photo_path, "rb") as f:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=message_html,
                        parse_mode=ParseMode.HTML,
                    )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message_html,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                )
        except Exception as e:
            logger.error(f"Failed to send Telegram update to {chat_id}: {e}")


async def trigger_instant_snipe(denomination: int, quantity: int, bot):
    """
    Executes the immediate restock purchase workflow.
    Triggered via internal webhook from Bot 1.
    """
    global LAST_PURCHASE_TIME

    if PURCHASE_LOCK.locked():
        logger.warning(f"Purchase already in progress! Skipping duplicate trigger for ₹{denomination:,} x {quantity}.")
        return

    async with PURCHASE_LOCK:
        now = time.time()
        # Cooldown guard: do not repeat purchase within 30 seconds
        if (now - LAST_PURCHASE_TIME) < 30:
            logger.info("Purchase cooldown active (30s). Skipping redundant restock signal.")
            return

        LAST_PURCHASE_TIME = now
        total_cost = denomination * quantity

        logger.info(f"⚡ SNIPER ACTIVATED: Instant buy triggered for PlayStation ₹{denomination:,} x {quantity} (₹{total_cost:,})!")

        # 1. Alert Telegram admins that sniper has fired
        await dispatch_telegram_broadcast(
            bot,
            f"⚡ <b>RESTOCK SIGNAL RECEIVED FROM MONITOR!</b>\n\n"
            f"🎯 <b>Target:</b> PlayStation ₹{denomination:,} x {quantity} cards (₹{total_cost:,})\n"
            f"🛒 <i>Opening Rooter and executing instant purchase now...</i>"
        )

        async def progress_reporter(text: str, emoji: str):
            logger.info(f"[Sniper Progress] {emoji} {text}")

        # 2. Execute Purchase
        result = await rooter_service.execute_purchase(
            denomination=denomination,
            quantity=quantity,
            simulation=config.SIMULATION_MODE,
            progress_callback=progress_reporter,
        )

        # 3. Report Results
        if result.success:
            codes_text = ""
            if result.codes:
                codes_list = [f"<code>{c}</code>" for c in result.codes]
                codes_text = "\n".join([f"• Voucher Code {i+1}: {c}" for i, c in enumerate(codes_list)])
            elif result.voucher_code:
                codes_text = f"• <b>Voucher Code:</b> <code>{result.voucher_code}</code>"

            pin_text = f"\n• <b>Voucher PIN:</b> <code>{result.voucher_pin}</code>" if result.voucher_pin else ""

            celebration_msg = (
                f"🎉 <b>PURCHASE COMPLETED SUCCESSFULLY!</b>\n\n"
                f"🎮 <b>Platform:</b> Rooter Shop\n"
                f"💳 <b>Item:</b> PlayStation Gift Card ₹{denomination:,}\n"
                f"📦 <b>Quantity:</b> {quantity} cards (Total: ₹{total_cost:,})\n"
                f"🆔 <b>Order ID:</b> <code>{result.order_id}</code>\n\n"
                f"{codes_text}{pin_text}\n\n"
                f"💡 <i>Tip: Tap the code above to copy it instantly and redeem on PlayStation Store!</i>"
            )
            logger.info(f"✅ Purchase Succeeded! Order ID: {result.order_id}")
            await dispatch_telegram_broadcast(bot, celebration_msg, photo_path=result.error_screenshot)

        else:
            fail_msg = (
                f"⚠️ <b>Purchase Could Not Complete</b>\n\n"
                f"• <b>Target:</b> PlayStation ₹{denomination:,} x {quantity}\n"
                f"• <b>Reason:</b> {result.message}\n\n"
                f"<i>Bot remains on standby for the next restock cycle.</i>"
            )
            logger.warning(f"❌ Purchase Failed: {result.message}")
            await dispatch_telegram_broadcast(bot, fail_msg, photo_path=result.error_screenshot)


def create_webhook_app(tg_bot) -> web.Application:
    """Builds the aiohttp webhook application for inter-bot communication."""
    app = web.Application()

    async def handle_stock_webhook(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        secret = data.get("secret", "").strip()
        if secret != config.INTERNAL_SECRET:
            logger.warning(f"Unauthorized webhook attempt with secret: '{secret}'")
            return web.json_response({"error": "Unauthorized"}, status=401)

        denomination = int(data.get("denomination", config.TARGET_DENOMINATION))
        quantity = int(data.get("quantity", config.TARGET_QUANTITY))

        # Launch purchase task in background
        asyncio.create_task(trigger_instant_snipe(denomination, quantity, tg_bot))

        return web.json_response({
            "status": "accepted",
            "message": f"Sniper triggered for ₹{denomination} x {quantity}",
            "simulation": config.SIMULATION_MODE,
        })

    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "bot": "psn-buy-bot",
            "has_session": browser_manager.has_session(),
            "target": {
                "denomination": config.TARGET_DENOMINATION,
                "quantity": config.TARGET_QUANTITY,
            },
            "simulation_mode": config.SIMULATION_MODE,
            "port": config.WEBHOOK_PORT,
        })

    app.router.add_post("/webhook/stock", handle_stock_webhook)
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    return app
