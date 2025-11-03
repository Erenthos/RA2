"""
Reverse Auction Platform — Final Stable Release
Streamlit + Neon PostgreSQL
-----------------------------------------------
✅ Buyer & Supplier roles
✅ Bulk bidding
✅ Live updates without logout
✅ Auto-close expired auctions
✅ Duration frozen after start
Requires NEON_URL in secrets.
"""

import os
import time
import datetime
import math
import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

# ---------------- CONFIG ----------------
REFRESH_SEC = 3
NEON_ENV = "NEON_URL"

# ---------------- DB HELPERS ----------------
def get_conn():
    url = os.getenv(NEON_ENV) or st.secrets.get(NEON_ENV)
    if not url:
        st.error("Missing NEON_URL secret/env variable")
        st.stop()
    return psycopg2.connect(url, cursor_factory=RealDictCursor)

def run_query(query, params=None, fetch=True):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            if fetch:
                rows = cur.fetchall()
                return pd.DataFrame(rows) if rows else pd.DataFrame()

# ---------------- UTILITIES ----------------
def clean_company(name: str) -> str:
    if not name:
        return ""
    return (
        name.replace("Pvt Ltd", "")
        .replace("Private Limited", "")
        .replace("Ltd", "")
        .strip()
    )

def is_multiple_of(step: float, diff: float, tol=1e-9) -> bool:
    if step == 0:
        return True
    if diff <= 0:
        return False
    ratio = diff / step
    return abs(round(ratio) - ratio) < tol

# ---------------- AUTH ----------------
def authenticate(email, pwd):
    q = "SELECT * FROM users WHERE email=%s AND password=%s"
    df = run_query(q, (email, pwd))
    return df.iloc[0].to_dict() if not df.empty else None

def create_account(name, email, password, role, company):
    q = """INSERT INTO users(name,email,password,role,company_name)
           VALUES(%s,%s,%s,%s,%s)
           ON CONFLICT (email) DO NOTHING RETURNING id"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(q, (name, email, password, role, company))
            row = cur.fetchone()
            conn.commit()
            return row["id"] if row else None

def logout():
    for k in ["user", "role"]:
        st.session_state.pop(k, None)
    st.rerun()

# ---------------- AUTO-CLOSE EXPIRED AUCTIONS ----------------
def auto_close_expired():
    q = "UPDATE auctions SET status='closed' WHERE status='live' AND end_time <= (NOW() AT TIME ZONE 'UTC')"
    run_query(q, fetch=False)

# ---------------- BUYER DASHBOARD ----------------
def buyer_dashboard(user):
    auto_close_expired()  # housekeeping on load
    company_clean = clean_company(user.get("company_name", ""))
    st.title("👩‍💼 Buyer Dashboard")
    st.markdown(f"**Welcome, {user['name']}** ({company_clean})")

    tabs = st.tabs(["🧾 Auctions", "📦 Create Auction", "📊 View Bids"])

    # ---------- Auctions Tab ----------
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

        st.markdown("### Manage Auction Status")
        aucs = run_query("SELECT id,title,status,end_time FROM auctions WHERE created_by=%s", (user["id"],))
        if not aucs.empty:
            sel = st.selectbox(
                "Select Auction to Manage",
                aucs["id"],
                format_func=lambda x: f"{x} - {aucs.loc[aucs['id']==x,'title'].iloc[0]}",
                key="manage_select"
            )
            row = aucs.loc[aucs["id"] == sel].iloc[0]
            status = row["status"]
            st.write(f"**Current Status:** {status}")

            # Allow start/close only if not already live/closed
            col1, col2 = st.columns(2)
            with col1:
                if status == "scheduled" and st.button("▶️ Start Auction Now", key=f"start_{sel}"):
                    q = """
                    UPDATE auctions
                    SET status='live',
                        start_time=NOW(),
                        end_time=NOW() + make_interval(mins := 10)
                    WHERE id=%s RETURNING id;
                    """
                    res = run_query(q, (sel,))
                    if res.empty:
                        st.error("Failed to start auction.")
                    else:
                        st.success("Auction started for 10 minutes.")
                        st.rerun()
            with col2:
                if status == "live" and st.button("⏹️ Close Auction", key=f"close_{sel}"):
                    run_query("UPDATE auctions SET status='closed' WHERE id=%s", (sel,), fetch=False)
                    st.warning("Auction closed manually.")
                    st.rerun()
            if status == "live":
                end = row["end_time"]
                st.info(f"⏳ This auction will auto-close at: **{end}**")

    # ---------- Create Auction ----------
    with tabs[1]:
        st.subheader("Create New Auction")
        title = st.text_input("Auction Title", key="new_title")
        desc = st.text_area("Description", key="new_desc")
        currency = st.selectbox("Currency", ["INR", "USD", "EUR"], key="new_curr")
        duration = st.number_input("Default Duration (minutes)", min_value=1, max_value=1440, value=10, key="new_dur")
        min_dec = st.number_input("Minimum Bid Decrement (X)", min_value=0.0, value=0.0, key="new_dec")
        if st.button("Create Auction", key="create_btn"):
            q = """
            INSERT INTO auctions(title,description,currency,status,created_by,start_time,end_time,min_decrement)
            VALUES(%s,%s,%s,'scheduled',%s,NOW(),NOW() + make_interval(mins := %s),%s)
            RETURNING id;
            """
            df = run_query(q, (title, desc, currency, user["id"], duration, min_dec))
            if df.empty:
                st.error("Failed to create auction.")
            else:
                st.success(f"Auction created with ID {df.iloc[0]['id']}. Add items below.")
                st.rerun()

        st.markdown("### Add Items")
        aucs = run_query("SELECT id,title FROM auctions WHERE created_by=%s AND status='scheduled'", (user["id"],))
        if aucs.empty:
            st.info("Only scheduled auctions can accept new items.")
        else:
            sel = st.selectbox(
                "Select Auction",
                aucs["id"],
                format_func=lambda x: aucs.loc[aucs['id']==x,'title'].iloc[0],
                key="add_select"
            )
            iname = st.text_input("Item Name", key="itm_name")
            idesc = st.text_input("Description", key="itm_desc")
            qty = st.number_input("Quantity", min_value=1.0, key="itm_qty")
            uom = st.text_input("UOM", "Nos", key="itm_uom")
            base = st.number_input("Base Price", min_value=0.0, key="itm_base")
            if st.button("Add Item", key="add_btn"):
                q = """INSERT INTO auction_items(auction_id,item_name,description,quantity,uom,base_price)
                       VALUES(%s,%s,%s,%s,%s,%s)"""
                run_query(q, (sel, iname, idesc, qty, uom, base), fetch=False)
                st.success("Item added successfully!")

    # ---------- Live Bid View ----------
    with tabs[2]:
        st.subheader("📊 Live Bids")
        aucs = run_query("SELECT id,title FROM auctions WHERE created_by=%s", (user["id"],))
        if aucs.empty:
            st.info("No auctions found.")
        else:
            sel = st.selectbox(
                "Select Auction",
                aucs["id"],
                format_func=lambda x: aucs.loc[aucs['id']==x,'title'].iloc[0],
                key="bid_select"
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
            placeholder = st.empty()
            for _ in range(200):
                df = run_query(q, (sel,))
                if df.empty:
                    placeholder.info("No bids yet.")
                else:
                    placeholder.dataframe(df, use_container_width=True)
                time.sleep(REFRESH_SEC)
                if st.session_state.get("role") != "buyer":
                    break

# ---------------- SUPPLIER DASHBOARD ----------------
def supplier_dashboard(user):
    auto_close_expired()
    company_clean = clean_company(user.get("company_name", ""))
    st.title("🏭 Supplier Dashboard")
    st.markdown(f"**Welcome, {company_clean}**")

    tabs = st.tabs(["🔎 Live Auctions", "💰 Place Bids"])

    # ---------- Live Auctions ----------
    with tabs[0]:
        st.subheader("Live Auctions (auto-refresh)")
        placeholder = st.empty()
        for _ in range(200):
            q = """
            SELECT a.id,a.title,a.currency,a.end_time,COUNT(ai.id) AS items
            FROM auctions a
            JOIN auction_items ai ON a.id=ai.auction_id
            WHERE a.status='live'
              AND a.end_time > (NOW() AT TIME ZONE 'UTC' - INTERVAL '1 minute')
            GROUP BY a.id,a.title,a.currency,a.end_time
            ORDER BY a.id;
            """
            df = run_query(q)
            if df.empty:
                placeholder.info("No live auctions right now.")
            else:
                df["time_left"] = pd.to_datetime(df["end_time"]) - datetime.datetime.utcnow()
                placeholder.dataframe(df, use_container_width=True)
            time.sleep(REFRESH_SEC)
            if st.session_state.get("role") != "supplier":
                break

    # ---------- Place Bids ----------
    with tabs[1]:
        aucs = run_query("SELECT id,title FROM auctions WHERE status='live' AND end_time>(NOW() AT TIME ZONE 'UTC')")
        if aucs.empty:
            st.info("No active auctions.")
            return
        sel = st.selectbox(
            "Select Auction",
            aucs["id"],
            format_func=lambda x: aucs.loc[aucs['id']==x,'title'].iloc[0],
            key="sup_bid_select"
        )

        dec_df = run_query("SELECT COALESCE(min_decrement,0) AS min_dec FROM auctions WHERE id=%s", (sel,))
        min_dec = float(dec_df.iloc[0]["min_dec"]) if not dec_df.empty else 0.0
        if min_dec:
            st.info(f"Minimum bid decrement: {min_dec}")

        q_items = """
        SELECT ai.id, ai.item_name, ai.quantity, ai.uom, ai.base_price,
               v.lowest_bid
        FROM auction_items ai
        LEFT JOIN v_lowest_bids_per_item v ON ai.id=v.item_id
        WHERE ai.auction_id=%s
        ORDER BY ai.id;
        """
        df = run_query(q_items, (sel,))
        if df.empty:
            st.info("No items found.")
            return

        st.markdown("### Enter Your Bids (edit & select items to submit)")
        if "bulk_edits" not in st.session_state:
            st.session_state["bulk_edits"] = {}
        edits = st.session_state["bulk_edits"].get(sel, {})
        df["your_bid"] = [edits.get(iid, {}).get("your_bid", None) for iid in df["id"]]
        df["select"] = [edits.get(iid, {}).get("select", False) for iid in df["id"]]

        edited = st.data_editor(
            df[["id","item_name","quantity","uom","base_price","lowest_bid","your_bid","select"]],
            column_config={
                "id": st.column_config.HiddenColumn("id"),
                "item_name": st.column_config.TextColumn("Item", disabled=True),
                "quantity": st.column_config.NumberColumn("Qty", disabled=True),
                "uom": st.column_config.TextColumn("UOM", disabled=True),
                "base_price": st.column_config.NumberColumn("Base", disabled=True),
                "lowest_bid": st.column_config.NumberColumn("Lowest", disabled=True),
                "your_bid": st.column_config.NumberColumn("Your Bid"),
                "select": st.column_config.CheckboxColumn("Select"),
            },
            use_container_width=True,
            num_rows="dynamic",
            key=f"edit_{sel}",
        )

        # persist edits
        new_edits = {}
        for _, r in edited.iterrows():
            iid = int(r["id"])
            new_edits[iid] = {
                "your_bid": None if pd.isna(r["your_bid"]) else float(r["your_bid"]),
                "select": bool(r["select"]),
            }
        st.session_state["bulk_edits"][sel] = new_edits

        if st.button("Submit Selected Bids", key=f"submit_{sel}"):
            bids = []
            for iid, d in new_edits.items():
                if not d["select"] or d["your_bid"] is None:
                    continue
                your_bid = d["your_bid"]
                low_df = run_query("SELECT MIN(bid_amount) as lowest FROM bids WHERE item_id=%s", (iid,))
                lowest = float(low_df.iloc[0]["lowest"]) if not low_df.empty and low_df.iloc[0]["lowest"] else None
                base_df = run_query("SELECT base_price FROM auction_items WHERE id=%s", (iid,))
                base_price = float(base_df.iloc[0]["base_price"]) if not base_df.empty else 0.0
                if lowest:
                    if your_bid >= lowest:
                        st.error(f"Item {iid}: must be lower than current lowest ({lowest})")
                        continue
                    if min_dec and not is_multiple_of(min_dec, lowest - your_bid):
                        st.error(f"Item {iid}: decrement must be multiple of {min_dec}")
                        continue
                else:
                    if your_bid >= base_price:
                        st.error(f"Item {iid}: first bid must be below base ({base_price})")
                        continue
                bids.append((sel, iid, user["id"], your_bid))
            if bids:
                try:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.executemany(
                                "INSERT INTO bids(auction_id,item_id,bidder_id,bid_amount) VALUES(%s,%s,%s,%s)",
                                bids,
                            )
                            conn.commit()
                    st.success(f"{len(bids)} bids submitted successfully.")
                    st.session_state["bulk_edits"][sel] = {}
                    st.rerun()
                except Exception as e:
                    st.error(f"DB Error: {e}")
            else:
                st.warning("No valid bids selected.")

# ---------------- MAIN ----------------
st.set_page_config(page_title="Reverse Auction Platform", layout="wide")

if "user" not in st.session_state:
    tab1, tab2 = st.tabs(["🔐 Login", "🆕 Sign Up"])
    with tab1:
        email = st.text_input("Email", key="login_email")
        pwd = st.text_input("Password", type="password", key="login_pwd")
        if st.button("Login", key="login_btn"):
            u = authenticate(email, pwd)
            if not u:
                st.error("Invalid credentials.")
            else:
                st.session_state["user"] = u
                st.session_state["role"] = u["role"]
                st.rerun()
    with tab2:
        name = st.text_input("Full Name", key="signup_name")
        email = st.text_input("Email Address", key="signup_email")
        pwd = st.text_input("Password", type="password", key="signup_pwd")
        role = st.selectbox("Role", ["buyer", "supplier"], key="signup_role")
        company = st.text_input("Company Name", key="signup_company")
        if st.button("Create Account", key="signup_btn"):
            uid = create_account(name, email, pwd, role, company)
            if uid:
                st.success("Account created. Please login.")
            else:
                st.warning("User already exists.")
else:
    user = st.session_state["user"]
    st.sidebar.success(f"Logged in as {user['name']} ({user['role']})")
    if st.sidebar.button("Logout", key="logout_btn"):
        logout()
    if user["role"] == "buyer":
        buyer_dashboard(user)
    else:
        supplier_dashboard(user)
