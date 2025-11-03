"""
Reverse Auction Platform (Buyer & Supplier)
--------------------------------------------
Streamlit + Neon PostgreSQL
Final Version: Live selective refresh + decrement validation + clean UI

Environment variable:
    NEON_URL = "postgresql://user:password@your-neon-host/dbname"
"""

import os, time, datetime
import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

# --------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------
REFRESH_SEC = 3  # selective refresh interval
NEON_ENV = "NEON_URL"

# --------------------------------------------------------------------
# DB HELPERS
# --------------------------------------------------------------------
def get_conn():
    url = os.getenv(NEON_ENV) or st.secrets.get(NEON_ENV)
    if not url:
        st.error("❌ Missing NEON_URL secret/environment variable")
        st.stop()
    return psycopg2.connect(url, cursor_factory=RealDictCursor)

def run_query(query, params=None, fetch=True):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            if fetch:
                rows = cur.fetchall()
                return pd.DataFrame(rows) if rows else pd.DataFrame()

# --------------------------------------------------------------------
# AUTH
# --------------------------------------------------------------------
def authenticate(email, pwd):
    q = "SELECT * FROM users WHERE email=%s AND password=%s"
    df = run_query(q, (email, pwd))
    return df.iloc[0].to_dict() if not df.empty else None

def logout():
    for k in ["user", "role"]:
        if k in st.session_state:
            del st.session_state[k]
    st.rerun()

# --------------------------------------------------------------------
# BUYER DASHBOARD
# --------------------------------------------------------------------
def buyer_dashboard(user):
    company_clean = user["company_name"].replace("Pvt Ltd", "").replace("Private Limited", "").replace("Ltd", "").strip()
    st.title("👩‍💼 Buyer Dashboard")
    st.markdown(f"**Welcome, {user['name']}** ({company_clean})")

    tabs = st.tabs(["🧾 Auctions", "📦 Create Auction", "📊 View Bids"])

    # ------------------------------------------------ Auctions tab
    with tabs[0]:
        st.subheader("Your Auctions")
        q = """
        SELECT a.id, a.title, a.status,
               TO_CHAR(a.start_time, 'YYYY-MM-DD HH24:MI') AS start_time,
               TO_CHAR(a.end_time, 'YYYY-MM-DD HH24:MI') AS end_time,
               a.min_decrement,
               COUNT(ai.id) AS total_items,
               COUNT(DISTINCT b.bidder_id) AS bidders
        FROM auctions a
        LEFT JOIN auction_items ai ON a.id=ai.auction_id
        LEFT JOIN bids b ON a.id=b.auction_id
        WHERE a.created_by=%s
        GROUP BY a.id,a.title,a.status,a.start_time,a.end_time,a.min_decrement
        ORDER BY a.id;
        """
        df = run_query(q, (user["id"],))
        if df.empty:
            st.info("No auctions yet.")
        else:
            st.dataframe(df, use_container_width=True)

        # Auction controls
        st.markdown("### Manage Auction Status")
        aucs = run_query("SELECT id,title,status FROM auctions WHERE created_by=%s", (user["id"],))
        if not aucs.empty:
            sel = st.selectbox(
                "Select Auction (Manage)",
                aucs["id"],
                format_func=lambda x: f"{x} - {aucs.loc[aucs['id']==x,'title'].iloc[0]}",
                key="manage_auction_select"
            )
            current_status = aucs.loc[aucs["id"] == sel, "status"].iloc[0]
            st.write(f"Current status: **{current_status}**")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("▶️ Start Auction"):
                    q = """
                    UPDATE auctions
                    SET status='live',
                        start_time=NOW(),
                        end_time=NOW() + INTERVAL '10 minutes'
                    WHERE id=%s
                    """
                    run_query(q, (sel,), fetch=False)
                    st.success("Auction started (10 minutes).")
                    st.rerun()

            with col2:
                if st.button("⏹️ Close Auction"):
                    run_query("UPDATE auctions SET status='closed' WHERE id=%s", (sel,), fetch=False)
                    st.warning("Auction closed.")
                    st.rerun()

    # ------------------------------------------------ Create Auction
    with tabs[1]:
        st.subheader("Create New Auction")
        title = st.text_input("Auction Title")
        desc = st.text_area("Description")
        currency = st.selectbox("Currency", ["INR", "USD", "EUR"])
        duration = st.number_input("Default Duration (minutes)", min_value=1, max_value=120, value=10)
        min_dec = st.number_input("Minimum Bid Decrement (X)", min_value=0.0, value=0.0, step=0.01,
                                  help="Suppliers must bid in multiples of this decrement.")
        if st.button("Create Auction"):
            q = """
                INSERT INTO auctions(title, description, currency, status, created_by, start_time, end_time, min_decrement)
                VALUES (%s, %s, %s, 'scheduled', %s, NOW(), NOW() + (%s || ' minutes')::interval, %s)
                RETURNING id
            """
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(q, (title, desc, currency, user["id"], str(duration), min_dec))
                    new_id = cur.fetchone()["id"]
                    conn.commit()
            st.success(f"Auction created (ID: {new_id}) — status: scheduled")
            st.rerun()

        st.markdown("### Add Items to Auction")
        aucs = run_query("SELECT id,title FROM auctions WHERE created_by=%s", (user["id"],))
        if not aucs.empty:
            sel = st.selectbox(
                "Select Auction (Add Items)",
                aucs["id"],
                format_func=lambda x: f"{x} - {aucs.loc[aucs['id']==x,'title'].iloc[0]}",
                key="add_item_select"
            )
            iname = st.text_input("Item Name")
            idesc = st.text_input("Description")
            qty = st.number_input("Quantity", min_value=0.01)
            uom = st.text_input("UOM", "Nos")
            base = st.number_input("Base Price", min_value=0.0)
            if st.button("Add Item"):
                q = """INSERT INTO auction_items(auction_id,item_name,description,quantity,uom,base_price)
                       VALUES(%s,%s,%s,%s,%s,%s)"""
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(q, (sel, iname, idesc, qty, uom, base))
                        conn.commit()
                st.success("Item added successfully!")

    # ------------------------------------------------ View Bids (LIVE)
    with tabs[2]:
        st.subheader("📊 View All Bids (Live)")
        aucs = run_query("SELECT id,title FROM auctions WHERE created_by=%s", (user["id"],))
        if aucs.empty:
            st.info("No auctions found.")
        else:
            sel = st.selectbox(
                "Select Auction (View Bids)",
                aucs["id"],
                format_func=lambda x: aucs.loc[aucs['id']==x,'title'].iloc[0],
                key="view_bids_select"
            )
            q = """
            SELECT ai.item_name, ai.quantity, ai.uom,
                   b.bid_amount, b.bid_time, u.company_name
            FROM bids b
            JOIN auction_items ai ON b.item_id=ai.id
            JOIN users u ON b.bidder_id=u.id
            WHERE b.auction_id=%s
            ORDER BY ai.id, b.bid_amount ASC;
            """
            st.markdown("🔄 **Live Refresh Every 3 Seconds**")
            placeholder = st.empty()
            for _ in range(100):
                df = run_query(q, (sel,))
                if df.empty:
                    placeholder.info("No bids yet.")
                else:
                    placeholder.dataframe(df, use_container_width=True)
                time.sleep(REFRESH_SEC)
                if st.session_state.get("role") != "buyer":
                    break

# --------------------------------------------------------------------
# SUPPLIER DASHBOARD
# --------------------------------------------------------------------
def supplier_dashboard(user):
    company_clean = user["company_name"].replace("Pvt Ltd", "").replace("Private Limited", "").replace("Ltd", "").strip()
    st.title("🏭 Supplier Dashboard")
    st.markdown(f"**Welcome, {company_clean}**")
    tabs = st.tabs(["🔎 Live Auctions", "💰 Place Bids"])

    # ------------------------------------------------ Live auctions
    with tabs[0]:
        q = """
        SELECT a.id,a.title,a.currency,a.end_time,COUNT(ai.id) AS items
        FROM auctions a
        JOIN auction_items ai ON a.id=ai.auction_id
        WHERE a.status='live' AND a.end_time>NOW()
        GROUP BY a.id,a.title,a.currency,a.end_time;
        """
        df = run_query(q)
        if df.empty:
            st.info("No live auctions right now.")
        else:
            df["time_left"] = pd.to_datetime(df["end_time"]) - datetime.datetime.utcnow()
            st.dataframe(df, use_container_width=True)

    # ------------------------------------------------ Place bids (LIVE)
    with tabs[1]:
        aucs = run_query("SELECT id,title FROM auctions WHERE status='live' AND end_time>NOW()")
        if aucs.empty:
            st.info("No active auctions.")
        else:
            sel = st.selectbox(
                "Select Auction (Bid)",
                aucs["id"],
                format_func=lambda x: aucs.loc[aucs['id']==x,'title'].iloc[0],
                key="supplier_bid_select"
            )
            q = """
            SELECT ai.id, ai.item_name, ai.quantity, ai.uom, ai.base_price,
                   v.lowest_bid
            FROM auction_items ai
            LEFT JOIN v_lowest_bids_per_item v ON ai.id=v.item_id
            WHERE ai.auction_id=%s
            ORDER BY ai.id;
            """
            st.markdown("🔄 **Live Lowest Bids (3s refresh)**")
            placeholder = st.empty()
            for _ in range(100):
                df = run_query(q, (sel,))
                if df.empty:
                    placeholder.info("No items found.")
                else:
                    placeholder.dataframe(df, use_container_width=True)
                time.sleep(REFRESH_SEC)
                if st.session_state.get("role") != "supplier":
                    break

            item = st.selectbox(
                "Select Item",
                df["id"],
                format_func=lambda x: df.loc[df["id"]==x,"item_name"].iloc[0],
                key="supplier_item_select"
            )
            amt = st.number_input("Your Bid Amount", min_value=0.0)
            if st.button("Submit Bid"):
                # Check decrement rule
                q_dec = "SELECT min_decrement FROM auctions WHERE id=%s"
                dec_df = run_query(q_dec, (sel,))
                min_dec = float(dec_df.iloc[0]["min_decrement"]) if not dec_df.empty else 0.0

                q_low = "SELECT MIN(bid_amount) AS lowest FROM bids WHERE item_id=%s"
                low_df = run_query(q_low, (item,))
                current_lowest = float(low_df.iloc[0]["lowest"]) if not low_df.empty and low_df.iloc[0]["lowest"] else None

                valid = True
                if current_lowest:
                    if amt >= current_lowest:
                        st.error(f"Bid must be lower than current lowest ({current_lowest:.2f}).")
                        valid = False
                    elif min_dec > 0 and abs(current_lowest - amt) % min_dec != 0:
                        st.error(f"Bid must decrease in multiples of {min_dec}. Adjust your amount.")
                        valid = False

                if valid:
                    q = """INSERT INTO bids(auction_id,item_id,bidder_id,bid_amount)
                           VALUES(%s,%s,%s,%s)"""
                    try:
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute(q, (sel, item, user["id"], amt))
                                conn.commit()
                        st.success("✅ Bid placed successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

# --------------------------------------------------------------------
# LOGIN + SIGNUP
# --------------------------------------------------------------------
st.set_page_config(page_title="Reverse Auction Platform", layout="wide")

if "user" not in st.session_state:
    tab_login, tab_signup = st.tabs(["🔐 Login", "🆕 Sign Up"])

    with tab_login:
        email = st.text_input("Email", key="login_email")
        pwd = st.text_input("Password", type="password", key="login_pwd")
        if st.button("Login"):
            u = authenticate(email, pwd)
            if not u:
                st.error("Invalid credentials")
            else:
                st.session_state["user"] = u
                st.session_state["role"] = u["role"]
                st.rerun()

    with tab_signup:
        name = st.text_input("Full Name")
        email = st.text_input("Email Address", key="signup_email")
        pwd = st.text_input("Password", type="password", key="signup_pwd")
        role = st.selectbox("Select Role", ["buyer", "supplier"])
        company = st.text_input("Company Name")
        if st.button("Create Account"):
            q = """INSERT INTO users(name,email,password,role,company_name)
                   VALUES(%s,%s,%s,%s,%s)
                   ON CONFLICT (email) DO NOTHING RETURNING id;"""
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(q, (name, email, pwd, role, company))
                    row = cur.fetchone()
                    conn.commit()
            if row:
                st.success("Account created! Please login.")
            else:
                st.warning("User already exists. Try logging in.")
else:
    user = st.session_state["user"]
    st.sidebar.success(f"Logged in as {user['name']} ({user['role']})")
    if st.sidebar.button("Logout"):
        logout()

    if user["role"] == "buyer":
        buyer_dashboard(user)
    else:
        supplier_dashboard(user)
