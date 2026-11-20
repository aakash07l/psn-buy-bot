"""
Multi-Account Manager for Rooter Gift Card Sniper Bot.
Supports managing up to 50 Rooter accounts simultaneously.
Handles Bearer tokens, browser storage states, live wallet balance synchronization, and account toggling.
Persists data to sessions/accounts.json and sessions/accounts/acc_{id}.json.
"""

import json
import logging
import base64
import asyncio
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import httpx

import config

logger = logging.getLogger(__name__)

ACCOUNTS_FILE = config.SESSION_DIR / "accounts.json"
ROOTER_WALLET_BALANCE_URL = config.ROOTER_WALLET_BALANCE_URL


def extract_user_id(token: str) -> int:
    """Extracts Rooter userId from JWT Bearer token payload."""
    try:
        clean = token.strip()
        if clean.lower().startswith("bearer "):
            clean = clean[7:].strip()
        parts = clean.split(".")
        if len(parts) >= 2:
            padding = "=" * (-len(parts[1]) % 4)
            payload_str = base64.urlsafe_b64decode(parts[1] + padding).decode("utf-8")
            data = json.loads(payload_str)
            return int(data.get("userId") or data.get("sub") or data.get("id") or 0)
    except Exception:
        pass
    return 0


def generate_storage_state_for_token(token: str, user_id: int = 0) -> Dict[str, Any]:
    """Generates a valid Playwright storage_state structure for a Rooter Bearer token including user_auth."""
    clean_token = token.strip()
    if clean_token.lower().startswith("bearer "):
        clean_token = clean_token[7:].strip()

    if not user_id:
        user_id = extract_user_id(clean_token)

    auth_payload = {
        "accessToken": clean_token,
        "refreshToken": clean_token,
        "token": clean_token,
    }
    if user_id:
        auth_payload["userId"] = user_id

    auth_json_str = json.dumps(auth_payload)

    cookies = [
        {
            "name": "user_auth",
            "value": auth_json_str,
            "domain": ".rooter.gg",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        },
        {
            "name": "token",
            "value": clean_token,
            "domain": ".rooter.gg",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        },
        {
            "name": "auth_token",
            "value": clean_token,
            "domain": ".rooter.gg",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        },
    ]

    local_storage = [
        {"name": "token", "value": clean_token},
        {"name": "auth_token", "value": clean_token},
        {"name": "user_auth", "value": auth_json_str},
    ]
    if user_id:
        local_storage.append({"name": "user_id", "value": str(user_id)})

    return {
        "cookies": cookies,
        "origins": [
            {
                "origin": "https://shop.rooter.gg",
                "localStorage": local_storage,
            }
        ],
    }


async def fetch_live_balance_for_token(token: str) -> Optional[float]:
    """Fetches real-time live wallet balance directly from Rooter payments API using Bearer token."""
    clean_tok = token.strip()
    if clean_tok.lower().startswith("bearer "):
        clean_tok = clean_tok[7:].strip()
    if not clean_tok:
        return None

    headers = {
        "Authorization": f"Bearer {clean_tok}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Referer": "https://shop.rooter.gg/",
        "Origin": "https://shop.rooter.gg",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(ROOTER_WALLET_BALANCE_URL, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                comp = data.get("component", {})
                dep = float(comp.get("deposit", 0.0) or 0.0)
                cred = float(comp.get("credit", 0.0) or 0.0)
                vouch = float(comp.get("voucher", 0.0) or 0.0)
                total = round(dep + cred + vouch, 2)
                logger.info(f"Rooter live balance fetched: ₹{total:.2f} (dep={dep}, cred={cred}, vouch={vouch})")
                return total
            else:
                logger.warning(f"Rooter balance API status {resp.status_code}: {resp.text[:120]}")
    except Exception as e:
        logger.warning(f"Failed to fetch live Rooter balance: {e}")
    return None


class AccountManager:
    """Manages up to 50 Rooter user accounts for concurrent restock sniping."""

    def __init__(self, filepath: Path = ACCOUNTS_FILE):
        self.filepath = filepath
        self.accounts: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        """Loads accounts from sessions/accounts.json and merges any environment seeds without duplicates."""
        loaded_accounts: Dict[str, Dict[str, Any]] = {}

        # 1. Load existing accounts from disk
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for a in data.get("accounts", []):
                        aid = a.get("id") or a.get("token") or str(a.get("user_id"))
                        if aid:
                            loaded_accounts[aid] = a
            except Exception as e:
                logger.warning(f"Failed to read {self.filepath}: {e}")

        # 2. Check environment tokens if present (seed without overwriting existing accounts)
        env_tokens = getattr(config, "ROOTER_AUTH_TOKENS", [])
        single = getattr(config, "ROOTER_AUTH_TOKEN", "")
        if single and len(single.strip()) > 20:
            env_tokens.append(single.strip())

        for idx, tok in enumerate(env_tokens, start=1):
            clean_tok = tok.strip()
            if clean_tok.lower().startswith("bearer "):
                clean_tok = clean_tok[7:].strip()
            uid = extract_user_id(clean_tok)
            # check if token already in loaded accounts
            already_exists = any(a.get("token") == clean_tok or (uid and a.get("user_id") == uid) for a in loaded_accounts.values())
            if not already_exists and len(loaded_accounts) < config.MAX_ACCOUNTS:
                acc_id = f"acc_{idx}"
                loaded_accounts[acc_id] = {
                    "id": acc_id,
                    "label": f"Account {idx}",
                    "token": clean_tok,
                    "user_id": uid,
                    "phone": None,
                    "wallet_balance": 0.0,
                    "is_active": True,
                    "created_at": datetime.now().isoformat(),
                    "storage_state_path": str(config.ACCOUNTS_DIR / f"{acc_id}.json"),
                }
                # Also generate storage state file for this token
                st_data = generate_storage_state_for_token(clean_tok, uid)
                st_path = config.ACCOUNTS_DIR / f"{acc_id}.json"
                try:
                    with open(st_path, "w", encoding="utf-8") as f:
                        json.dump(st_data, f, indent=2)
                except Exception:
                    pass

        # 3. Ensure all loaded accounts have up-to-date storage_state with user_auth cookie
        for acc in loaded_accounts.values():
            tok = acc.get("token")
            uid = acc.get("user_id") or extract_user_id(tok)
            st_path_str = acc.get("storage_state_path")
            if tok and st_path_str:
                st_path = Path(st_path_str)
                try:
                    st_data = generate_storage_state_for_token(tok, uid)
                    st_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(st_path, "w", encoding="utf-8") as f:
                        json.dump(st_data, f, indent=2)
                except Exception:
                    pass

        self.accounts = list(loaded_accounts.values())
        self._save()
        logger.info(f"AccountManager initialized with {len(self.accounts)} account(s).")

    def _save(self):
        """Saves current accounts list atomically to JSON file."""
        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump({"accounts": self.accounts}, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save accounts to {self.filepath}: {e}")

    def get_all(self) -> List[Dict[str, Any]]:
        """Returns all registered accounts."""
        return list(self.accounts)

    def get_active(self) -> List[Dict[str, Any]]:
        """Returns all currently active accounts (armed for restock sniping)."""
        return [acc for acc in self.accounts if acc.get("is_active", True)]

    def get_account(self, acc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single account by ID, UID, or index."""
        target = str(acc_id).strip().lower()
        for acc in self.accounts:
            aid = str(acc.get("id", "")).lower()
            uid = str(acc.get("user_id", "")).lower()
            if aid == target or uid == target or aid == f"acc_{target}":
                return acc
        return None

    def _get_next_id(self) -> str:
        """Finds the next sequential acc_N identifier."""
        indices = []
        for a in self.accounts:
            aid = str(a.get("id", ""))
            if aid.startswith("acc_"):
                try:
                    indices.append(int(aid[4:]))
                except ValueError:
                    pass
        next_val = max(indices, default=0) + 1
        return f"acc_{next_val}"

    async def add_account_token(
        self,
        token: str,
        phone: Optional[str] = None,
        label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Adds or updates an account using a JWT Bearer token / Cookie string.
        Fetches real-time live wallet balance and generates Playwright storage_state.
        """
        clean_token = token.strip()
        if clean_token.lower().startswith("bearer "):
            clean_token = clean_token[7:].strip()
        # If input was cookie format: user_auth=xxx; or token=xxx; or auth_token=xxx
        uid = 0
        if "user_auth=" in clean_token or "token=" in clean_token:
            import urllib.parse
            for part in clean_token.split(";"):
                part = part.strip()
                if part.startswith("user_auth="):
                    val = urllib.parse.unquote(part[10:].strip())
                    try:
                        u_obj = json.loads(val)
                        if isinstance(u_obj, dict):
                            clean_token = u_obj.get("accessToken") or u_obj.get("token") or clean_token
                            if u_obj.get("userId"):
                                uid = int(u_obj.get("userId"))
                    except Exception:
                        if len(val) > 20:
                            clean_token = val
                    break
                elif part.startswith("token="):
                    clean_token = part[6:].strip()
                    break
                elif part.startswith("auth_token="):
                    clean_token = part[11:].strip()
                    break

        if not uid:
            uid = extract_user_id(clean_token)

        # Check if account already exists by token or userId
        for acc in self.accounts:
            if acc.get("token") == clean_token or (uid and acc.get("user_id") == uid):
                acc["token"] = clean_token
                if phone:
                    acc["phone"] = phone
                if label:
                    acc["label"] = label
                acc["is_active"] = True

                # Fetch fresh balance
                bal = await fetch_live_balance_for_token(clean_token)
                if bal is not None:
                    acc["wallet_balance"] = bal

                # Update storage state file
                st_data = generate_storage_state_for_token(clean_token, uid)
                st_path = Path(acc.get("storage_state_path") or (config.ACCOUNTS_DIR / f"{acc['id']}.json"))
                with open(st_path, "w", encoding="utf-8") as f:
                    json.dump(st_data, f, indent=2)
                acc["storage_state_path"] = str(st_path)

                self._save()
                logger.info(f"Updated existing account {acc['id']} (UID: {uid}, Balance: ₹{acc.get('wallet_balance')})")
                return acc

        # Create new account
        new_id = self._get_next_id()
        idx_num = new_id.replace("acc_", "")
        acc_label = label or (f"Account {idx_num} (+91 {phone[-10:]})" if phone else f"Account {idx_num} (UID: {uid or 'N/A'})")

        bal = await fetch_live_balance_for_token(clean_token)
        wallet_balance = bal if bal is not None else 0.0

        st_path = config.ACCOUNTS_DIR / f"{new_id}.json"
        st_data = generate_storage_state_for_token(clean_token, uid)
        with open(st_path, "w", encoding="utf-8") as f:
            json.dump(st_data, f, indent=2)

        new_acc = {
            "id": new_id,
            "label": acc_label,
            "token": clean_token,
            "user_id": uid,
            "phone": phone,
            "wallet_balance": wallet_balance,
            "is_active": True,
            "created_at": datetime.now().isoformat(),
            "storage_state_path": str(st_path),
        }

        self.accounts.append(new_acc)
        self._save()
        logger.info(f"Added new account {new_id} ({acc_label}, Balance: ₹{wallet_balance})")
        return new_acc

    async def add_account_from_context(
        self,
        context: Any,
        phone: Optional[str] = None,
        label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Adds or updates an account after successful Playwright Phone + OTP login.
        Saves the browser storage_state directly and extracts token/cookies.
        """
        new_id = self._get_next_id()
        st_path = config.ACCOUNTS_DIR / f"{new_id}.json"

        # Save Playwright storage state
        await context.storage_state(path=str(st_path))

        # Also update global rooter_storage_state.json for backwards compatibility
        await context.storage_state(path=str(config.STORAGE_STATE_FILE))

        # Inspect storage_state for tokens
        token = ""
        uid = 0
        try:
            with open(st_path, "r", encoding="utf-8") as f:
                st_data = json.load(f)
                # Check cookies
                for c in st_data.get("cookies", []):
                    cname = c.get("name", "").lower()
                    if cname in ("token", "auth_token", "jwt"):
                        token = c.get("value", "")
                        break
                    elif cname == "user_auth":
                        try:
                            val = json.loads(c.get("value", "{}"))
                            token = val.get("accessToken") or val.get("token") or token
                            if val.get("userId"):
                                uid = int(val.get("userId"))
                        except Exception:
                            pass
                # Check origins localStorage
                if not token:
                    for orig in st_data.get("origins", []):
                        for item in orig.get("localStorage", []):
                            iname = item.get("name", "").lower()
                            if iname in ("token", "auth_token", "jwt"):
                                token = item.get("value", "")
                                break
        except Exception as e:
            logger.warning(f"Could not parse storage state file {st_path}: {e}")

        if token:
            uid = extract_user_id(token)

        idx_num = new_id.replace("acc_", "")
        clean_phone = phone[-10:] if phone else None
        acc_label = label or (f"Account {idx_num} (+91 {clean_phone})" if clean_phone else f"Account {idx_num}")

        # Fetch balance
        wallet_balance = 0.0
        if token:
            bal = await fetch_live_balance_for_token(token)
            if bal is not None:
                wallet_balance = bal

        new_acc = {
            "id": new_id,
            "label": acc_label,
            "token": token,
            "user_id": uid,
            "phone": clean_phone,
            "wallet_balance": wallet_balance,
            "is_active": True,
            "created_at": datetime.now().isoformat(),
            "storage_state_path": str(st_path),
        }

        self.accounts.append(new_acc)
        self._save()
        logger.info(f"Added OTP account {new_id} ({acc_label}, Balance: ₹{wallet_balance})")
        return new_acc

    def toggle_active(self, acc_id: str) -> Optional[bool]:
        """Toggles active/paused status of an account."""
        acc = self.get_account(acc_id)
        if acc:
            acc["is_active"] = not acc.get("is_active", True)
            self._save()
            logger.info(f"Toggled account {acc['id']} active -> {acc['is_active']}")
            return acc["is_active"]
        return None

    def remove_account(self, acc_id: str) -> bool:
        """Removes an account by ID and deletes its storage state file."""
        acc = self.get_account(acc_id)
        if not acc:
            return False

        # Clean up storage state file
        if acc.get("storage_state_path"):
            try:
                p = Path(acc["storage_state_path"])
                if p.exists():
                    p.unlink()
            except Exception:
                pass

        self.accounts = [a for a in self.accounts if a.get("id") != acc["id"]]
        self._save()
        logger.info(f"Removed account {acc['id']} ({acc.get('label')})")
        return True

    def deduct_balance(self, acc_id: str, amount: float) -> bool:
        """Deducts purchased amount from account's tracked wallet balance."""
        acc = self.get_account(acc_id)
        if acc:
            cur = float(acc.get("wallet_balance", 0.0) or 0.0)
            acc["wallet_balance"] = max(0.0, round(cur - amount, 2))
            self._save()
            logger.info(f"Deducted ₹{amount:.2f} from {acc['id']} (New balance: ₹{acc['wallet_balance']:.2f})")
            return True
        return False

    def get_total_balance(self) -> float:
        """Returns the sum of wallet balances across all active accounts."""
        return sum(float(a.get("wallet_balance", 0.0) or 0.0) for a in self.get_active())

    async def sync_all_balances(self) -> Dict[str, float]:
        """Fetches real-time live balance for all registered accounts concurrently."""
        tasks = []
        for acc in self.accounts:
            tok = acc.get("token", "")
            if tok:
                tasks.append((acc, fetch_live_balance_for_token(tok)))

        if not tasks:
            return {}

        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
        updated = {}
        for (acc, _), res in zip(tasks, results):
            if isinstance(res, (int, float)):
                acc["wallet_balance"] = float(res)
                updated[acc["id"]] = float(res)
            else:
                updated[acc["id"]] = float(acc.get("wallet_balance", 0.0) or 0.0)

        self._save()
        return updated


# Global singleton instance
account_manager = AccountManager()
