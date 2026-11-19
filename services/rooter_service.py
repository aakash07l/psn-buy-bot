"""
Rooter Automation Service for PlayStation Gift Cards.
Handles Playwright-based Mobile/Email OTP login, wallet balance checks, and ultra-fast restock sniper checkout.
"""

import os
import re
import time
import json
import asyncio
import logging
from typing import Optional, List, Callable, Awaitable, Tuple, Dict, Any
from playwright.async_api import Page, BrowserContext, TimeoutError as PlaywrightTimeoutError

import config
from services.base_platform import BasePlatform, CardItem, PurchaseResult, ProgressCallback
from services.browser_manager import browser_manager

logger = logging.getLogger(__name__)


class RooterService(BasePlatform):
    """Automation service for Rooter Shop PlayStation voucher purchase and authentication."""

    def __init__(self):
        super().__init__(
            platform_id="rooter",
            name="Rooter",
            base_url=config.ROOTER_BASE_URL
        )
        self.playstation_url = config.ROOTER_PLAYSTATION_URL

    async def check_login_status(self, page: Page) -> bool:
        """Verifies if the browser context is logged into Rooter."""
        try:
            await page.goto(self.base_url, timeout=config.BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)

            # Check if login button is visible
            login_btn = page.locator("button:has-text('Login'), button:has-text('Sign In'), div:has-text('Login / Register')").first
            if await login_btn.is_visible(timeout=2500):
                return False

            # Check for profile icon / avatar / coin balance
            profile = page.locator("div[class*='Profile'], img[alt*='avatar'], div[class*='coin-balance'], a[href*='profile']").first
            return await profile.is_visible(timeout=3000)
        except Exception as e:
            logger.warning(f"Error checking Rooter login status: {e}")
            return False

    async def perform_phone_login(
        self,
        context: BrowserContext,
        page: Page,
        phone_number: str,
        otp_callback: Callable[[], Awaitable[str]],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> bool:
        """
        Interactive login via Mobile Number + SMS OTP.
        No static cookies required. Session is saved to STORAGE_STATE_FILE on success.
        """
        try:
            if progress_callback:
                await progress_callback("Opening Rooter login drawer...", "🔍")

            await page.goto(self.base_url, timeout=config.BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)

            # Click login button
            login_btn = page.locator("button:has-text('Login'), div:has-text('Login'), button:has-text('Sign In')").first
            if await login_btn.is_visible(timeout=4000):
                await login_btn.click()
                await page.wait_for_timeout(1000)

            # Clean and fill 10-digit mobile number
            clean_phone = re.sub(r"\D", "", phone_number)[-10:]
            phone_input = page.locator("input[type='tel'], input[placeholder*='Mobile'], input[name*='phone'], input[placeholder*='number']").first
            if not await phone_input.is_visible(timeout=5000):
                logger.error("Phone number input not found on Rooter login modal.")
                return False

            await phone_input.fill(clean_phone)
            await page.wait_for_timeout(500)

            # Click Get OTP / Continue
            get_otp_btn = page.locator("button:has-text('Get OTP'), button:has-text('Continue'), button:has-text('Next')").first
            await get_otp_btn.click()

            if progress_callback:
                await progress_callback(f"OTP requested for +91 {clean_phone}. Waiting for your OTP...", "📩")

            # Await OTP code from Telegram conversation
            otp_code = await otp_callback()
            if not otp_code:
                logger.error("No OTP provided. Login aborted.")
                return False

            clean_otp = re.sub(r"\D", "", str(otp_code)).strip()

            # Fill OTP inputs (Mantine often uses separate single-char inputs or one numeric input)
            otp_inputs = page.locator("input[type='number'], input[inputmode='numeric'], input[class*='pinInput']")
            count = await otp_inputs.count()
            if count > 1:
                for i in range(min(count, len(clean_otp))):
                    await otp_inputs.nth(i).fill(clean_otp[i])
            else:
                single_input = page.locator("input[placeholder*='OTP'], input[name*='otp'], input[type='tel']").first
                await single_input.fill(clean_otp)

            await page.wait_for_timeout(800)

            # Click Verify / Submit button
            verify_btn = page.locator("button:has-text('Verify'), button:has-text('Submit'), button:has-text('Done')").first
            if await verify_btn.is_visible(timeout=3000):
                await verify_btn.click()

            # Wait for login to complete
            await page.wait_for_timeout(3000)

            # Persist authentication state
            await context.storage_state(path=str(config.STORAGE_STATE_FILE))
            logger.info(f"✅ Rooter session saved successfully to {config.STORAGE_STATE_FILE}")

            if progress_callback:
                await progress_callback("Login successful! Session saved securely.", "✅")

            return True

        except Exception as e:
            logger.error(f"Error during Rooter phone login: {e}", exc_info=True)
            return False

    async def dismiss_location_popup(self, page: Page):
        """Auto-dismisses 'Different Location Detected' modal if it appears."""
        try:
            stay_btn = page.locator("button:has-text('stay on this site'), button:has-text('No, stay on this site'), button:has-text('Stay on this site')").first
            if await stay_btn.is_visible(timeout=1000):
                await stay_btn.click(force=True, timeout=1000)
                logger.info("[Rooter] Dismissed Location popup.")
                await page.wait_for_timeout(300)
        except Exception:
            pass

    async def select_denomination(self, page: Page, denomination: int) -> bool:
        """Selects the specific PlayStation voucher denomination chip (e.g. ₹1,000)."""
        await self.dismiss_location_popup(page)

        # Locator for requested denomination
        opt_locator = page.locator("div, span, p").filter(has_text=re.compile(rf"Rs\.?\s*{denomination}\s*Wallet Code", re.IGNORECASE))
        if await opt_locator.count() == 0:
            opt_locator = page.locator("div, span, p").filter(has_text=re.compile(rf"{denomination}\s*Wallet Code", re.IGNORECASE))

        if await opt_locator.count() > 0:
            chip = opt_locator.first
            try:
                await chip.click(force=True, timeout=2000)
                await page.wait_for_timeout(500)
                return True
            except Exception as e:
                logger.warning(f"Error clicking ₹{denomination} chip: {e}")

        return False

    async def adjust_quantity(self, page: Page, target_quantity: int):
        """Clicks the '+' button to set target quantity (e.g., target 2 -> click '+' once)."""
        if target_quantity <= 1:
            return

        clicks_needed = target_quantity - 1
        logger.info(f"[Rooter] Incrementing quantity by {clicks_needed} click(s)...")

        plus_btn = page.locator(
            "button:has-text('+'), div[class*='qty'] button:has-text('+'), "
            "div[class*='quantity'] button:has-text('+'), [aria-label*='increase']"
        ).first

        for i in range(clicks_needed):
            try:
                if await plus_btn.is_visible(timeout=1500):
                    await plus_btn.click(force=True, timeout=1500)
                    await page.wait_for_timeout(300)
                    logger.info(f"[Rooter] Clicked quantity '+' ({i+1}/{clicks_needed})")
            except Exception as e:
                logger.warning(f"Could not click quantity '+': {e}")

    async def execute_purchase(
        self,
        denomination: int = 1000,
        quantity: int = 2,
        simulation: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
        account: Optional[Dict[str, Any]] = None,
    ) -> PurchaseResult:
        """
        Executes immediate restock purchase for PlayStation cards.
        Supports multi-account authentication, balance verification, and PIN extraction.
        """
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None
        screenshot_path: Optional[str] = None

        total_amount = config.get_card_total_cost(denomination, quantity)
        single_amount = config.get_card_total_cost(denomination, 1)
        acc_label = account.get("label", "Default") if account else "Default"

        # Pre-flight Wallet Balance Check (respects DISABLE_BALANCE_CHECK)
        if account and not simulation and not config.SIMULATION_MODE and not getattr(config, "DISABLE_BALANCE_CHECK", False):
            acc_bal = float(account.get("wallet_balance", 0.0) or 0.0)
            if acc_bal < single_amount:
                logger.warning(f"[{acc_label}] Insufficient balance: ₹{acc_bal:.2f} < ₹{single_amount:.2f}")
                return PurchaseResult(
                    success=False,
                    platform="rooter",
                    denomination=denomination,
                    quantity=quantity,
                    message=f"Not enough balance to place order (Balance: ₹{acc_bal:,.2f}, Required: ₹{single_amount:,.2f})",
                )
            elif acc_bal < total_amount and quantity > 1:
                logger.info(f"[{acc_label}] Adjusting quantity from {quantity} to 1 to match available balance ₹{acc_bal:.2f}")
                quantity = 1
                total_amount = single_amount

        try:
            if progress_callback:
                await progress_callback(f"[{acc_label}] Opening PlayStation voucher page (₹{denomination:,} x {quantity})...", "🚀")

            # Launch browser with account session
            if account:
                context = await browser_manager.create_context_for_account(account)
            else:
                context = await browser_manager.create_context(apply_storage_state=True)
            page = await context.new_page()

            # Direct navigation to PlayStation gift cards page
            await page.goto(self.playstation_url, timeout=config.BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_timeout(1000)
            await self.dismiss_location_popup(page)

            # 1. Select Denomination
            if progress_callback:
                await progress_callback(f"Selecting ₹{denomination:,} voucher chip...", "🎯")

            await self.select_denomination(page, denomination)

            # 2. Adjust Quantity (e.g. quantity = 2)
            if quantity > 1:
                if progress_callback:
                    await progress_callback(f"Setting quantity to {quantity} cards...", "🔢")
                await self.adjust_quantity(page, quantity)

            # 3. Check if Out of Stock button is visible
            out_btn = page.locator("button:has-text('Out of Stock'), button[disabled]:has-text('Out of Stock')").first
            if await out_btn.is_visible(timeout=1200):
                return PurchaseResult(
                    success=False,
                    platform="rooter",
                    denomination=denomination,
                    quantity=quantity,
                    message=f"PlayStation ₹{denomination:,} went out of stock right before checkout!",
                )

            # 4. Handle Simulation Mode
            if simulation or config.SIMULATION_MODE:
                if progress_callback:
                    await progress_callback("SIMULATION MODE: Voucher verified! Simulating dry-run purchase...", "💳")
                await page.wait_for_timeout(1200)

                mock_code = f"ROTR-PSN-{denomination}-{int(time.time()) % 90000 + 10000}"
                mock_pin = f"{int(time.time()) % 9000 + 1000}"
                mock_codes = [f"{mock_code}-A", f"{mock_code}-B"] if quantity == 2 else [mock_code]

                screenshot_path = await browser_manager.capture_screenshot(page, name_prefix="sim_order")
                return PurchaseResult(
                    success=True,
                    platform="rooter",
                    denomination=denomination,
                    quantity=quantity,
                    voucher_code=mock_codes[0],
                    voucher_pin=mock_pin,
                    codes=mock_codes,
                    order_id=f"SIM-RTR-{int(time.time())}",
                    receipt_summary=f"PlayStation ₹{denomination:,} x {quantity} (Total: ₹{total_amount:,}) [SIMULATION]",
                    message="Simulated purchase completed successfully. Disable SIMULATION_MODE for real order.",
                    simulation=True,
                    error_screenshot=screenshot_path,
                )

            # 5. Live Purchase Execution
            if progress_callback:
                await progress_callback(f"Clicking Buy Now / Proceed to Pay...", "🛒")

            # Check if modal is already open
            delivery_modal = page.locator(".mantine-Modal-content, div[role='dialog']").first
            modal_open = False
            try:
                modal_open = await delivery_modal.is_visible(timeout=1000)
            except Exception:
                pass

            if not modal_open:
                buy_btn = page.locator(
                    "button[data-testid='proceed-to-pay'], button[testid='proceed-to-pay'], "
                    "button[data-testid='add-address-btn'], button:has-text('Proceed to Pay'), "
                    "button:has-text('Pay via wallet'), button:has-text('Pay ₹'), button:has-text('Pay Rs'), "
                    "button:has-text('Buy Now'), button:has-text('Proceed')"
                ).first
                if await buy_btn.is_visible(timeout=5000):
                    await buy_btn.scroll_into_view_if_needed()
                    await buy_btn.click(force=True)
                    await page.wait_for_timeout(1500)

            # 6. Inside Delivery Details modal
            await self.dismiss_location_popup(page)
            
            # Fill email if present and empty
            if config.DELIVERY_EMAIL:
                email_input = page.locator("input[placeholder*='Email' i], input[name*='email'], input[type='email']").first
                try:
                    if await email_input.is_visible(timeout=1000):
                        current_val = await email_input.input_value()
                        if not current_val:
                            await email_input.fill(config.DELIVERY_EMAIL)
                except Exception:
                    pass

            # Click final Pay via wallet button
            modal_pay_btn = page.locator(
                ".mantine-Modal-content button:has-text('Pay via wallet'), "
                "div[role='dialog'] button:has-text('Pay via wallet'), "
                ".mantine-Modal-content button:has-text('Proceed to Pay'), "
                "div[role='dialog'] button:has-text('Proceed to Pay'), "
                ".mantine-Modal-content button:has-text('Pay ₹'), "
                ".mantine-Modal-content button:has-text('Pay Rs'), "
                "button:has-text('Pay via wallet'), "
                ".mantine-Modal-content button:has-text('Pay'), "
                ".mantine-Modal-content button[type='submit']"
            ).last

            if await modal_pay_btn.is_visible(timeout=6000):
                if progress_callback:
                    await progress_callback(f"Submitting final wallet payment for ₹{total_amount:,}...", "💳")
                await modal_pay_btn.click(force=True)

            # 7. Wait for Order Confirmation & Extract Codes
            if progress_callback:
                await progress_callback("Processing order & extracting PlayStation PIN...", "⏳")

            await page.wait_for_timeout(4000)
            screenshot_path = await browser_manager.capture_screenshot(page, name_prefix="order_success")

            # Extract order text & PINs
            content_text = await page.inner_text("body")

            # Extract Voucher Codes & PINs
            code_matches = re.findall(r"\b([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})\b", content_text)
            if not code_matches:
                code_matches = re.findall(r"\b([A-Z0-9]{12})\b", content_text)

            order_id_match = re.search(r"Order\s*ID[:\s]+([A-Za-z0-9\-_]+)", content_text, re.IGNORECASE)
            order_id = order_id_match.group(1) if order_id_match else f"RTR-{int(time.time())}"

            pin_match = re.search(r"PIN[:\s]+([0-9]{4,8})", content_text, re.IGNORECASE)
            voucher_pin = pin_match.group(1) if pin_match else None

            # Deduplicate extracted codes
            unique_codes = list(dict.fromkeys(code_matches))

            # Deduct balance on account if live order
            if account and not simulation and not config.SIMULATION_MODE:
                from services.account_manager import account_manager
                account_manager.deduct_balance(account["id"], total_amount)
                asyncio.create_task(account_manager.sync_all_balances())

            return PurchaseResult(
                success=True,
                platform="rooter",
                denomination=denomination,
                quantity=quantity,
                voucher_code=unique_codes[0] if unique_codes else "Check Order Screenshot",
                voucher_pin=voucher_pin,
                codes=unique_codes,
                order_id=order_id,
                receipt_summary=f"PlayStation ₹{denomination:,} x {quantity} (Total: ₹{total_amount:,})",
                message="Order submitted and voucher generated successfully!",
                error_screenshot=screenshot_path,
                simulation=False,
            )

        except Exception as e:
            logger.error(f"Error during purchase execution: {e}", exc_info=True)
            if page:
                try:
                    screenshot_path = await browser_manager.capture_screenshot(page, name_prefix="err_purchase")
                except Exception:
                    pass

            return PurchaseResult(
                success=False,
                platform="rooter",
                denomination=denomination,
                quantity=quantity,
                message=f"Purchase failed: {str(e)}",
                error_screenshot=screenshot_path,
            )

        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    async def perform_login(self, page, phone_number: str, otp_callback: Callable[[], Awaitable[str]], progress_callback: Optional[ProgressCallback] = None) -> bool:
        """Alias for perform_phone_login matching BasePlatform signature."""
        return await self.perform_phone_login(page.context, page, phone_number, otp_callback, progress_callback)

    async def search_playstation_cards(self, page: Page, denomination: Optional[int] = None, progress_callback: Optional[ProgressCallback] = None) -> List[CardItem]:
        return []

    async def execute_buy_flow(self, page: Page, denomination: int, progress_callback: Optional[ProgressCallback] = None, simulation: bool = False) -> PurchaseResult:
        return await self.execute_purchase(denomination=denomination, quantity=config.TARGET_QUANTITY, simulation=simulation, progress_callback=progress_callback)

    async def check_is_in_stock(self, page: Page, denomination: int) -> bool:
        return True


# Global singleton
rooter_service = RooterService()
