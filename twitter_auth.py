import random

import asyncio
import undetected_chromedriver as uc
from twscrape import AccountsPool, API
import logging
from pymongo import MongoClient
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TwitterAuth:
    def __init__(self, mongo_uri="mongodb://localhost:27017"):
        self.pool = AccountsPool()
        self.db = MongoClient(mongo_uri).twitter_monitor
        self.selenium_timeout = 30
        self.max_login_attempts = 3
        self.login_retry_delay = 5
        self.failed_accounts = set()

    def _setup_selenium(self, headless=True):
        options = uc.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        driver = uc.Chrome(version_main=136, options=options, headless=headless)
        return driver

    async def _get_cookies_via_selenium(self, username: str, password: str, attempt: int = 1):
        driver = None
        try:
            logger.debug(f"Launching Chrome for login: {username} (attempt {attempt})")
            driver = self._setup_selenium(headless=True)
            driver.get("https://twitter.com/i/flow/login")

            logger.debug(f"Waiting for username input field")
            username_field = WebDriverWait(driver, self.selenium_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[autocomplete='username']"))
            )
            username_field.send_keys(username)

            logger.debug(f"Clicking Next after entering username")
            next_buttons = WebDriverWait(driver, self.selenium_timeout).until(
                EC.presence_of_all_elements_located((By.XPATH, "//button[@role='button']//span[text()='Next']"))
            )
            next_buttons[0].click()

            logger.debug(f"Waiting for password input field")
            password_field = WebDriverWait(driver, self.selenium_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[autocomplete='current-password']"))
            )
            password_field.send_keys(password)

            logger.debug(f"Clicking Login button")
            login_buttons = WebDriverWait(driver, self.selenium_timeout).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "button[data-testid='LoginForm_Login_Button']"))
            )
            login_buttons[0].click()

            logger.debug(f"Waiting for Home tab to confirm login success")
            WebDriverWait(driver, self.selenium_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='AppTabBar_Home_Link']"))
            )

            logger.debug(f"Login success, extracting cookies")
            all_cookies = driver.get_cookies()
            cookie_dict = {cookie['name']: cookie['value'] for cookie in all_cookies}

            logger.debug(f"Extracted cookies: {cookie_dict}")
            auth_token = cookie_dict.get("auth_token")
            ct0 = cookie_dict.get("ct0")

            if not auth_token or not ct0:
                logger.warning(f"auth_token or ct0 not found in cookies for {username}")
                raise ValueError("auth_token or ct0 not found in cookies")

            cookies_str = f"auth_token={auth_token}; ct0={ct0}"
            return cookies_str

        except Exception as e:
            logger.exception(f"Exception during login attempt {attempt} for {username}: {e}")
            if attempt < self.max_login_attempts:
                retry_delay = self.login_retry_delay * attempt + random.uniform(0, 2)
                logger.warning(
                    f"Login attempt {attempt} failed for {username}. Retrying in {retry_delay:.1f} seconds..."
                )
                await asyncio.sleep(retry_delay)
                return await self._get_cookies_via_selenium(username, password, attempt + 1)
            else:
                logger.error(f"Max login attempts ({self.max_login_attempts}) reached for {username}")
                self.failed_accounts.add(username)
                raise
        finally:
            if driver:
                driver.quit()

    async def add_account(self, username: str, password: str, email: str | None, email_password: str | None):
        try:
            cookies = await self._get_cookies_via_selenium(username, password)
            await self.pool.delete_accounts(username)
            await self.pool.add_account(username, password, email, email_password, cookies=cookies)

            account_data = {
                "username": username,
                "email": email,
                "cookies": cookies,
                "added_at": datetime.utcnow(),
                "last_used": datetime.utcnow(),
                "is_active": True,
                "auth_method": "selenium"
            }

            self.db.twitter_accounts.update_one(
                {"username": username},
                {"$set": account_data},
                upsert=True
            )

            logger.info(f"Successfully added account: {username} with cookies")
            return True

        except Exception as e:
            logger.error(f"Failed to add account {username}: {str(e)}")
            await self.disable_account(username)
            return False

    async def get_active_accounts(self):
        return list(self.db.twitter_accounts.find({"is_active": True}))

    async def disable_account(self, username: str):
        self.db.twitter_accounts.update_one(
            {"username": username},
            {"$set": {"is_active": False, "disabled_at": datetime.utcnow()}}
        )
        logger.warning(f"Disabled account: {username}")

    async def reactivate_all_accounts(self):
        result = self.db.twitter_accounts.update_many(
            {"is_active": False},
            {"$set": {"is_active": True, "reactivated_at": datetime.utcnow()}}
        )
        logger.info(f"Reactivated {result.modified_count} previously disabled accounts.")

    async def initialize_accounts(self):
        await self.reactivate_all_accounts()
        active_accounts = await self.get_active_accounts()
        for acc in active_accounts:
            try:
                username = acc["username"]
                password = acc["password"]
                email = acc.get("email")
                email_password = acc.get("email_password")

                cookies = await self._get_cookies_via_selenium(username, password)
                await self.pool.add_account(username, password, email, email_password, cookies=cookies)

                logger.info(f"Initialized account in pool: {username}")
            except Exception as e:
                logger.error(f"Failed to initialize account {acc.get('username')}: {str(e)}")

    async def get_api(self, exclude_accounts=None, preferred_accounts=None):
        active_accounts = await self.get_active_accounts()

        if exclude_accounts:
            active_accounts = [acc for acc in active_accounts if acc["username"] not in exclude_accounts]

        if preferred_accounts:
            preferred = [acc for acc in active_accounts if acc["username"] in preferred_accounts]
            non_preferred = [acc for acc in active_accounts if acc["username"] not in preferred_accounts]
            active_accounts = preferred + non_preferred

        for account in active_accounts:
            username = account["username"]
            password = account.get("password")
            email = account.get("email")
            email_password = account.get("email_password")
            try:
                await self.add_account(username, password, email, email_password)
            except Exception as e:
                logger.error(f"Failed to add {username} to pool: {str(e)}")
        await self.pool.login_all()
        return API(self.pool)
