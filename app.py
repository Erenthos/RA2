"""
app.py
Streamlit Reverse Auction (RA) Platform using Neon PostgreSQL as backend.

Usage:
1. Add your Neon connection string as environment variable or Streamlit secret:
   NEON_URL = "postgresql://user:password@host/dbname"
2. Run locally: streamlit run app.py
3. Or deploy on Streamlit Cloud (add NEON_URL under Secrets)
"""

import os
import time
import datetime
import streamlit as st
import psycopg2
import pandas as pd
from psycopg2.extras import RealDictCursor

# ------------------- CONFIG -------------------
REFRESH_SECONDS = 5
NEON_ENV_VAR = "NEON_URL"

# ------------- DATABASE FUNCTIONS -------------
def get_neon_url():
    neon = os.getenv(NEON_ENV_VAR) or st.secrets.get(NEON_ENV_VAR, None)
    if not neon:
        st.error(f"Missing Neon connection string. Add {NEON_ENV_VAR} to Streamlit Secrets.")
        st.stop()
    return neon

def get_conn():
    return psycopg2.connect(get_neon_url(), cursor_factory=RealDictCursor)

def fetch_auctions():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM auctions ORDER BY id;")
            rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def fetch_bids(auction_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT bidder_name, bid_amount, bid_time FROM bids WHERE auction_id=%s ORDER BY bid_amount ASC, bid_time ASC;",
                (auction_id,),
            )
            rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def place_bid(auction_id, bidder_name, bid_amount):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT base_price FROM auctions WHERE id=%s;", (auction_id,))
            base = cur.fetchone()
            if not base:
                return {"ok": False, "msg": "Auction not found."}
            cur.execute(
                "SELECT bid_amount FROM bids WHERE auction_id=%s ORDER BY bid_amount ASC LIMIT 1;",
                (auction_id,),
            )
            lowest = cur.fetchone()
            if lowest and bid_amount >= lowest["bid_amount"]:
                return {"ok": False, "msg": f"Bid must be lower than current lowest ({lowest['bid_amount']})."}
            if not lowest and bid_amount >= base["base_price"]:
                return {"ok": False, "msg": f"First bid must be lower than base price ({base['base_price']})."}
            cur.execute(
                "INSERT INTO bids (auction_id, bidder_name, bid_amount, bid_time) VALUES (%s, %s, %s, NOW()) RETURNING id;",
                (auction_id, bidder_name, bid_amount),
            )
            conn.commit()
            return {"ok": True, "msg": "✅ Bid placed successfully!"}

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="Reverse Auction Platform", layout="wide")
st.title("🔻 Reverse Auction — Streamlit + Neon PostgreSQL")

# Sidebar user info
with st.sidebar:
    st.subheader("👤 Your Info")
    name = st.text_input("Your name", key="username")
    refresh = st.slider("Auto-refresh (seconds)", 2, 30, REFRESH_SECONDS)
    st.write("DB connection from NEON_URL secret.")

# Fetch auctions
auctions = fetch_auctions()
if auctions.empty:
    st.warning("No auctions found in the database.")
    st.stop()

col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("Available Auctions")
    st.dataframe(auctions[["id", "title", "status", "base_price"]])
    selected_id = st.selectbox("Select Auction ID", auctions["id"].tolist())
    sel = auctions[auctions["id"] == selected_id].iloc[0]
    st.markdown(f"### {sel['title']}")
    st.caption(sel.get("description", ""))
    st.write(f"Status: {sel['status']} | Base Price: {sel['base_price']}")

with col2:
    st.subheader("Live Bids")
    bids = fetch_bids(selected_id)
    if not bids.empty:
        st.metric("Current Lowest Bid", f"{bids.iloc[0]['bid_amount']}")
        st.table(bids)
    else:
        st.info("No bids yet.")

    st.markdown("---")
    st.subheader("Place a Bid")
    bid_value = st.number_input("Bid amount", min_value=0.0, step=0.01)
    if st.button("Submit Bid"):
        if not name:
            st.warning("Enter your name first.")
        else:
            result = place_bid(selected_id, name, float(bid_value))
            if result["ok"]:
                st.success(result["msg"])
                st.experimental_rerun()
            else:
                st.error(result["msg"])

st.caption(f"Auto-refresh every {refresh} seconds | {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

if "last_refresh" not in st.session_state:
    st.session_state["last_refresh"] = time.time()
if time.time() - st.session_state["last_refresh"] > refresh:
    st.session_state["last_refresh"] = time.time()
    st.experimental_rerun()
