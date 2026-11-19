"""
Internal Webhook Server for PlayStation Sniper Bot (Bot 2).
Listens on localhost (port 5000) for instant restock triggers from Stock Monitor Bot (Bot 1).
Supports multi-account simultaneous execution, balance verification, and sniper arming/pausing.
"""

import sys
import os
import time
import asyncio
import logging
from typing import Optional, Dict, Any, List

from aiohttp import web
from telegram.constants import ParseMode

import config
from services.rooter_service import rooter_service
from services.browser_manager import browser_manager
from services.account_manager import account_manager

logger = logging.getLogger("BuyBotWebhook")

# Concurrency lock to prevent duplicate orders if restock triggers fire repeatedly
PURCHASE_LOCK = asyncio.Lock()
LAST_PURCHASE_TIME: float = 0.0

# Global Sniper Active Flag (armed vs paused)
SNIPER_ACTIVE: bool = getattr(config, "SNIPER_ACTIVE", True)


def is_sniper_active() -> bool:
    """Returns True if the auto-buy sniper is currently armed."""
    return SNIPER_ACTIVE


def set_sniper_active(active: bool):
    """Sets the sniper armed status."""
    global SNIPER_ACTIVE
    SNIPER_ACTIVE = active
    config.SNIPER_ACTIVE = active
    logger.info(f"Sniper active status updated -> {SNIPER_ACTIVE}")


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
    Executes simultaneous multi-account restock purchases.
    Triggered via internal webhook from Bot 1 or manually.
    """
    global LAST_PURCHASE_TIME

    if not is_sniper_active():
        logger.info("⏸️ Restock signal received, but Sniper is currently PAUSED (/snipe to arm). Skipping buy.")
        await dispatch_telegram_broadcast(
            bot,
            f"⏸️ <b>RESTOCK DETECTED (Sniper Paused)</b>\n\n"
            f"Stock detected for PlayStation ₹{denomination:,} x {quantity} cards, but auto-buy is currently <b>PAUSED</b>.\n"
            f"<i>Type /snipe to arm the sniper for instant auto-purchasing.</i>"
        )
        return

    if PURCHASE_LOCK.locked():
        logger.warning(f"Purchase already in progress! Skipping duplicate trigger for ₹{denomination:,} x {quantity}.")
        return

    async with PURCHASE_LOCK:
        now = time.time()
        # Cooldown guard: do not repeat purchase within 25 seconds
        if (now - LAST_PURCHASE_TIME) < 25:
            logger.info("Purchase cooldown active (25s). Skipping redundant restock signal.")
            return

        LAST_PURCHASE_TIME = now
        total_cost_per_acc = config.get_card_total_cost(denomination, quantity)
        single_card_cost = config.get_card_total_cost(denomination, 1)

        # Fetch all active accounts
        active_accounts = account_manager.get_active()

        # If no multi-accounts registered, check fallback session
        if not active_accounts:
            if browser_manager.has_session():
                logger.info("No multi-accounts found in accounts.json. Using default session.")
                active_accounts = [{
                    "id": "acc_default",
                    "label": "Default Session Account",
                    "wallet_balance": 999999.0 if config.SIMULATION_MODE else 0.0,
                    "storage_state_path": str(config.STORAGE_STATE_FILE),
                }]
            else:
                logger.warning("No active accounts registered for purchase!")
                await dispatch_telegram_broadcast(
                    bot,
                    f"⚠️ <b>RESTOCK SIGNAL RECEIVED, BUT NO ACCOUNTS READY!</b>\n\n"
                    f"🎯 Target: PlayStation ₹{denomination:,} x {quantity}\n"
                    f"❌ No active Rooter accounts found. Please use /accounts or /login to connect your account(s)."
                )
                return

        logger.info(f"⚡ SNIPER TRIGGERED: Evaluating {len(active_accounts)} active account(s) for ₹{denomination:,} x {quantity} (Total with fees: ₹{total_cost_per_acc:,}/acc)...")

        # 1. Pre-flight Balance Verification (with automatic fee accounting & 1-card fallback)
        eligible_accounts: List[tuple] = []
        insufficient_accounts: List[Dict[str, Any]] = []

        if not config.SIMULATION_MODE and not getattr(config, "DISABLE_BALANCE_CHECK", False):
            # Sync live balances in parallel right before checkout
            try:
                await account_manager.sync_all_balances()
            except Exception as e:
                logger.warning(f"Pre-flight sync error: {e}")

            for acc in active_accounts:
                bal = float(acc.get("wallet_balance", 0.0) or 0.0)
                acc_label = acc.get("label") or acc.get("id")

                if bal >= total_cost_per_acc:
                    eligible_accounts.append((acc, quantity))
                elif quantity > 1 and bal >= single_card_cost:
                    # Account has enough for at least 1 card! Auto-adjust quantity so user never misses restock
                    eligible_accounts.append((acc, 1))
                    logger.info(f"[{acc_label}] Auto-adjusted quantity from {quantity} to 1 (Balance: ₹{bal:,.2f})")
                else:
                    insufficient_accounts.append(acc)
                    fee = single_card_cost - denomination
                    await dispatch_telegram_broadcast(
                        bot,
                        f"⚠️ <b>[Account: {acc_label}] Not enough balance to place order!</b>\n\n"
                        f"• <b>Available Balance:</b> ₹{bal:,.2f}\n"
                        f"• <b>Required Amount:</b> ₹{single_card_cost:,.2f} (Card: ₹{denomination:,} + Rooter Fee: ₹{fee:,.2f})\n"
                        f"• <b>Status:</b> ❌ Order skipped for this account."
                    )
        else:
            eligible_accounts = [(acc, quantity) for acc in active_accounts]

        if not eligible_accounts:
            logger.warning("No accounts have sufficient balance to place the order.")
            await dispatch_telegram_broadcast(
                bot,
                f"⚠️ <b>ALL PURCHASES ABORTED:</b> None of the {len(active_accounts)} active account(s) have enough balance for PlayStation ₹{denomination:,} (Total: ₹{single_card_cost:,})."
            )
            return

        # 2. Announce purchase firing for eligible accounts
        acc_names = ", ".join([f"{a.get('label', a['id'])} (x{q})" for a, q in eligible_accounts])
        await dispatch_telegram_broadcast(
            bot,
            f"⚡ <b>SNIPER ACTIVATED FOR {len(eligible_accounts)} ACCOUNT(S)!</b>\n\n"
            f"🎯 <b>Target:</b> PlayStation ₹{denomination:,}\n"
            f"👥 <b>Buying on:</b> {acc_names}\n"
            f"🛒 <i>Executing simultaneous checkout now...</i>"
        )

        # 3. Execute Simultaneous Purchases Concurrently
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_BROWSERS)

        async def purchase_single(acc_item: tuple):
            acc, acc_qty = acc_item
            async with semaphore:
                try:
                    return await rooter_service.execute_purchase(
                        denomination=denomination,
                        quantity=acc_qty,
                        simulation=config.SIMULATION_MODE,
                        account=acc,
                    )
                except Exception as ex:
                    logger.error(f"Purchase crashed for {acc.get('id')}: {ex}", exc_info=True)
                    from services.base_platform import PurchaseResult
                    return PurchaseResult(
                        success=False,
                        platform="rooter",
                        denomination=denomination,
                        quantity=acc_qty,
                        message=f"Purchase exception: {str(ex)}",
                    )

        tasks = [purchase_single(item) for item in eligible_accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 4. Process and Broadcast Purchase Results
        for (acc, acc_qty), res in zip(eligible_accounts, results):
            acc_label = acc.get("label") or acc.get("id")
            acc_total = config.get_card_total_cost(denomination, acc_qty)
            if isinstance(res, Exception):
                await dispatch_telegram_broadcast(
                    bot,
                    f"❌ <b>Purchase Error on {acc_label}</b>\n\n"
                    f"• Target: PlayStation ₹{denomination:,} x {acc_qty}\n"
                    f"• Error: <code>{str(res)}</code>"
                )
            elif res.success:
                codes_text = ""
                if res.codes:
                    codes_list = [f"<code>{c}</code>" for c in res.codes]
                    codes_text = "\n".join([f"• Voucher Code {i+1}: {c}" for i, c in enumerate(codes_list)])
                elif res.voucher_code:
                    codes_text = f"• <b>Voucher Code:</b> <code>{res.voucher_code}</code>"

                pin_text = f"\n• <b>Voucher PIN:</b> <code>{res.voucher_pin}</code>" if res.voucher_pin else ""

                celebration_msg = (
                    f"🎉 <b>PURCHASE COMPLETED SUCCESSFULLY!</b>\n\n"
                    f"👤 <b>Account:</b> {acc_label}\n"
                    f"🎮 <b>Platform:</b> Rooter Shop\n"
                    f"💳 <b>Item:</b> PlayStation Gift Card ₹{denomination:,}\n"
                    f"📦 <b>Quantity:</b> {acc_qty} card(s) (Total Paid: ₹{acc_total:,})\n"
                    f"🆔 <b>Order ID:</b> <code>{res.order_id}</code>\n\n"
                    f"{codes_text}{pin_text}\n\n"
                    f"💡 <i>Tip: Tap any code above to copy it instantly and redeem on PS Store!</i>"
                )
                logger.info(f"✅ Purchase Succeeded for {acc_label}! Order ID: {res.order_id}")
                await dispatch_telegram_broadcast(bot, celebration_msg, photo_path=res.error_screenshot)
            else:
                fail_msg = (
                    f"⚠️ <b>Purchase Failed on {acc_label}</b>\n\n"
                    f"• <b>Target:</b> PlayStation ₹{denomination:,} x {acc_qty}\n"
                    f"• <b>Reason:</b> {res.message}\n\n"
                    f"<i>Bot remains on standby for the next restock cycle.</i>"
                )
                logger.warning(f"❌ Purchase Failed for {acc_label}: {res.message}")
                await dispatch_telegram_broadcast(bot, fail_msg, photo_path=res.error_screenshot)


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

        # Launch multi-account purchase task in background
        asyncio.create_task(trigger_instant_snipe(denomination, quantity, tg_bot))

        return web.json_response({
            "status": "accepted",
            "message": f"Sniper triggered for ₹{denomination} x {quantity}",
            "simulation": config.SIMULATION_MODE,
            "sniper_active": is_sniper_active(),
            "active_accounts": len(account_manager.get_active()),
        })

    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "bot": "psn-buy-bot",
            "sniper_active": is_sniper_active(),
            "active_accounts": len(account_manager.get_active()),
            "total_accounts": len(account_manager.get_all()),
            "total_balance": account_manager.get_total_balance(),
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
