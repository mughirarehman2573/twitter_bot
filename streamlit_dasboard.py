import hashlib
import os
import signal
import sys

import streamlit as st
from plotly.subplots import make_subplots
from pymongo import MongoClient
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import asyncio
from twitter_auth import TwitterAuth
import subprocess

st.set_page_config(page_title="Twitter Hashtag Monitor", layout="wide")

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

@st.cache_resource
def get_db():
    return MongoClient("mongodb://localhost:27017").twitter_monitor

db = get_db()

if "user" not in st.session_state:
    st.session_state.user = None
if "auth_mode" not in st.session_state:
    st.session_state.auth_mode = "Login"

# Utility to center UI and use consistent layout
def centered_container(content_fn):
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        content_fn()

def show_auth_toggle():
    st.markdown("### 🔐 Authentication")
    centered_container(lambda: toggle_buttons())

def toggle_buttons():
    login_btn, signup_btn = st.columns(2)
    with login_btn:
        if st.button("🔑 Login", use_container_width=True):
            st.session_state.auth_mode = "Login"
    with signup_btn:
        if st.button("🆕 Sign Up", use_container_width=True):
            st.session_state.auth_mode = "Sign Up"

def signup():
    centered_container(lambda: signup_form())

def signup_form():
    st.subheader("📝 Create an Account")
    st.write("Fill in the form to sign up")
    st.divider()
    username = st.text_input("👤 Choose a username", key="signup_username")
    password = st.text_input("🔒 Choose a password", type="password", key="signup_password")
    confirm = st.text_input("🔒 Confirm password", type="password", key="signup_confirm")

    st.markdown("")
    if st.button("✅ Sign Up", use_container_width=True):
        if not username or not password or not confirm:
            st.error("All fields are required")
        elif password != confirm:
            st.error("Passwords do not match")
        elif db.users.find_one({"username": username}):
            st.error("Username already exists")
        else:
            db.users.insert_one({"username": username, "password": hash_password(password)})
            st.success("✅ Signup successful! Please log in.")
            st.session_state.auth_mode = "Login"

def login():
    centered_container(lambda: login_form())

def login_form():
    st.subheader("🔐 Login to Your Account")
    st.write("Enter your credentials to continue")
    st.divider()
    username = st.text_input("👤 Username", key="login_username")
    password = st.text_input("🔒 Password", type="password", key="login_password")

    st.markdown("")
    if st.button("🚀 Login", use_container_width=True):
        user = db.users.find_one({"username": username})
        if user and user["password"] == hash_password(password):
            st.session_state.user = username
            st.success(f"👋 Welcome, {username}!")
            st.rerun()
        else:
            st.error("Invalid username or password")

def logout():
    with st.sidebar:
        st.write(f"👤 Logged in as: `{st.session_state.user}`")
        if st.button("🔓 Logout"):
            st.session_state.user = None
            st.success("🚪 Logged out successfully")
            st.rerun()

if not st.session_state.user:
    show_auth_toggle()
    if st.session_state.auth_mode == "Login":
        login()
    else:
        signup()
    st.stop()
else:
    logout()

st.title("Twitter Hashtag Monitoring Dashboard")

pages = [
    "Account Management",
    "Campaign Management",
    "Surge Visualization",
    "Flagged Accounts",
    "Scraped Posts",
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
            email = st.text_input("Email")
            proxy = st.text_input("Proxy")
            email_password = password

            if st.form_submit_button("Add Account"):
                if username and password:
                    try:
                        auth = TwitterAuth()
                        auth.add_account(username, password, email, email_password ,proxy)
                        db.twitter_accounts.insert_one({
                            "username": username,
                            "password": password,
                            "email": email,
                            "email_password": email_password,
                            "is_active": True,
                            "added_at": datetime.utcnow(),
                            "last_used": None,
                            "proxy": proxy
                        })
                        st.success("Account added successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error adding account: {str(e)}")
                else:
                    st.error("All fields including proxy file are required")

    with st.expander("Bulk Upload from File"):
        uploaded_file = st.file_uploader("Upload accounts file (username:password:email:email_password)", type=["txt"])
        if uploaded_file is not None:
            if st.button("Process Uploaded File"):
                try:
                    with open("accounts.txt", "wb") as f:
                        f.write(uploaded_file.getvalue())

                    auth = TwitterAuth()
                    asyncio.run(auth.add_accounts_from_file("accounts.txt"))
                    st.success("Accounts uploaded successfully!")
                except Exception as e:
                    st.error(f"Error processing file: {str(e)}")
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
                    btn_col1, btn_col2 = st.columns(2)
                    with btn_col1:
                        if st.button("Disable", key=f"disable_{account['username']}"):
                            auth = TwitterAuth()
                            asyncio.run(auth.disable_account(account['username']))
                            st.rerun()
                    with btn_col2:
                        if st.button("Delete", key=f"delete_{account['username']}"):
                            db.twitter_accounts.delete_one({"username": account['username']})
                            st.success(f"Account @{account['username']} deleted permanently!")
                            st.rerun()

    if st.checkbox("Show inactive accounts"):
        inactive_accounts = list(db.twitter_accounts.find({"is_active": False}))
        if inactive_accounts:
            st.subheader("Inactive Accounts")
            for account in inactive_accounts:
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
                        btn_col1, btn_col2 = st.columns(2)
                        with btn_col1:
                            if st.button("Enable", key=f"enable_{account['username']}"):
                                db.twitter_accounts.update_one(
                                    {"username": account['username']},
                                    {"$set": {"is_active": True}}
                                )
                                st.rerun()
                        with btn_col2:
                            if st.button("Delete", key=f"delete_inactive_{account['username']}"):
                                db.twitter_accounts.delete_one({"username": account['username']})
                                st.success(f"Account @{account['username']} deleted permanently!")
                                st.rerun()


elif selected_page == "Campaign Management":
    st.header("🛠️ Campaign Management")

    with st.expander("➕ Create New Campaign", expanded=True):
        with st.form("new_campaign"):
            name = st.text_input("📌 Campaign Name")
            hashtag_pairs = st.text_area("🏷️ Hashtag Pairs (one pair per line, separate hashtags with comma)")
            accounts_to_track = st.text_area("👥 Accounts to Track (one per line)")

            if st.form_submit_button("🚀 Create Campaign"):
                if name and hashtag_pairs:
                    raw_pairs = [line.strip() for line in hashtag_pairs.splitlines() if line.strip()]
                    valid_pairs = []
                    invalid_lines = []

                    for line in raw_pairs:
                        parts = [p.strip() for p in line.split(",") if p.strip()]
                        if 2 <= len(parts) <= 3:
                            valid_pairs.append(parts[:3])
                        else:
                            invalid_lines.append(line)

                    if not valid_pairs:
                        st.error("❌ No valid hashtag pairs were provided. (Each line must contain 2 or 3 hashtags separated by commas)")
                    else:
                        if invalid_lines:
                            st.warning(f"⚠️ Some lines were ignored due to incorrect format: {', '.join(invalid_lines)}")

                        accounts = [acc.strip() for acc in accounts_to_track.splitlines() if acc.strip()]
                        db.campaigns.insert_one({
                            "name": name,
                            "hashtag_pairs": valid_pairs,
                            "accounts_to_track": accounts,
                            "created_at": datetime.utcnow(),
                            "updated_at": datetime.utcnow(),
                            "active": True
                        })
                        st.success("✅ Campaign created successfully!")
                        st.rerun()
                else:
                    st.error("❌ Campaign name and at least one hashtag pair are required")


    st.subheader("📋 Active Campaigns")
    campaigns = list(db.campaigns.find({"active": True}))

    if not campaigns:
        st.info("ℹ️ No active campaigns found")
    else:
        for campaign in campaigns:
            is_editing = 'editing_campaign' in st.session_state and st.session_state.editing_campaign == campaign['_id']

            with st.expander(f"{campaign['name']} (📅 Created: {campaign['created_at'].strftime('%Y-%m-%d')})",
                             expanded=is_editing):

                if not is_editing:
                    col1, col2 = st.columns([3, 1])

                    with col1:
                        st.markdown("**🏷️ Hashtag Pairs:**")
                        for pair in campaign['hashtag_pairs']:
                            st.write(f"- `{pair[0].strip()} + {pair[1].strip()}`")

                        st.write(f"**👥 Accounts Tracked:** {len(campaign['accounts_to_track'])}")
                        if campaign['accounts_to_track']:
                            st.markdown("**📍 Tracked Accounts:**")
                            for acc in campaign['accounts_to_track']:
                                st.write(f"- {acc}")

                        st.write(f"**🕒 Created:** {campaign['created_at'].strftime('%Y-%m-%d %H:%M')}")
                        st.write(f"**🔄 Last Updated:** {campaign['updated_at'].strftime('%Y-%m-%d %H:%M')}")

                    with col2:
                        if st.button("✏️ Edit", key=f"edit_{campaign['_id']}"):
                            st.session_state.editing_campaign = campaign['_id']
                            st.rerun()

                        if st.button("🚫 Deactivate", key=f"deactivate_{campaign['_id']}"):
                            db.campaigns.update_one(
                                {"_id": campaign["_id"]},
                                {"$set": {"active": False, "updated_at": datetime.utcnow()}}
                            )
                            st.success("✅ Campaign deactivated!")
                            st.rerun()

                        if st.button("🗑️ Delete", key=f"delete_{campaign['_id']}"):
                            db.campaigns.delete_one({"_id": campaign["_id"]})
                            st.success("🗑️ Campaign deleted!")
                            st.rerun()

                else:
                    with st.form(f"edit_form_{campaign['_id']}"):
                        new_name = st.text_input("📌 Campaign Name", value=campaign['name'])

                        current_pairs = "\n".join([f"{pair[0]},{pair[1]}" for pair in campaign['hashtag_pairs']])
                        new_hashtag_pairs = st.text_area(
                            "🏷️ Hashtag Pairs (one pair per line, separate hashtags with comma)",
                            value=current_pairs
                        )

                        current_accounts = "\n".join(campaign['accounts_to_track'])
                        new_accounts_to_track = st.text_area(
                            "👥 Accounts to Track (one per line)",
                            value=current_accounts
                        )

                        col1, col2 = st.columns(2)
                        with col1:
                            if st.form_submit_button("💾 Update Campaign"):
                                if new_name and new_hashtag_pairs:
                                    pairs = [line.split(",")[:2] for line in new_hashtag_pairs.splitlines() if line.strip()]
                                    accounts = [acc.strip() for acc in new_accounts_to_track.splitlines() if acc.strip()]

                                    db.campaigns.update_one(
                                        {"_id": campaign["_id"]},
                                        {"$set": {
                                            "name": new_name,
                                            "hashtag_pairs": pairs,
                                            "accounts_to_track": accounts,
                                            "updated_at": datetime.utcnow()
                                        }}
                                    )
                                    st.success("✅ Campaign updated successfully!")
                                    del st.session_state.editing_campaign
                                    st.rerun()
                                else:
                                    st.error("❌ Campaign name and at least one hashtag pair are required")

                        with col2:
                            if st.form_submit_button("❎ Cancel"):
                                del st.session_state.editing_campaign
                                st.rerun()



if 'script_process' not in st.session_state:
    st.session_state.script_process = None

if selected_page == "Run Script":
    st.header("Script Execution")


    def run_my_script():
        if st.session_state.script_process is None:
            process = subprocess.Popen([sys.executable, os.path.join(os.getcwd(), "twitter_bot.py")])
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
    st.header("📈 Hashtag Activity Surges")

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
            st.info("✅ No surge activity detected for this campaign")
        else:
            st.markdown("### ⚠️ Recent Surge Alerts")
            for alert in activity_data:
                st.warning(
                    f"🔥 Surge on `{'+'.join(alert['hashtag_pair'])}` - "
                    f"{alert['post_count']} posts on {alert['date'].strftime('%Y-%m-%d')}"
                )

            st.markdown("---")
            st.subheader("📊 Activity Trends Over Time")

            trend_data = list(db.hashtag_activity.find(
                {"campaign_id": campaign_id}
            ).sort("date", 1))

            if trend_data:
                df = pd.DataFrame(trend_data)
                df["hashtag_pair_str"] = df["hashtag_pair"].apply(lambda x: "+".join(x))

                fig = px.line(
                    df,
                    x="date",
                    y="post_count",
                    color="hashtag_pair_str",
                    title="📉 Daily Post Volume by Hashtag Pair",
                    labels={
                        "date": "Date",
                        "post_count": "Post Count",
                        "hashtag_pair_str": "Hashtag Pair"
                    },
                    markers=True
                )
                fig.update_layout(legend_title_text="Hashtag Pair", margin=dict(t=50, b=30))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No hashtag activity data available.")


elif selected_page == "Flagged Accounts":
    st.header("🚩 Flagged Accounts")

    campaigns = list(db.campaigns.find({"active": True}))

    if not campaigns:
        st.info("No active campaigns to display")
    else:
        campaign_id = st.selectbox(
            "Select Campaign",
            options=[c["_id"] for c in campaigns],
            format_func=lambda x: next(c["name"] for c in campaigns if c["_id"] == x)
        )

        flagged_accounts = list(
            db.flagged_accounts.find({"campaign_id": campaign_id}).sort("last_detected", -1)
        )

        if not flagged_accounts:
            st.info("No accounts flagged for this campaign")
        else:
            st.success(f"🚨 Found {len(flagged_accounts)} flagged accounts")

            for account in flagged_accounts:
                with st.expander(f"@{account['username']} — 📝 {account['post_count']} Posts"):
                    col1, col2 = st.columns(2)

                    with col1:
                        st.markdown(f"**🕒 First Detected:** {account['first_detected'].strftime('%Y-%m-%d %H:%M')}")
                    with col2:
                        st.markdown(f"**🕒 Last Detected:** {account['last_detected'].strftime('%Y-%m-%d %H:%M')}")

                    st.markdown("---")
                    st.subheader("📰 Recent Posts")

                    posts = list(
                        db.posts.find({"_id": {"$in": account["posts"]}})
                        .sort("timestamp", -1)
                        .limit(5)
                    )

                    if not posts:
                        st.warning("No recent posts found for this account.")
                    else:
                        for post in posts:
                            with st.container():
                                st.markdown(f"**🗓️ {post['timestamp'].strftime('%Y-%m-%d %H:%M')}**")
                                st.write(post.get("caption", "_No caption available_"))

                                col1, col2, col3 = st.columns(3)
                                col1.metric("👍 Likes", post.get("likes", 0))
                                col2.metric("💬 Comments", post.get("comments", 0))
                                col3.metric("🔁 Retweets", post.get("retweets", 0))

                                st.markdown(f"[🔗 View on Twitter]({post.get('url', '#')})")
                                st.markdown("---")


elif selected_page == "Scraped Posts":
    st.header("📥 Scraped Posts")

    campaigns = list(db.campaigns.find({"active": True}))

    if not campaigns:
        st.info("No active campaigns to display")
    else:
        campaign_id = st.selectbox(
            "Select Campaign",
            options=[c["_id"] for c in campaigns],
            format_func=lambda x: next(c["name"] for c in campaigns if c["_id"] == x)
        )

        posts = list(db.posts.find({"campaign_id": campaign_id}).sort("timestamp", -1))

        if not posts:
            st.info("No posts found for this campaign")
        else:
            st.success(f"✅ Found {len(posts)} posts")

            st.subheader("🕒 Recent Posts")
            for post in posts:
                with st.container():
                    st.markdown("---")
                    st.markdown(f"### 🧑 @{post.get('username', 'unknown')}")
                    st.markdown(f"**🗓️ {post['timestamp'].strftime('%Y-%m-%d %H:%M')}**")

                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.write(post.get("caption", ""))
                        if post.get("hashtags"):
                            hashtags = " ".join([f"#{h}" for h in post['hashtags']])
                            st.markdown(f"**Hashtags:** {hashtags}")

                    with col2:
                        st.metric("👍 Likes", post.get("likes", 0))
                        st.metric("💬 Comments", post.get("comments", 0))
                        st.metric("🔁 Retweets", post.get("retweets", 0))

                    st.markdown(f"[🔗 View on Twitter]({post.get('url', '#')})")


elif selected_page == "Summary Metrics":
    st.markdown("""
    <style>
        .metric-card {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 15px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 15px;
        }
        .metric-title {
            font-size: 12px;
            color: #6c757d;
            font-weight: 600;
        }
        .metric-value {
            font-size: 20px;
            color: #212529;
            font-weight: 700;
        }
        @media (max-width: 768px) {
            .metric-card {
                padding: 10px;
                margin-bottom: 10px;
            }
            .metric-value {
                font-size: 18px;
            }
            .stRadio > div {
                flex-direction: row !important;
                gap: 10px;
            }
            .st-bd {
                padding: 0.5rem;
            }
        }
    </style>
    """, unsafe_allow_html=True)

    st.header("Summary Metrics")

    campaigns = list(db.campaigns.find({"active": True}))

    if not campaigns:
        st.info("No active campaigns")
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

        cols = st.columns(2)
        with cols[0]:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-title">Total Posts</div>
                <div class="metric-value">{total_posts:,}</div>
            </div>
            """, unsafe_allow_html=True)

            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-title">Flagged Accounts</div>
                <div class="metric-value">{flagged_accounts:,}</div>
            </div>
            """, unsafe_allow_html=True)

        with cols[1]:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-title">Unique Accounts</div>
                <div class="metric-value">{unique_accounts:,}</div>
            </div>
            """, unsafe_allow_html=True)

            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-title">Surge Alerts</div>
                <div class="metric-value">{surge_alerts:,}</div>
            </div>
            """, unsafe_allow_html=True)

        st.subheader("Daily Activity Trends")
        date_range = st.radio("Time Period", ["7 Days", "30 Days"], horizontal=True)
        days = 7 if date_range == "7 Days" else 30

        start_date = datetime.utcnow() - timedelta(days=days)
        pipeline = [
            {"$match": {"campaign_id": campaign_id, "timestamp": {"$gte": start_date}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                "posts": {"$sum": 1},
                "unique_users": {"$addToSet": "$username"}
            }},
            {"$addFields": {"unique_users_count": {"$size": "$unique_users"}}},
            {"$sort": {"_id": 1}}
        ]
        daily_activity = list(db.posts.aggregate(pipeline))

        if daily_activity:
            df = pd.DataFrame(daily_activity)
            df['date'] = pd.to_datetime(df['_id'])
            df = df.rename(columns={'posts': 'Daily Posts'})

            tab1, tab2 = st.tabs(["Posts Volume", "Engagement"])

            with tab1:
                fig = px.area(
                    df,
                    x='date',
                    y='Daily Posts',
                    title="Daily Post Volume",
                    template='plotly_white',
                    height=300
                )
                fig.update_traces(line=dict(width=2.5), fill='tozeroy', opacity=0.7)
                st.plotly_chart(fig, use_container_width=True)

                fig2 = px.bar(
                    df,
                    x='date',
                    y='Daily Posts',
                    title="Posts by Day",
                    color='Daily Posts',
                    color_continuous_scale='Blues',
                    height=300
                )
                st.plotly_chart(fig2, use_container_width=True)

            with tab2:
                fig3 = px.line(
                    df,
                    x='date',
                    y='unique_users_count',
                    title="Daily Unique Users",
                    markers=True,
                    height=300
                )
                st.plotly_chart(fig3, use_container_width=True)

                fig4 = px.scatter(
                    df,
                    x='unique_users_count',
                    y='Daily Posts',
                    trendline="lowess",
                    title="Posts vs Users Correlation",
                    height=300
                )
                st.plotly_chart(fig4, use_container_width=True)
        else:
            st.info("No recent activity")

        st.subheader("Account Distribution")
        fig5 = px.pie(
            names=["Regular Accounts", "Flagged Accounts"],
            values=[unique_accounts - flagged_accounts, flagged_accounts],
            hole=0.4,
            height=300
        )
        st.plotly_chart(fig5, use_container_width=True)

        if surge_alerts > 0:
            st.subheader("Recent Alerts")
            alerts = list(db.hashtag_activity.find(
                {"campaign_id": campaign_id, "is_surge": True}
            ).sort("timestamp", -1).limit(3))

            for alert in alerts:
                with st.expander(f"⚠️ Surge detected on {alert['timestamp'].strftime('%b %d %H:%M')}"):
                    st.metric("Baseline Activity", alert.get('baseline', 0))
                    st.metric("Current Activity", alert.get('current_volume', 0))
                    st.write(f"**Hashtags:** {', '.join(alert.get('hashtags', []))}")