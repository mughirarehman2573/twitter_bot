import os
import signal

import streamlit as st
from pymongo import MongoClient
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import asyncio
from twitter_auth import TwitterAuth
import subprocess

st.set_page_config(page_title="Twitter Hashtag Monitor", layout="wide")


@st.cache_resource
def get_db():
    return MongoClient("mongodb://localhost:27017").twitter_monitor


db = get_db()

st.title("Twitter Hashtag Monitoring Dashboard")

pages = [
    "Account Management",
    "Campaign Management",
    "Surge Visualization",
    "Flagged Accounts",
    "Summary Metrics",
    "Run Script"
]

selected_page = st.sidebar.radio("Pages", pages)

if selected_page == "Account Management":
    st.header("Twitter Account Management")

    with st.expander("Add New Twitter Account"):
        with st.form("new_account"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            email = st.text_input("Email (optional)")
            email_password = st.text_input("Email Password (optional)", type="password")

            if st.form_submit_button("Add Account"):
                if username and password:
                    try:
                        auth = TwitterAuth()
                        auth.add_account(username, password, email, email_password)
                        db.twitter_accounts.insert_one({
                            "username": username,
                            "password": password,
                            "email": email,
                            "email_password": email_password,
                            "is_active": True,
                            "added_at": datetime.utcnow(),
                            "last_used": None,
                            "proxy": None
                        })
                        st.success("Account added successfully!")
                    except Exception as e:
                        st.error(f"Error adding account: {str(e)}")
                else:
                    st.error("All fields including proxy file are required")

    st.subheader("Active Twitter Accounts")
    accounts = list(db.twitter_accounts.find({"is_active": True}).sort("last_used", -1))

    if not accounts:
        st.info("No active accounts found")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Active Accounts", len(accounts))

        for account in accounts:
            with st.expander(f"@{account['username']}"):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"**Added:** {account['added_at'].strftime('%Y-%m-%d %H:%M')}")
                    if 'last_used' in account and account['last_used']:
                        st.write(f"**Last used:** {account['last_used'].strftime('%Y-%m-%d %H:%M')}")
                    else:
                        st.write("**Last used:** Never")
                    if 'email' in account and account['email']:
                        st.write(f"**Email:** {account['email']}")
                    if 'proxy' in account and account['proxy']:
                        st.write(f"**Proxy:** {account['proxy']}")
                with col2:
                    if st.button("Disable", key=f"disable_{account['username']}"):
                        auth = TwitterAuth()
                        asyncio.run(auth.disable_account(account['username']))

        if st.checkbox("Show inactive accounts"):
            inactive_accounts = list(db.twitter_accounts.find({"is_active": False}))
            if inactive_accounts:
                st.subheader("Inactive Accounts")
                for account in inactive_accounts:
                    st.write(f"@{account['username']} (last active: {account.get('last_used', 'never')})")



elif selected_page == "Campaign Management":
    st.header("Campaign Management")

    with st.expander("Create New Campaign", expanded=True):
        with st.form("new_campaign"):
            name = st.text_input("Campaign Name")
            hashtag_pairs = st.text_area("Hashtag Pairs (one pair per line, separate hashtags with comma)")
            accounts_to_track = st.text_area("Accounts to Track (one per line)")

            if st.form_submit_button("Create Campaign"):
                if name and hashtag_pairs:
                    pairs = [line.split(",")[:2] for line in hashtag_pairs.splitlines() if line.strip()]
                    accounts = [acc.strip() for acc in accounts_to_track.splitlines() if acc.strip()]
                    db.campaigns.insert_one({
                        "name": name,
                        "hashtag_pairs": pairs,
                        "accounts_to_track": accounts,
                        "created_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow(),
                        "active": True
                    })
                    st.success("Campaign created successfully!")
                    st.rerun()
                else:
                    st.error("Campaign name and at least one hashtag pair are required")

    st.subheader("Active Campaigns")
    campaigns = list(db.campaigns.find({"active": True}))

    if not campaigns:
        st.info("No active campaigns found")
    else:
        for campaign in campaigns:
            is_editing = 'editing_campaign' in st.session_state and st.session_state.editing_campaign == campaign['_id']

            with st.expander(f"{campaign['name']} (Created: {campaign['created_at'].strftime('%Y-%m-%d')})",
                             expanded=is_editing):
                if not is_editing:
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.write(f"**Hashtag Pairs:**")
                        for pair in campaign['hashtag_pairs']:
                            st.write(f"- {pair[0].strip()} + {pair[1].strip()}")

                        st.write(f"**Accounts Tracked:** {len(campaign['accounts_to_track'])}")
                        if campaign['accounts_to_track']:
                            st.write("**Tracked Accounts:**")
                            for acc in campaign['accounts_to_track']:
                                st.write(f"- {acc}")

                        st.write(f"**Created:** {campaign['created_at'].strftime('%Y-%m-%d %H:%M')}")
                        st.write(f"**Last Updated:** {campaign['updated_at'].strftime('%Y-%m-%d %H:%M')}")

                    with col2:
                        if st.button("Edit", key=f"edit_{campaign['_id']}"):
                            st.session_state.editing_campaign = campaign['_id']
                            st.rerun()

                        if st.button("Deactivate", key=f"deactivate_{campaign['_id']}"):
                            db.campaigns.update_one(
                                {"_id": campaign["_id"]},
                                {"$set": {"active": False, "updated_at": datetime.utcnow()}}
                            )
                            st.success("Campaign deactivated!")
                            st.rerun()

                        if st.button("Delete", key=f"delete_{campaign['_id']}"):
                            db.campaigns.delete_one({"_id": campaign["_id"]})
                            st.success("Campaign deleted!")
                            st.rerun()
                else:
                    with st.form(f"edit_form_{campaign['_id']}"):
                        new_name = st.text_input("Campaign Name", value=campaign['name'])

                        current_pairs = "\n".join([f"{pair[0]},{pair[1]}" for pair in campaign['hashtag_pairs']])
                        new_hashtag_pairs = st.text_area(
                            "Hashtag Pairs (one pair per line, separate hashtags with comma)",
                            value=current_pairs
                        )

                        current_accounts = "\n".join(campaign['accounts_to_track'])
                        new_accounts_to_track = st.text_area(
                            "Accounts to Track (one per line)",
                            value=current_accounts
                        )

                        col1, col2 = st.columns(2)
                        with col1:
                            if st.form_submit_button("Update Campaign"):
                                if new_name and new_hashtag_pairs:
                                    pairs = [line.split(",")[:2] for line in new_hashtag_pairs.splitlines() if
                                             line.strip()]
                                    accounts = [acc.strip() for acc in new_accounts_to_track.splitlines() if
                                                acc.strip()]

                                    db.campaigns.update_one(
                                        {"_id": campaign["_id"]},
                                        {"$set": {
                                            "name": new_name,
                                            "hashtag_pairs": pairs,
                                            "accounts_to_track": accounts,
                                            "updated_at": datetime.utcnow()
                                        }}
                                    )
                                    st.success("Campaign updated successfully!")
                                    del st.session_state.editing_campaign
                                    st.rerun()
                                else:
                                    st.error("Campaign name and at least one hashtag pair are required")

                        with col2:
                            if st.form_submit_button("Cancel"):
                                del st.session_state.editing_campaign
                                st.rerun()


if 'script_process' not in st.session_state:
    st.session_state.script_process = None

if selected_page == "Run Script":
    st.header("Script Execution")


    def run_my_script():
        if st.session_state.script_process is None:
            process = subprocess.Popen(["python", "twitter_bot.py"])
            st.session_state.script_process = process
            st.success("Script started!")
        else:
            st.warning("Script is already running.")


    def stop_my_script():
        process = st.session_state.script_process
        if process and process.poll() is None:
            os.kill(process.pid, signal.SIGTERM)
            st.session_state.script_process = None
            st.success("Script stopped!")
        else:
            st.warning("No script is running.")


    col1, col2 = st.columns(2)
    with col1:
        if st.button("Run Script Now"):
            run_my_script()
    with col2:
        if st.button("Cancel Script"):
            stop_my_script()


elif selected_page == "Surge Visualization":
    st.header("Hashtag Activity Surges")

    campaigns = list(db.campaigns.find({"active": True}))

    if not campaigns:
        st.info("No active campaigns to display")
    else:
        campaign_id = st.selectbox(
            "Select Campaign",
            options=[c["_id"] for c in campaigns],
            format_func=lambda x: next(c["name"] for c in campaigns if c["_id"] == x)
        )

        activity_data = list(db.hashtag_activity.find(
            {"campaign_id": campaign_id, "is_surge": True}
        ).sort("date", -1).limit(30))

        if not activity_data:
            st.info("No surge activity detected for this campaign")
        else:
            st.subheader("Recent Surge Alerts")
            for alert in activity_data:
                st.warning(
                    f"Surge detected for {'+'.join(alert['hashtag_pair'])} on {alert['date']}: "
                    f"{alert['post_count']} posts"
                )

            st.subheader("Activity Trends")
            trend_data = list(db.hashtag_activity.find(
                {"campaign_id": campaign_id}
            ).sort("date", 1))

            if trend_data:
                df = pd.DataFrame(trend_data)
                fig = px.line(
                    df,
                    x="date",
                    y="post_count",
                    color=df["hashtag_pair"].apply(lambda x: "+".join(x)),
                    title="Daily Post Volume by Hashtag Pair",
                    labels={"date": "Date", "post_count": "Post Count"}
                )
                st.plotly_chart(fig, use_container_width=True)

elif selected_page == "Flagged Accounts":
    st.header("Flagged Accounts")

    campaigns = list(db.campaigns.find({"active": True}))

    if not campaigns:
        st.info("No active campaigns to display")
    else:
        campaign_id = st.selectbox(
            "Select Campaign",
            options=[c["_id"] for c in campaigns],
            format_func=lambda x: next(c["name"] for c in campaigns if c["_id"] == x)
        )
        flagged_accounts = list(db.flagged_accounts.find(
            {"campaign_id": campaign_id}
        ).sort("last_detected", -1))

        if not flagged_accounts:
            st.info("No accounts flagged for this campaign")
        else:
            st.write(f"Found {len(flagged_accounts)} flagged accounts")

            for account in flagged_accounts:
                with st.expander(f"@{account['username']} - {account['post_count']} posts"):
                    st.write(f"**First detected:** {account['first_detected'].strftime('%Y-%m-%d %H:%M')}")
                    st.write(f"**Last detected:** {account['last_detected'].strftime('%Y-%m-%d %H:%M')}")

                    st.subheader("Recent Posts")
                    posts = list(db.posts.find(
                        {"_id": {"$in": account["posts"]}}
                    ).sort("timestamp", -1).limit(5))

                    for post in posts:
                        st.write(f"**{post['timestamp'].strftime('%Y-%m-%d %H:%M')}**")
                        st.write(post["caption"])
                        st.write(f"Likes: {post['likes']} | Comments: {post['comments']}")
                        st.markdown(f"[View on Twitter]({post['url']})")
                        st.divider()

elif selected_page == "Summary Metrics":
    st.header("Summary Metrics")

    campaigns = list(db.campaigns.find({"active": True}))

    if not campaigns:
        st.info("No active campaigns to display")
    else:
        campaign_id = st.selectbox(
            "Select Campaign",
            options=[c["_id"] for c in campaigns],
            format_func=lambda x: next(c["name"] for c in campaigns if c["_id"] == x)
        )

        total_posts = db.posts.count_documents({"campaign_id": campaign_id})
        unique_accounts = len(db.posts.distinct("username", {"campaign_id": campaign_id}))
        flagged_accounts = db.flagged_accounts.count_documents({"campaign_id": campaign_id})
        surge_alerts = db.hashtag_activity.count_documents({"campaign_id": campaign_id, "is_surge": True})

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Posts", total_posts)
        col2.metric("Unique Accounts", unique_accounts)
        col3.metric("Flagged Accounts", flagged_accounts)
        col4.metric("Surge Alerts", surge_alerts)

        st.subheader("Recent Activity (Last 7 Days)")
        seven_days_ago = datetime.utcnow() - timedelta(days=7)

        pipeline = [
            {
                "$match": {
                    "campaign_id": campaign_id,
                    "timestamp": {"$gte": seven_days_ago}
                }
            },
            {
                "$group": {
                    "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                    "post_count": {"$sum": 1}
                }
            },
            {"$sort": {"_id": 1}}
        ]

        daily_activity = list(db.posts.aggregate(pipeline))

        if daily_activity:
            df = pd.DataFrame(daily_activity)
            fig = px.bar(
                df,
                x="_id",
                y="post_count",
                title="Daily Post Volume",
                labels={"_id": "Date", "post_count": "Posts"}
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No activity in the last 7 days")