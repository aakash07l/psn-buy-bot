"""
Telegram inline keyboards for the PlayStation Sniper & Auto-Buy Bot (Bot 2).
Provides interactive menus for accounts management, sniper control, and login methods.
"""

from typing import List, Dict, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
from services.webhook_server import is_sniper_active
from services.account_manager import account_manager


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Builds the main dashboard keyboard."""
    sniper_label = "🟢 Sniper: ARMED" if is_sniper_active() else "⏸️ Sniper: PAUSED"
    sim_label = "🧪 Sim: ON" if config.SIMULATION_MODE else "⚡ Live: REAL"
    total_active = len(account_manager.get_active())

    keyboard = [
        [
            InlineKeyboardButton(sniper_label, callback_data="toggle_sniper_arm"),
            InlineKeyboardButton("💰 Check Balances", callback_data="cmd_balance"),
        ],
        [
            InlineKeyboardButton(f"👥 Accounts ({total_active} Active)", callback_data="show_accounts_menu"),
            InlineKeyboardButton("🛒 Test Buy", callback_data="cmd_manual_buy"),
        ],
        [
            InlineKeyboardButton("🔑 Add Account", callback_data="choose_login_type"),
            InlineKeyboardButton(sim_label, callback_data="toggle_sim"),
        ],
        [
            InlineKeyboardButton("📊 System Status", callback_data="cmd_status"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_login_options_keyboard() -> InlineKeyboardMarkup:
    """Keyboard to choose between Cookie/Token paste and Phone+OTP login."""
    keyboard = [
        [
            InlineKeyboardButton("🔑 Paste Cookie / Token", callback_data="login_method_cookie"),
            InlineKeyboardButton("📱 Phone + SMS OTP", callback_data="login_method_otp"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_accounts_keyboard(accounts: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Builds interactive account list with Toggle, Delete, and Refresh options."""
    keyboard = []

    # Individual account toggles (show up to 15 per view, scrollable/manageable)
    for acc in accounts[:15]:
        aid = acc.get("id", "")
        idx = aid.replace("acc_", "")
        is_active = acc.get("is_active", True)
        badge = "🟢" if is_active else "⏸️"
        label = acc.get("label", f"Acc {idx}")
        # Truncate label for button
        short_label = (label[:16] + "..") if len(label) > 18 else label

        keyboard.append([
            InlineKeyboardButton(f"{badge} {short_label}", callback_data=f"toggle_acc_{aid}"),
            InlineKeyboardButton("❌ Del", callback_data=f"del_acc_{aid}"),
        ])

    keyboard.append([
        InlineKeyboardButton("➕ Add Cookie/Token", callback_data="login_method_cookie"),
        InlineKeyboardButton("📱 Add Phone+OTP", callback_data="login_method_otp"),
    ])
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh All Balances", callback_data="refresh_all_acc_balances"),
        InlineKeyboardButton("🔙 Main Menu", callback_data="cmd_start_menu"),
    ])

    return InlineKeyboardMarkup(keyboard)


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Provides a cancel button for conversation handlers."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
    ])
