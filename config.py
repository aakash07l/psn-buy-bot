"""
Configuration settings for PlayStation Sniper & Auto-Buy Bot (Bot 2).
Handles Telegram Bot Token, Webhook Receiver, Rooter Automation, and User Sessions.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory paths
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Telegram Bot Token (Buy Bot)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN or "AAFS9F" in BOT_TOKEN:
    BOT_TOKEN = "9001018100:AAHMhA03MsTgcwsThqJeYTKYeNhnMQSDwp0"

# Authorized Admin / User Chat IDs (Aakash & Ajay)
DEFAULT_ADMIN_CHAT_IDS = [5701727023, 1988639571]
raw_admin_ids = os.getenv("ADMIN_CHAT_IDS", "").strip()
if raw_admin_ids:
    ADMIN_CHAT_IDS = [
        int(x.strip()) for x in raw_admin_ids.split(",")
        if x.strip().lstrip("-").isdigit()
    ]
else:
    ADMIN_CHAT_IDS = list(DEFAULT_ADMIN_CHAT_IDS)

# ==========================================
# 2. Sniper Purchase Targets
# ==========================================
# Default Target: ₹1,000 PlayStation Card
TARGET_DENOMINATION = int(os.getenv("TARGET_DENOMINATION", "1000"))
# Target Quantity to buy: 1 card by default (User can change via /set_target)
TARGET_QUANTITY = int(os.getenv("TARGET_QUANTITY", "1"))

# Rooter card prices including platform convenience fees (from Rooter checkout)
ROOTER_CARD_PRICES = {
    1000: 1206.0,
    2000: 2359.0,
    3000: 3509.0,
    4000: 4659.0,
    5000: 5810.0,
}

def get_card_total_cost(denomination: int, quantity: int = 1) -> float:
    """Returns exact Rooter billing cost including convenience fees."""
    base_price = ROOTER_CARD_PRICES.get(denomination, float(denomination))
    return round(base_price * quantity, 2)

# Optional: Disable pre-flight balance check if user wants to attempt buy regardless of tracked balance
DISABLE_BALANCE_CHECK = os.getenv("DISABLE_BALANCE_CHECK", "False").lower() in ("true", "1", "yes")

# Simulation / Dry Run mode (safe mode for testing checkout flow without debiting real balance)
SIMULATION_MODE = os.getenv("SIMULATION_MODE", "False").lower() in ("true", "1", "yes")

# Optional Email for Rooter Delivery Details modal
DELIVERY_EMAIL = os.getenv("DELIVERY_EMAIL", "").strip()

# ==========================================
# 3. Webhook Receiver (Listening on localhost:5000 for Bot 1)
# ==========================================
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "psn_sniper_secret_key_2026").strip()

# ==========================================
# 4. Browser & Session Configuration
# ==========================================
HEADLESS = os.getenv("HEADLESS", "True").lower() in ("true", "1", "yes")
BROWSER_TIMEOUT_MS = int(os.getenv("BROWSER_TIMEOUT_MS", "45000"))

SESSION_DIR = BASE_DIR / "sessions"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Path to persisted browser authentication state (created via /login)
STORAGE_STATE_FILE = SESSION_DIR / "rooter_storage_state.json"
ACCOUNTS_DIR = SESSION_DIR / "accounts"
ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)

# Sniper Auto-Buy Active State (Armed / Paused)
SNIPER_ACTIVE = os.getenv("SNIPER_ACTIVE", "True").lower() in ("true", "1", "yes")
MAX_CONCURRENT_BROWSERS = int(os.getenv("MAX_CONCURRENT_BROWSERS", "8"))
MAX_ACCOUNTS = 50

# Rooter URLs
ROOTER_BASE_URL = "https://shop.rooter.gg"
ROOTER_PLAYSTATION_URL = "https://shop.rooter.gg/product/playstation-gift-cards"
ROOTER_WALLET_BALANCE_URL = "https://payments.rooter.io/wallet/balance"
