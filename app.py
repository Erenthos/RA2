"""
app.py – Reverse Auction Platform (Multi-Item Version)
Compatible with Neon PostgreSQL & Streamlit Cloud

Environment:
    NEON_URL = "postgresql://user:password@host/dbname"
"""

import os, time, datetime
import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

# --------------------------------------------------------------------
#  CONFIG
# --------------------------------------------------------------------
REFRESH_SEC = 8
NEON_ENV = "NEON_URL"

# --------------------------------------------------------------------
#  DB helpers
# --------------------------------------------------------------------
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

# --------------------------------------------------------------------
#  AUTH
# --------------------------------------------------------------------
def authenticate(email, pwd):
    q = "SELECT * FROM users WHERE email=%s AND password=%s"
    df = run_query(q, (email, pwd))
    return df.iloc[0].to_dict() if not df.empty else None

def logout():
    for k in ["user","role"]:
        if k in st.session_state:
            del st.session_state[k]
    st.rerun()

# --------------------------------------------------------------------
#  BUYER DASHBOARD
# --------------------------------------------------------------------
def buyer_dashboard(user):
    st.title("👩‍💼 Buyer Dashboard")
    st.markdown(f"**Welcome, {user['name']}** ({user['company_name']})")

    tabs = st.tabs(["🧾 Auctions", "📦 Create Auction", "📊 Bids"])

    # ------------------------------------------------ Auctions tab
    with tabs[0]:
        st.subheader("Your Auctions")
        q = """
        SELECT a.id, a.title, a.status, COUNT(ai.id) AS total_items,
               COUNT(DISTINCT b.bidder_id) AS bidders
        FROM auctions a
        LEFT JOIN auction_items ai ON a.id=ai.auction_id
        LEFT JOIN bids b ON a.id=b.auction_id
        WHERE a.created_by=%s
        GROUP BY a.id,a.title,a.status
        ORDER BY a.id;
        """
        df = run_query(q,(user["id"],))
        if df.empty: st.info("No auctions yet.")
        else: st.dataframe(df, use_container_width=True)

    # ------------------------------------------------ Create Auction
    with tabs[1]:
        st.subheader("Create New Auction")
        title = st.text_input("Auction Title")
        desc = st.text_area("Description")
        currency = st.selectbox("Currency", ["INR","USD","EUR"])
        if st.button("Create Auction"):
            q = """INSERT INTO auctions(title,description,currency,status,created_by)
                   VALUES(%s,%s,%s,'live',%s) RETURNING id"""
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(q,(title,desc,currency,user["id"]))
                    new_id = cur.fetchone()["id"]
                    conn.commit()
            st.success(f"Auction created with ID {new_id}")
            st.rerun()

        st.markdown("### Add Items to Existing Auction")
        aucs = run_query("SELECT id,title FROM auctions WHERE created_by=%s",(user["id"],))
        if not aucs.empty:
            sel = st.selectbox("Select Auction", aucs["id"], format_func=lambda x: f"{x} - {aucs.loc[aucs['id']==x,'title'].iloc[0]}")
            iname = st.text_input("Item Name")
            idesc = st.text_input("Description")
            qty = st.number_input("Quantity",min_value=0.01)
            uom = st.text_input("UOM","Nos")
            base = st.number_input("Base Price",min_value=0.0)
            if st.button("Add Item"):
                q = """INSERT INTO auction_items(auction_id,item_name,description,quantity,uom,base_price)
                       VALUES(%s,%s,%s,%s,%s,%s)"""
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(q,(sel,iname,idesc,qty,uom,base))
                        conn.commit()
                st.success("Item added successfully!")

    # ------------------------------------------------ Bids tab
    with tabs[2]:
        st.subheader("View Bids (All Suppliers)")
        aucs = run_query("SELECT id,title FROM auctions WHERE created_by=%s",(user["id"],))
        if aucs.empty:
            st.info("No auctions yet.")
        else:
            sel = st.selectbox("Select Auction", aucs["id"], format_func=lambda x: aucs.loc[aucs['id']==x,'title'].iloc[0])
            q = """
            SELECT ai.item_name, ai.quantity, ai.uom,
                   b.bid_amount, b.bid_time, u.company_name
            FROM bids b
            JOIN auction_items ai ON b.item_id=ai.id
            JOIN users u ON b.bidder_id=u.id
            WHERE b.auction_id=%s
            ORDER BY ai.id,b.bid_amount ASC;
            """
            df = run_query(q,(sel,))
            if df.empty: st.info("No bids yet.")
            else: st.dataframe(df,use_container_width=True)

# --------------------------------------------------------------------
#  SUPPLIER DASHBOARD
# --------------------------------------------------------------------
def supplier_dashboard(user):
    st.title("🏭 Supplier Dashboard")
    st.markdown(f"**Welcome, {user['company_name']}**")
    tabs = st.tabs(["🔎 Live Auctions","💰 Place Bids"])

    # ------------------------------------------------ Live auctions
    with tabs[0]:
        q = """
        SELECT a.id,a.title,a.currency,COUNT(ai.id) AS items
        FROM auctions a
        JOIN auction_items ai ON a.id=ai.auction_id
        WHERE a.status='live'
        GROUP BY a.id,a.title,a.currency;
        """
        df = run_query(q)
        if df.empty: st.info("No live auctions right now.")
        else: st.dataframe(df,use_container_width=True)

    # ------------------------------------------------ Place bids
    with tabs[1]:
        aucs = run_query("SELECT id,title FROM auctions WHERE status='live'")
        if aucs.empty:
            st.info("No active auctions.")
        else:
            sel = st.selectbox("Select Auction", aucs["id"],
                               format_func=lambda x: aucs.loc[aucs['id']==x,'title'].iloc[0])
            q = """
            SELECT ai.id, ai.item_name, ai.quantity, ai.uom, ai.base_price,
                   v.lowest_bid
            FROM auction_items ai
            LEFT JOIN v_lowest_bids_per_item v ON ai.id=v.item_id
            WHERE ai.auction_id=%s
            ORDER BY ai.id;
            """
            df = run_query(q,(sel,))
            if df.empty:
                st.info("No items found.")
            else:
                st.dataframe(df,use_container_width=True)

                item = st.selectbox("Select Item", df["id"],
                                    format_func=lambda x: df.loc[df["id"]==x,"item_name"].iloc[0])
                amt = st.number_input("Your Bid Amount",min_value=0.0)
                if st.button("Submit Bid"):
                    q = """INSERT INTO bids(auction_id,item_id,bidder_id,bid_amount)
                           VALUES(%s,%s,%s,%s)"""
                    try:
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute(q,(sel,item,user["id"],amt))
                                conn.commit()
                        st.success("Bid placed successfully!")
                        st.experimental_rerun()
                    except Exception as e:
                        st.error(f"Error placing bid: {e}")

# --------------------------------------------------------------------
#  MAIN
# --------------------------------------------------------------------
st.set_page_config(page_title="Reverse Auction Platform", layout="wide")

if "user" not in st.session_state:
    st.title("🔐 Login")
    email = st.text_input("Email")
    pwd = st.text_input("Password", type="password")
    if st.button("Login"):
        u = authenticate(email,pwd)
        if not u:
            st.error("Invalid credentials")
        else:
            st.session_state["user"] = u
            st.session_state["role"] = u["role"]
            st.rerun()
else:
    user = st.session_state["user"]
    st.sidebar.success(f"Logged in as {user['name']} ({user['role']})")
    if st.sidebar.button("Logout"):
        logout()

    if user["role"] == "buyer":
        buyer_dashboard(user)
    else:
        supplier_dashboard(user)

# periodic refresh
if "last_refresh" not in st.session_state: st.session_state["last_refresh"]=time.time()
if time.time()-st.session_state["last_refresh"]>REFRESH_SEC:
    st.session_state["last_refresh"]=time.time()
    st.rerun()
