"""
app.py — Reverse Auction Platform (Bulk supplier bidding + selective live refresh)
Streamlit + Neon PostgreSQL
Make sure NEON_URL secret/env is set and `min_decrement` column exists in auctions table.
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
    """Return pandas DataFrame or None (if fetch=False)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            if fetch:
                rows = cur.fetchall()
                return pd.DataFrame(rows) if rows else pd.DataFrame()

# ---------------- AUTH ----------------
def authenticate(email, pwd):
    q = "SELECT * FROM users WHERE email=%s AND password=%s"
    df = run_query(q, (email, pwd))
    return df.iloc[0].to_dict() if not df.empty else None

def create_account(name, email, password, role, company):
    q = """INSERT INTO users(name,email,password,role,company_name)
           VALUES(%s,%s,%s,%s,%s) ON CONFLICT (email) DO NOTHING RETURNING id"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(q, (name, email, password, role, company))
            row = cur.fetchone()
            conn.commit()
            return row["id"] if row else None

def logout():
    for k in ["user", "role"]:
        if k in st.session_state:
            del st.session_state[k]
    st.rerun()

# ---------------- UTIL ----------------
def clean_company(name: str) -> str:
    if not name:
        return ""
    out = name.replace("Pvt Ltd", "").replace("Private Limited", "").replace("Ltd", "").strip()
    return out

def is_multiple_of(step: float, diff: float, tol=1e-9) -> bool:
    # returns True if diff is a multiple of step (with small tolerance)
    if step == 0:
        return True
    if diff <= 0:
        return False
    ratio = diff / step
    return abs(round(ratio) - ratio) < tol

# ---------------- BUYER DASHBOARD ----------------
def buyer_dashboard(user):
    company_clean = clean_company(user.get("company_name",""))
    st.title("👩‍💼 Buyer Dashboard")
    st.markdown(f"**Welcome, {user['name']}** ({company_clean})")

    tabs = st.tabs(["🧾 Auctions", "📦 Create Auction", "📊 View Bids"])
    # ---------------- Auctions tab ----------------
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

        # Manage auction: start/close with duration input override
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
            col1, col2, col3 = st.columns([1,1,2])
            with col1:
                start_override = st.number_input("Start duration (minutes) - quick start", min_value=1, max_value=1440, value=10, key="start_dur")
                if st.button("▶️ Start Auction Now"):
                    q = """
                    UPDATE auctions
                    SET status='live',
                        start_time=NOW(),
                        end_time=NOW() + (%s || ' minutes')::interval
                    WHERE id=%s
                    """
                    run_query(q, (str(start_override), sel), fetch=False)
                    st.success(f"Auction {sel} started for {start_override} minutes.")
                    st.rerun()
            with col2:
                if st.button("⏹️ Close Auction"):
                    run_query("UPDATE auctions SET status='closed' WHERE id=%s", (sel,), fetch=False)
                    st.warning("Auction closed.")
                    st.rerun()
            with col3:
                st.write("Tip: You can schedule auctions when creating them.")

    # ---------------- Create Auction ----------------
    with tabs[1]:
        st.subheader("Create New Auction")
        title = st.text_input("Auction Title")
        desc = st.text_area("Description")
        currency = st.selectbox("Currency", ["INR", "USD", "EUR"])
        duration = st.number_input("Default Duration (minutes)", min_value=1, max_value=1440, value=10)
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

    # ---------------- View Bids (LIVE refresh only this section) ----------------
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

            # live refresh of only the bids table
            q = """
            SELECT ai.item_name, ai.quantity, ai.uom,
                   b.bid_amount, b.bid_time, u.company_name
            FROM bids b
            JOIN auction_items ai ON b.item_id=ai.id
            JOIN users u ON b.bidder_id=u.id
            WHERE b.auction_id=%s
            ORDER BY ai.id, b.bid_amount ASC;
            """
            st.markdown("🔄 **Auto-refreshing every 3 seconds**")
            placeholder = st.empty()
            # loop that updates only the placeholder table
            for _ in range(200):  # loops while user stays on this tab (approx 10 minutes)
                df = run_query(q, (sel,))
                if df.empty:
                    placeholder.info("No bids yet.")
                else:
                    placeholder.dataframe(df, use_container_width=True)
                time.sleep(REFRESH_SEC)
                # break early if role changed (user logged out or switched role)
                if st.session_state.get("role") != "buyer":
                    break

# ---------------- SUPPLIER DASHBOARD ----------------
def supplier_dashboard(user):
    company_clean = clean_company(user.get("company_name",""))
    st.title("🏭 Supplier Dashboard")
    st.markdown(f"**Welcome, {company_clean}**")
    tabs = st.tabs(["🔎 Live Auctions", "💰 Place Bids"])

    # ---------------- Live auctions (auto-refresh list only) ----------------
    with tabs[0]:
        st.subheader("Live Auctions (auto-refreshing)")
        auc_placeholder = st.empty()
        # keep updating auction list until user navigates away
        for _ in range(200):
            q = """
            SELECT a.id, a.title, a.currency, a.end_time, COUNT(ai.id) AS items
            FROM auctions a
            JOIN auction_items ai ON a.id=ai.auction_id
            WHERE a.status='live' AND a.end_time>NOW()
            GROUP BY a.id,a.title,a.currency,a.end_time
            ORDER BY a.id;
            """
            df = run_query(q)
            if df.empty:
                auc_placeholder.info("No live auctions right now.")
            else:
                df["time_left"] = pd.to_datetime(df["end_time"]) - datetime.datetime.utcnow()
                auc_placeholder.dataframe(df, use_container_width=True)
            time.sleep(REFRESH_SEC)
            if st.session_state.get("role") != "supplier":
                break

    # ---------------- Place bids (bulk editable table + live item updates) ----------------
    with tabs[1]:
        st.subheader("Place Bids (Bulk or Selective)")
        aucs = run_query("SELECT id,title FROM auctions WHERE status='live' AND end_time>NOW()")
        if aucs.empty:
            st.info("No active auctions.")
            return

        sel = st.selectbox(
            "Select Auction (Bid)",
            aucs["id"],
            format_func=lambda x: aucs.loc[aucs['id']==x,'title'].iloc[0],
            key="supplier_bid_select"
        )

        # fetch min_decrement for selected auction
        dec_df = run_query("SELECT COALESCE(min_decrement,0) AS min_dec FROM auctions WHERE id=%s", (sel,))
        min_dec = float(dec_df.iloc[0]["min_dec"]) if not dec_df.empty else 0.0
        if min_dec and min_dec > 0:
            st.info(f"This auction requires bid decrements in multiples of {min_dec}")

        # Build items table with current lowest bids
        q_items = """
        SELECT ai.id, ai.item_name, ai.quantity, ai.uom, ai.base_price,
               v.lowest_bid
        FROM auction_items ai
        LEFT JOIN v_lowest_bids_per_item v ON ai.id=v.item_id
        WHERE ai.auction_id=%s
        ORDER BY ai.id;
        """
        # placeholder for live-updating items + lowest bids
        placeholder = st.empty()
        # We will show editable data_editor with columns: item_id (hidden), item_name, quantity, uom, base_price, lowest_bid, your_bid, select
        # Loop updates the non-editable columns every REFRESH_SEC, but we must preserve edits typed by user.
        # Strategy:
        #  - maintain user-edits in st.session_state["bulk_bid_edits"][sel] as dict keyed by item_id
        if "bulk_bid_edits" not in st.session_state:
            st.session_state["bulk_bid_edits"] = {}

        # ensure there is a dict for this auction
        if sel not in st.session_state["bulk_bid_edits"]:
            st.session_state["bulk_bid_edits"][sel] = {}

        # We'll run a short refresh loop that updates the dataframe view but keeps 'your_bid' and 'select' from session state
        for _ in range(200):
            df_items = run_query(q_items, (sel,))
            if df_items.empty:
                placeholder.info("No items found.")
                break

            # Prepare the editable DataFrame
            editable_df = pd.DataFrame({
                "item_id": df_items["id"],
                "item_name": df_items["item_name"],
                "quantity": df_items["quantity"],
                "uom": df_items["uom"],
                "base_price": df_items["base_price"],
                "lowest_bid": df_items["lowest_bid"].fillna(""),
            })

            # attach user edits (persisted in session_state)
            edits = st.session_state["bulk_bid_edits"].get(sel, {})
            # ensure columns for editing: 'your_bid' and 'select'
            editable_df["your_bid"] = editable_df["item_id"].apply(lambda iid: edits.get(iid, {}).get("your_bid", float('nan')))
            editable_df["select"] = editable_df["item_id"].apply(lambda iid: edits.get(iid, {}).get("select", False))

            # Use data_editor for user to edit 'your_bid' and 'select'
            edited = placeholder.data_editor(
                editable_df[["item_id","item_name","quantity","uom","base_price","lowest_bid","your_bid","select"]],
                column_config={
                    "item_id": st.column_config.HiddenColumn("item_id"),
                    "item_name": st.column_config.TextColumn("item_name", disabled=True),
                    "quantity": st.column_config.NumberColumn("quantity", disabled=True),
                    "uom": st.column_config.TextColumn("uom", disabled=True),
                    "base_price": st.column_config.NumberColumn("base_price", disabled=True),
                    "lowest_bid": st.column_config.TextColumn("lowest_bid", disabled=True),
                    "your_bid": st.column_config.NumberColumn("your_bid", help="Enter your bid (must be lower than current lowest)"),
                    "select": st.column_config.CheckboxColumn("select", help="Tick to include this item in the bulk submission")
                },
                use_container_width=True,
                num_rows="dynamic",
                key=f"data_editor_{sel}"
            )

            # Save edits to session_state for persistence across refresh cycles
            new_edits = {}
            for idx, row in edited.iterrows():
                iid = int(row["item_id"])
                # row['your_bid'] might be empty string or NaN
                yb = row.get("your_bid")
                if pd.isna(yb) or yb == "":
                    yb_val = None
                else:
                    try:
                        yb_val = float(yb)
                    except Exception:
                        yb_val = None
                sel_flag = bool(row.get("select", False))
                new_edits[iid] = {"your_bid": yb_val, "select": sel_flag}
            st.session_state["bulk_bid_edits"][sel] = new_edits

            # show submission controls below the editor
            st.markdown("---")
            col1, col2 = st.columns([1,1])
            with col1:
                if st.button("Submit Selected Bids"):
                    # Validate and insert selected bids in bulk
                    to_submit = []
                    # Refresh current lowest per item to validate exactly at submission time
                    low_map_df = run_query("SELECT item_id, MIN(bid_amount) as lowest FROM bids WHERE auction_id=%s GROUP BY item_id", (sel,))
                    low_map = {}
                    if not low_map_df.empty:
                        for _, r in low_map_df.iterrows():
                            low_map[int(r["item_id"])] = float(r["lowest"]) if r["lowest"] is not None else None

                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            errors = []
                            for iid, info in st.session_state["bulk_bid_edits"].get(sel, {}).items():
                                if not info.get("select"):
                                    continue
                                your_bid = info.get("your_bid")
                                if your_bid is None:
                                    errors.append(f"Item {iid}: no bid entered.")
                                    continue

                                current_lowest = low_map.get(iid, None)
                                # get base price for item if no bids
                                if current_lowest is None:
                                    base_row = run_query("SELECT base_price FROM auction_items WHERE id=%s", (iid,))
                                    base_price = float(base_row.iloc[0]["base_price"]) if not base_row.empty else None
                                    if base_price is not None:
                                        if your_bid >= base_price:
                                            errors.append(f"Item {iid}: first bid must be lower than base price ({base_price}).")
                                            continue
                                else:
                                    if your_bid >= current_lowest:
                                        errors.append(f"Item {iid}: bid must be lower than current lowest ({current_lowest}).")
                                        continue
                                    # check decrement multiple
                                    if min_dec and min_dec > 0:
                                        diff = current_lowest - your_bid if current_lowest is not None else (base_price - your_bid)
                                        if not is_multiple_of(min_dec, diff):
                                            errors.append(f"Item {iid}: decrement {diff} not a multiple of {min_dec}.")
                                            continue
                                # passed validation => queue insert
                                to_submit.append((sel, iid, user["id"], your_bid))

                            if errors:
                                st.error("Some bids failed validation:\n" + "\n".join(errors))
                            else:
                                # insert all bids atomically
                                try:
                                    insert_q = "INSERT INTO bids(auction_id, item_id, bidder_id, bid_amount) VALUES %s"
                                    # psycopg2 mogrify multiple: we'll use executemany
                                    cur.executemany("INSERT INTO bids(auction_id,item_id,bidder_id,bid_amount) VALUES(%s,%s,%s,%s)", to_submit)
                                    conn.commit()
                                    st.success(f"Inserted {len(to_submit)} bids successfully.")
                                    # clear edits for this auction
                                    st.session_state["bulk_bid_edits"][sel] = {}
                                    # after inserting, refresh the page data
                                    st.rerun()
                                except Exception as e:
                                    conn.rollback()
                                    st.error(f"DB error inserting bids: {e}")
                with st.button("Clear Selected Flags"):
                    # clear select flags but keep your_bid values
                    edits = st.session_state["bulk_bid_edits"].get(sel, {})
                    for iid in edits:
                        edits[iid]["select"] = False
                    st.session_state["bulk_bid_edits"][sel] = edits
                    st.rerun()
            with col2:
                st.write("Hint: Tick items you want to submit and fill 'your_bid' column. Then click Submit Selected Bids.")
                if st.button("Refresh Items Now"):
                    st.experimental_rerun()

            # sleep then continue refresh loop (this preserves user edits because we store them in st.session_state)
            time.sleep(REFRESH_SEC)
            if st.session_state.get("role") != "supplier":
                break

# ---------------- MAIN ----------------
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
            new_id = create_account(name, email, pwd, role, company)
            if new_id:
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
