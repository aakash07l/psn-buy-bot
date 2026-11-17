# ⚡ PlayStation Sniper & Auto-Buy Bot (Bot 2)

High-speed restock sniper for **Rooter Shop PlayStation Gift Cards**.
Specifically buys **₹1,000 PlayStation Cards x 2 Quantity (Total: ₹2,000)** upon restock signal.

## 🛡️ Anti-Ban Zero-Polling Architecture
- **0 Rooter Requests while Idle:** Unlike traditional scrapers, this bot **NEVER polls or visits Rooter repeatedly** while waiting for stock.
- Rooter's anti-fraud systems will see **ZERO bot activity** from your funded account.
- It sits silently in Standby mode and only wakes up in `< 2ms` when **Stock Monitor Bot (Bot 1)** sends an authenticated local HTTP signal (`POST http://127.0.0.1:5000/webhook/stock`).

---

## 🔑 No Cookies! Phone + OTP Login via Telegram
No more copying cookies or JWT tokens manually:
1. Open Telegram chat with the Buy Bot.
2. Send `/login`.
3. Enter your 10-digit Rooter mobile number.
4. Reply with the SMS OTP received on your phone.
5. The bot securely saves your session state into `sessions/rooter_storage_state.json` and goes on Standby!

---

## ⚙️ Configuration (`.env`)
```ini
BOT_TOKEN=9001018100:AAHMhA03MsTgcwsThqJeYTKYeNhnMQSDwp0
ADMIN_CHAT_IDS=5701727023,1988639571
TARGET_DENOMINATION=1000
TARGET_QUANTITY=2
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=5000
INTERNAL_SECRET=psn_sniper_secret_key_2026
SIMULATION_MODE=False
DELIVERY_EMAIL=
HEADLESS=True
```

---

## 🤖 Telegram Commands
- `/start` - Dashboard overview & standby status.
- `/login` - Interactive Rooter login with Phone + OTP (no cookies).
- `/status` - Bot health, target (₹1k x 2), session validity.
- `/balance` - Check live Rooter wallet balance.
- `/buy` - Execute a manual purchase test.
- `/sim` - Toggle dry-run / simulation mode (safe testing).
- `/set_target <denomination> [qty]` - Update target voucher and quantity.
- `/set_email <email>` - Configure delivery email for order checkout.

---

## 🏃 Run Locally or on Server
```bash
pip install -r requirements.txt
playwright install chromium
python main.py
```
