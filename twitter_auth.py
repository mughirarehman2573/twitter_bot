import asyncio
import json
import os

import undetected_chromedriver as uc
from twscrape import AccountsPool, API
import logging
from pymongo import MongoClient
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from logging.handlers import RotatingFileHandler
from selenium.webdriver.remote.remote_connection import LOGGER as selenium_logger


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TwitterAuth:
    def __init__(self, mongo_uri="mongodb://localhost:27017"):
        self.pool = AccountsPool()
        self.db = MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
            socketTimeoutMS=10000,
            retryWrites=True,
            retryReads=True
        ).twitter_monitor
        self.selenium_timeout = 45
        self.max_login_attempts = 3
        self.login_retry_delay = 10
        self.failed_accounts = set()
        self.driver = None
        self._setup_selenium_logging()

    def _setup_selenium_logging(self):
        os.makedirs("logs", exist_ok=True)
        selenium_logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(
            'logs/selenium.log',
            maxBytes=1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        selenium_logger.addHandler(handler)
        uc_logger = logging.getLogger('undetected_chromedriver')
        uc_logger.setLevel(logging.INFO)
        uc_logger.addHandler(handler)

    def _setup_selenium(self, headless=True):
        options = uc.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-browser-side-navigation")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.set_capability("goog:loggingPrefs", {
            'browser': 'ALL',
            'performance': 'ALL',
            'driver': 'ALL'
        })
        try:
            driver = uc.Chrome(
                options=options,
                headless=headless,
                version_main=114
            )
            return driver
        except Exception as e:
            logger.warning(f"Failed with Chrome 114, trying without version specification: {e}")
            try:
                driver = uc.Chrome(options=options, headless=headless)
                return driver
            except Exception as e:
                logger.error(f"Failed to initialize Chrome driver: {e}")
                raise

    def _save_browser_logs(self, identifier: str):
        log_dir = "logs/browser"
        os.makedirs(log_dir, exist_ok=True)
        try:
            console_logs = self.driver.get_log('browser')
            if console_logs:
                with open(f"{log_dir}/console_{identifier}.log", 'w') as f:
                    json.dump(console_logs, f, indent=2)
        except Exception as e:
            logger.warning(f"Couldn't save console logs: {e}")
        try:
            perf_logs = self.driver.get_log('performance')
            if perf_logs:
                with open(f"{log_dir}/perf_{identifier}.log", 'w') as f:
                    json.dump(perf_logs, f, indent=2)
        except Exception as e:
            logger.warning(f"Couldn't save performance logs: {e}")
        try:
            network_logs = self.driver.execute_script("return window.performance.getEntries();")
            if network_logs:
                with open(f"{log_dir}/network_{identifier}.log", 'w') as f:
                    json.dump(network_logs, f, indent=2)
        except Exception as e:
            logger.warning(f"Couldn't save network logs: {e}")

    async def _get_cookies_via_selenium(self, username: str, password: str, attempt: int = 1):
        try:
            logger.info(f"Attempting login for {username} (attempt {attempt})")
            self.driver = self._setup_selenium(headless=True)
            self.driver.set_page_load_timeout(60)
            try:
                logger.debug("Loading Twitter login page")
                self.driver.get("https://twitter.com/i/flow/login")
            except Exception as e:
                logger.warning(f"Page load timeout, continuing anyway: {e}")
            await asyncio.sleep(2)
            try:
                logger.debug("Entering username")
                username_field = WebDriverWait(self.driver, self.selenium_timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[autocomplete='username']"))
                )
                username_field.clear()
                username_field.send_keys(username)
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"Username field not found, trying alternative selectors: {e}")
                username_field = WebDriverWait(self.driver, self.selenium_timeout).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='text']"))
                )
                username_field.clear()
                username_field.send_keys(username)
                await asyncio.sleep(1)
            logger.debug("Clicking next button")
            next_button = WebDriverWait(self.driver, self.selenium_timeout).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@role='button']//span[text()='Next']"))
            )
            next_button.click()
            await asyncio.sleep(3)
            try:
                unusual_activity = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//span[contains(text(),'unusual activity')]"))
                )
                if unusual_activity:
                    logger.warning("Unusual activity detected, trying to handle")
            except:
                pass
            logger.debug("Entering password")
            password_field = WebDriverWait(self.driver, self.selenium_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[autocomplete='current-password']"))
            )
            password_field.send_keys(password)
            await asyncio.sleep(1)
            logger.debug("Clicking login button")
            login_button = WebDriverWait(self.driver, self.selenium_timeout).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-testid='LoginForm_Login_Button']"))
            )
            login_button.click()
            await asyncio.sleep(5)
            try:
                WebDriverWait(self.driver, self.selenium_timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='AppTabBar_Home_Link']"))
                )
            except:
                logger.warning("Home link not found, checking for error messages")
                try:
                    error_message = self.driver.find_element(By.XPATH, "//span[contains(text(),'incorrect')]")
                    if error_message:
                        logger.error(f"Login error: {error_message.text}")
                        raise ValueError(f"Login failed: {error_message.text}")
                except:
                    pass
            logger.debug("Extracting cookies")
            cookies = self.driver.get_cookies()
            cookie_dict = {c['name']: c['value'] for c in cookies}
            if not cookie_dict.get('auth_token') or not cookie_dict.get('ct0'):
                raise ValueError("Essential cookies not found")
            cookies_str = f"auth_token={cookie_dict['auth_token']}; ct0={cookie_dict['ct0']}"
            logger.info(f"Successfully logged in {username}")
            self._save_browser_logs(username)
            return cookies_str
        except Exception as e:
            logger.error(f"Login attempt {attempt} failed for {username}: {str(e)}")
            if self.driver:
                try:
                    self.driver.save_screenshot(f"login_error_{username}_attempt_{attempt}.png")
                    logger.info(f"Screenshot saved for debugging")
                    self._save_browser_logs(f"error_{username}_attempt_{attempt}")
                except:
                    pass
                self.driver.quit()
                self.driver = None
            if attempt < self.max_login_attempts:
                retry_delay = self.login_retry_delay * attempt
                logger.warning(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                return await self._get_cookies_via_selenium(username, password, attempt + 1)
            else:
                logger.error(f"Max login attempts reached for {username}")
                self.failed_accounts.add(username)
                raise
        finally:
            if self.driver:
                try:
                    self.driver.quit()
                except:
                    pass
                self.driver = None

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
        if not active_accounts:
            logger.warning("⚠️ No active accounts. Attempting to reactivate...")

            await self.reactivate_all_accounts()
            await self.initialize_accounts()
            await asyncio.sleep(10)

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
