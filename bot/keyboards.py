"""
Telegram inline keyboards for the PlayStation Sniper & Auto-Buy Bot (Bot 2).
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Builds the main dashboard keyboard."""
    sim_status = "🟢 ON" if config.SIMULATION_MODE else "🔴 OFF"
    keyboard = [
        [
            InlineKeyboardButton(f"🎯 Target: ₹{config.TARGET_DENOMINATION:,} x {config.TARGET_QUANTITY}", callback_data="show_target_info"),
            InlineKeyboardButton("💰 Check Balance", callback_data="cmd_balance"),
        ],
        [
            InlineKeyboardButton("🛒 Test Buy (Instant)", callback_data="cmd_manual_buy"),
            InlineKeyboardButton(f"🧪 Sim Mode: {sim_status}", callback_data="toggle_sim"),
        ],
        [
            InlineKeyboardButton("🔑 Rooter Login (OTP)", callback_data="start_login_flow"),
            InlineKeyboardButton("📊 System Status", callback_data="cmd_status"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Provides a cancel button for conversation handlers."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
    ])
