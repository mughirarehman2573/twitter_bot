import asyncio
from datetime import datetime, timedelta
import time
from typing import List, Tuple
import twscrape
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

    async def initialize_api(self):
        """Initialize the Twitter API with accounts from MongoDB"""
        self.api = await self.auth.get_api()

    async def get_active_campaigns(self) -> List[dict]:
        """Retrieve all active monitoring campaigns"""
        return list(self.db.campaigns.find({"active": True}))

    async def search_hashtag_pairs(self, hashtag_pairs: List[Tuple[str, str]]) -> List[dict]:
        """Search Twitter for posts containing hashtag pairs"""
        all_tweets = []

        for pair in hashtag_pairs:
            query = " ".join(pair) + " lang:en"
            try:
                tweets = []
                async for tweet in self.api.search(query, limit=100):
                    tweets.append({
                        "username": tweet.user.username,
                        "hashtags": [hashtag.text for hashtag in tweet.entities.hashtags],
                        "caption": tweet.text,
                        "timestamp": tweet.created_at,
                        "likes": tweet.favorite_count,
                        "comments": tweet.reply_count,
                        "retweets": tweet.retweet_count,
                        "url": f"https://twitter.com/{tweet.user.username}/status/{tweet.id}"
                    })
                all_tweets.extend(tweets)
                logger.info(f"Found {len(tweets)} tweets for {pair}")
            except Exception as e:
                logger.error(f"Error searching for {pair}: {str(e)}")
                await asyncio.sleep(60)

        return all_tweets

    async def store_tweets(self, campaign_id: str, tweets: List[dict]):
        """Store tweets in MongoDB with deduplication"""
        inserted_count = 0

        for tweet in tweets:
            try:
                tweet["campaign_id"] = campaign_id
                tweet["processed"] = False
                result = self.db.posts.insert_one(tweet)
                inserted_count += 1
            except DuplicateKeyError:
                continue
            except Exception as e:
                logger.error(f"Error storing tweet: {str(e)}")

        logger.info(f"Inserted {inserted_count} new tweets for campaign {campaign_id}")
        return inserted_count

    async def detect_flagged_accounts(self, campaign_id: str):
        """Detect accounts posting too frequently"""
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
        """Detect sudden spikes in hashtag pair activity"""
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
        """Monitor a single campaign"""
        campaign_id = campaign["_id"]
        logger.info(f"Monitoring campaign: {campaign['name']}")

        tweets = await self.search_hashtag_pairs(campaign["hashtag_pairs"])

        if tweets:
            await self.store_tweets(campaign_id, tweets)
            await self.detect_flagged_accounts(campaign_id)
            await self.detect_activity_surges(campaign_id)

    async def run(self):
        """Main monitoring loop"""
        logger.info("Starting Twitter hashtag monitoring system")

        await self.initialize_api()

        while True:
            try:
                start_time = time.time()
                campaigns = await self.get_active_campaigns()
                for campaign in campaigns:
                    try:
                        await self.monitor_campaign(campaign)
                    except Exception as e:
                        logger.error(f"Error monitoring campaign {campaign['name']}: {str(e)}")
                processing_time = time.time() - start_time
                sleep_time = max(0, self.poll_interval - processing_time)
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}")
                await asyncio.sleep(60)
                await self.initialize_api()


if __name__ == "__main__":
    monitor = TwitterHashtagMonitor()
    asyncio.run(monitor.run())