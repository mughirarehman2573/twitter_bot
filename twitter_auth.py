from aiohttp.abc import HTTPException
from twscrape import AccountsPool, API
import logging
from pymongo import MongoClient
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TwitterAuth:
    def __init__(self, mongo_uri="mongodb://localhost:27017"):
        self.pool = AccountsPool()
        self.db = MongoClient(mongo_uri).twitter_monitor

    async def add_account(self, username: str, password: str, email: str, email_password: str):
        """Add single account to both twscrape and MongoDB"""
        await self.pool.add_account(username, password, email, email_password)

        self.db.twitter_accounts.update_one(
            {"username": username},
            {
                "$setOnInsert": {
                    "added_at": datetime.utcnow(),
                    "is_active": True
                },
                "$set": {
                    "last_used": datetime.utcnow(),
                    "email": email
                }
            },
            upsert=True
        )
        logger.info(f"Added account: {username}")

    async def get_active_accounts(self):
        """Get list of active accounts from MongoDB"""
        return list(self.db.twitter_accounts.find({"is_active": True}))

    async def disable_account(self, username: str):
        """Mark an account as inactive"""
        self.db.twitter_accounts.update_one(
            {"username": username},
            {"$set": {"is_active": False}}
        )
        logger.warning(f"Disabled account: {username}")

    async def get_api(self):
        """Get authenticated API instance"""
        await self.pool.login_all()
        return API(self.pool)


