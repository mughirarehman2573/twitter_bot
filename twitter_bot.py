import asyncio
from datetime import datetime, timedelta
import time
from typing import List, Tuple
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import logging
from twitter_auth import TwitterAuth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TwitterHashtagMonitor:
    def __init__(self, mongo_uri: str = "mongodb://localhost:27017"):
        self.db = MongoClient(mongo_uri).twitter_monitor
        self.auth = TwitterAuth(mongo_uri)
        self.api = None
        self.poll_interval = 120
        self.last_account_check = datetime.utcnow()
        self.failed_accounts = set()
        self.used_accounts = set()

    async def initialize_api(self):
        """Initialize the Twitter API with accounts from MongoDB"""
        self.api = await self.auth.get_api(exclude_accounts=self.used_accounts)

    async def check_for_new_accounts(self):
        """Check if new accounts have been added since the last check"""
        new_accounts = list(self.db.accounts.find({
            "created_at": {"$gt": self.last_account_check}
        }))
        self.last_account_check = datetime.utcnow()

        if new_accounts:
            logger.info("New accounts added. Restarting monitoring with updated accounts.")
            self.failed_accounts.clear()
            self.used_accounts.clear()
            await self.initialize_api()

    async def get_active_campaigns(self) -> List[dict]:
        """Retrieve all active monitoring campaigns"""
        return list(self.db.campaigns.find({"active": True}))

    async def search_hashtag_pairs(self, hashtag_pairs: List[Tuple[str, str]]) -> List[dict]:
        """Search Twitter for posts containing hashtag pairs with account retry"""
        all_tweets = []

        for pair in hashtag_pairs:
            query = " ".join(pair) + " lang:en"
            retries = 3

            for attempt in range(retries):
                try:
                    tweets = []
                    async for tweet in self.api.search(query, limit=100):
                        tweets.append({
                            "username": tweet.user.username,
                            "hashtags": [hashtag.get("text", "") for hashtag in
                                         getattr(tweet, "entities", {}).get("hashtags", [])],
                            "caption": tweet.rawContent,
                            "timestamp": tweet.date,
                            "likes": tweet.likeCount,
                            "comments": tweet.replyCount,
                            "retweets": tweet.retweetCount,
                            "url": tweet.url,
                        })
                    all_tweets.extend(tweets)
                    logger.info(f"Found {len(tweets)} tweets for {pair}")
                    break
                except Exception as e:
                    if "No account available for queue" in str(e):
                        logger.warning("Rate limit hit or no account available. Retrying with another account...")
                        self.used_accounts.add(self.api.username)
                        self.failed_accounts.add(self.api.username)
                        await asyncio.sleep(5)
                        self.api = await self.auth.get_api(exclude_accounts=self.used_accounts)
                        continue
                    else:
                        logger.error(f"Error searching for {pair}: {str(e)}")
                        await asyncio.sleep(10)
                        break

        return all_tweets

    async def store_tweets(self, campaign_id: str, tweets: List[dict]):
        inserted_count = 0
        for tweet in tweets:
            try:
                tweet["campaign_id"] = campaign_id
                tweet["processed"] = False
                self.db.posts.insert_one(tweet)
                inserted_count += 1
            except DuplicateKeyError:
                continue
            except Exception as e:
                logger.error(f"Error storing tweet: {str(e)}")
        logger.info(f"Inserted {inserted_count} new tweets for campaign {campaign_id}")
        return inserted_count

    async def detect_flagged_accounts(self, campaign_id: str):
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        pipeline = [
            {
                "$match": {
                    "campaign_id": campaign_id,
                    "timestamp": {"$gte": one_hour_ago},
                    "processed": False
                }
            },
            {
                "$group": {
                    "_id": "$username",
                    "post_count": {"$sum": 1},
                    "post_ids": {"$push": "$_id"},
                    "first_post": {"$min": "$timestamp"},
                    "last_post": {"$max": "$timestamp"}
                }
            },
            {
                "$match": {
                    "post_count": {"$gte": 2}
                }
            }
        ]
        flagged_accounts = list(self.db.posts.aggregate(pipeline))
        for account in flagged_accounts:
            self.db.flagged_accounts.update_one(
                {"username": account["_id"], "campaign_id": campaign_id},
                {
                    "$setOnInsert": {
                        "first_detected": account["first_post"],
                        "campaign_id": campaign_id
                    },
                    "$set": {
                        "last_detected": account["last_post"],
                    },
                    "$inc": {"post_count": account["post_count"]},
                    "$addToSet": {"posts": {"$each": account["post_ids"]}}
                },
                upsert=True
            )
        self.db.posts.update_many(
            {"campaign_id": campaign_id, "processed": False},
            {"$set": {"processed": True}}
        )
        logger.info(f"Flagged {len(flagged_accounts)} accounts for campaign {campaign_id}")

    async def detect_activity_surges(self, campaign_id: str):
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        campaign = self.db.campaigns.find_one({"_id": campaign_id})
        if not campaign:
            return
        hashtag_pairs = campaign.get("hashtag_pairs", [])
        for pair in hashtag_pairs:
            pair_activity = list(self.db.posts.aggregate([
                {
                    "$match": {
                        "campaign_id": campaign_id,
                        "hashtags": {"$all": pair},
                        "timestamp": {"$gte": seven_days_ago}
                    }
                },
                {
                    "$group": {
                        "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                        "post_count": {"$sum": 1},
                        "unique_accounts": {"$addToSet": "$username"}
                    }
                },
                {"$sort": {"_id": 1}}
            ]))
            if len(pair_activity) >= 2:
                current_day = pair_activity[-1]
                previous_day = pair_activity[-2]
                if previous_day["post_count"] == 0 and current_day["post_count"] >= 20:
                    self.db.hashtag_activity.update_one(
                        {
                            "campaign_id": campaign_id,
                            "hashtag_pair": pair,
                            "date": current_day["_id"]
                        },
                        {
                            "$set": {
                                "post_count": current_day["post_count"],
                                "unique_accounts": len(current_day["unique_accounts"]),
                                "is_surge": True
                            }
                        },
                        upsert=True
                    )
                    logger.info(f"Surge detected for {pair} on {current_day['_id']}")

    async def monitor_campaign(self, campaign: dict):
        campaign_id = campaign["_id"]
        logger.info(f"Monitoring campaign: {campaign['name']}")
        tweets = await self.search_hashtag_pairs(campaign["hashtag_pairs"])
        if tweets:
            await self.store_tweets(campaign_id, tweets)
            await self.detect_flagged_accounts(campaign_id)
            await self.detect_activity_surges(campaign_id)

    async def retry_failed_accounts(self):
        if self.failed_accounts:
            logger.info("Retrying previously failed accounts...")
            self.used_accounts = set()
            self.api = await self.auth.get_api(preferred_accounts=self.failed_accounts)
            self.failed_accounts.clear()

    async def run(self):
        logger.info("Starting Twitter hashtag monitoring system")
        await self.initialize_api()

        while True:
            try:
                await self.check_for_new_accounts()
                start_time = time.time()
                campaigns = await self.get_active_campaigns()
                for campaign in campaigns:
                    try:
                        await self.monitor_campaign(campaign)
                    except Exception as e:
                        logger.error(f"Error monitoring campaign {campaign['name']}: {str(e)}")

                await self.retry_failed_accounts()

                processing_time = time.time() - start_time
                sleep_time = max(0, int(self.poll_interval - processing_time))
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}")
                await asyncio.sleep(60)
                await self.initialize_api()
