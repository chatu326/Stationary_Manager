import streamlit as st
import sqlite3
import qrcode
from io import BytesIO
from PIL import Image
from pyzbar.pyzbar import decode
import datetime
from fpdf import FPDF
import hashlib
import pygit2
import os
import shutil
import tempfile

# ─────────────────────────────────────────────────────────────
# GitHub / Database Sync Setup
# ─────────────────────────────────────────────────────────────

try:
    REPO_OWNER = st.secrets["github"]["repo_owner"]
    REPO_NAME = st.secrets["github"]["repo_name"]
    BRANCH = st.secrets["github"]["branch"]
    GITHUB_TOKEN = st.secrets["github"]["token"]
    USE_GITHUB = True
except KeyError as e:
    st.warning(f"GitHub secrets missing: {e}. Using local database only.")
    REPO_OWNER = "local"
    REPO_NAME = "local"
    BRANCH = "main"
    GITHUB_TOKEN = ""
    USE_GITHUB = False

REPO_PATH = "./temp_repo"

def update_db_schema():
    conn = sqlite3.connect('stationary.db', check_same_thread=False)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(items)")
    columns = [info[1] for info in cur.fetchall()]
    if 'form_number' not in columns:
        try:
            cur.execute("ALTER TABLE items ADD COLUMN form_number TEXT")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_form_number ON items(form_number)")
            conn.commit()
        except sqlite3.OperationalError as e:
            pass  # silent if already exists
    cur.execute("PRAGMA table_info(users)")
    columns = [info[1] for info in cur.fetchall()]
    if 'is_admin' not in columns:
        try:
            cur.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    # Default admin
    admin_pw_hash = hashlib.sha256("Admin123!".encode()).hexdigest()
    cur.execute("SELECT * FROM users WHERE username = 'admin'")
    if not cur.fetchone():
        cur.execute("INSERT OR IGNORE INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                    ("admin", admin_pw_hash))
        conn.commit()
    conn.close()

def sync_db_from_github():
    if not USE_GITHUB:
        return
    repo_url = f"https://{GITHUB_TOKEN}@github.com/{REPO_OWNER}/{REPO_NAME}.git"
    if os.path.exists(REPO_PATH):
        shutil.rmtree(REPO_PATH)
    try:
        pygit2.clone_repository(repo_url, REPO_PATH)
    except Exception as e:
        st.error(f"GitHub clone failed: {e}")
        st.stop()

    db_source = os.path.join(REPO_PATH, "stationary.db")
    if os.path.exists(db_source):
        shutil.copy(db_source, "stationary.db")
    else:
        conn = sqlite3.connect("stationary.db")
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, is_admin BOOLEAN DEFAULT 0)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, form_number TEXT, name TEXT NOT NULL,
            shelf INTEGER NOT NULL, row INTEGER NOT NULL, price REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0, low_stock_threshold INTEGER NOT NULL DEFAULT 10)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS transactions (
            trans_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL,
            trans_date DATE NOT NULL, quantity INTEGER NOT NULL,
            trans_type TEXT NOT NULL, user TEXT NOT NULL)''')
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_form_number ON items(form_number)")
        conn.commit()
        conn.close()

def sync_db_to_github():
    if not USE_GITHUB:
        return
    db_path = "stationary.db"
    repo = pygit2.Repository(REPO_PATH)
    shutil.copy(db_path, os.path.join(REPO_PATH, db_path))
    index = repo.index
    index.add(db_path)
    index.write()
    tree = index.write_tree()
    author = pygit2.Signature("Stationary App", "app@example.com")
    repo.create_commit(f"refs/heads/{BRANCH}", author, author, "Update db", tree, [repo.head.target])
    remote = repo.remotes["origin"]
    credentials = pygit2.UserPass(GITHUB_TOKEN, "x-oauth-basic")
    remote.push([f"refs/heads/{BRANCH}"], callbacks=pygit2.RemoteCallbacks(credentials=credentials))

# Initialize
sync_db_from_github()
update_db_schema()

conn = sqlite3.connect('stationary.db', check_same_thread=False)
cur = conn.cursor()

# ─────────────────────────────────────────────────────────────
# Utility Functions
# ─────────────────────────────────────────────────────────────

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def verify_user(username, password):
    cur.execute("SELECT * FROM users WHERE username = ? AND password_hash = ?",
                (username, hash_password(password)))
    return cur.fetchone() is not None

def is_admin_user(username):
    cur.execute("SELECT is_admin FROM users WHERE username = ?", (username,))
    r = cur.fetchone()
    return r and r[0] == 1

def generate_qr(item_id):
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(str(item_id))
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def add_item(form_number, name, shelf, row, price, initial_stock, low_stock_threshold):
    try:
        cur.execute(
            "INSERT INTO items (form_number, name, shelf, row, price, stock, low_stock_threshold) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (form_number, name, shelf, row, price, initial_stock, low_stock_threshold)
        )
        conn.commit()
        sync_db_to_github()
        item_id = cur.lastrowid
        return item_id, generate_qr(item_id)
    except sqlite3.IntegrityError:
        st.error("Form number already exists. Please use a unique one.")
        return None, None

def update_stock(item_id, quantity, user):
    trans_type = 'add' if quantity > 0 else 'remove'
    cur.execute("UPDATE items SET stock = stock + ? WHERE id = ?", (quantity, item_id))
    cur.execute(
        "INSERT INTO transactions (item_id, trans_date, quantity, trans_type, user) "
        "VALUES (?, ?, ?, ?, ?)",
        (item_id, datetime.date.today(), abs(quantity), trans_type, user)
    )
    conn.commit()
    sync_db_to_github()

def get_item_by_id(item_id):
    cur.execute("""
        SELECT id, form_number, name, shelf, row, price, stock, low_stock_threshold 
        FROM items WHERE id = ?
    """, (item_id,))
    return cur.fetchone()

def search_items(term):
    term = f"%{term}%"
    cur.execute("""
        SELECT id, form_number, name, shelf, row, price, stock, low_stock_threshold 
        FROM items 
        WHERE name LIKE ? OR form_number LIKE ?
    """, (term, term))
    return cur.fetchall()

# ─────────────────────────────────────────────────────────────
# Graphical Item Card Display
# ─────────────────────────────────────────────────────────────

def display_item_card(item, key_prefix=""):
    item_id, form_number, name, shelf, row, price, stock, threshold = item
    form_number = form_number or "N/A"

    with st.container(border=True):
        col_qr, col_info = st.columns([1, 3])

        with col_qr:
            qr_data = generate_qr(item_id)
            st.image(qr_data, use_container_width=True)
            st.caption(f"ID: {item_id}")

        with col_info:
            st.subheader(name)
            st.caption(f"Form Number: **{form_number}**")

            st.markdown("---")

            c1, c2, c3 = st.columns(3)
            c1.metric("Location", f"Shelf {shelf} • Row {row}")
            c2.metric("Price", f"LKR {price:,.2f}")
            c3.metric("Stock", stock, delta_color="normal")

            if stock <= threshold:
                st.error(f"⚠ Low stock! Only {stock} left (alert at {threshold})")
            else:
                st.success(f"Stock is healthy (alert at {threshold})")

        # Action buttons
        st.markdown("---")
        btn1, btn2 = st.columns(2)
        with btn1:
            if st.button("➕ Add Stock", key=f"{key_prefix}add_{item_id}", use_container_width=True):
                st.session_state["action_item_id"] = item_id
                st.session_state["action_type"] = "add"
                st.rerun()
        with btn2:
            if st.button("➖ Remove Stock", key=f"{key_prefix}remove_{item_id}", use_container_width=True):
                st.session_state["action_item_id"] = item_id
                st.session_state["action_type"] = "remove"
                st.rerun()

# ─────────────────────────────────────────────────────────────
# Main App UI
# ─────────────────────────────────────────────────────────────

st.title("Stationary Management System")

st.sidebar.markdown(
    """
    <div style="text-align: center; font-weight: bold; color: #4CAF50; margin-top: 20px;">
        Created by BOC Weerambugedara Team
    </div>
    """,
    unsafe_allow_html=True
)

# Login / Register
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user = None

if not st.session_state.logged_in:
    st.header("Login / Register")
    tab1, tab2 = st.tabs(["Login", "Register"])

    with tab1:
        un = st.text_input("Username")
        pw = st.text_input("Password", type="password")
        if st.button("Login"):
            if verify_user(un, pw):
                st.session_state.logged_in = True
                st.session_state.user = un
                st.rerun()
            else:
                st.error("Invalid credentials")

    with tab2:
        new_un = st.text_input("New Username")
        new_pw = st.text_input("New Password", type="password")
        if st.button("Register"):
            if add_user(new_un, new_pw):
                st.success("Registered! Now login.")
            else:
                st.error("Username already taken")
else:
    st.sidebar.write(f"**Logged in as:** {st.session_state.user}")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.user = None
        st.rerun()

    # Handle add/remove stock actions from card buttons
    if "action_item_id" in st.session_state:
        item_id = st.session_state["action_item_id"]
        action = st.session_state["action_type"]

        item = get_item_by_id(item_id)
        if item:
            _, form_number, name, _, _, price, stock, threshold = item
            form_number = form_number or "N/A"

            st.header(f"{'Add' if action == 'add' else 'Remove'} Stock – {name}")
            st.write(f"Form Number: **{form_number}** | Current Stock: **{stock}**")

            qty = st.number_input("Quantity", min_value=1, step=1)

            if st.button(f"Confirm {'Add' if action == 'add' else 'Remove'}"):
                change = qty if action == "add" else -qty
                update_stock(item_id, change, st.session_state.user)
                st.success(f"Stock updated successfully!")
                del st.session_state["action_item_id"]
                del st.session_state["action_type"]
                st.rerun()

            if st.button("Cancel"):
                del st.session_state["action_item_id"]
                del st.session_state["action_type"]
                st.rerun()
        else:
            st.error("Item not found.")
            del st.session_state["action_item_id"]
            del st.session_state["action_type"]

    # Main Menu
    menu_options = ["Search Items", "Add New Item", "Generate Report", "Reorder Reminders", "QR Code List"]
    if is_admin_user(st.session_state.user):
        menu_options.append("Admin Panel")
    menu = st.sidebar.selectbox("Menu", menu_options)

    # ────────────── Search Items ──────────────
    if menu == "Search Items":
        st.header("Search Stationary Items")
        term = st.text_input("Enter item name or form number")
        if term:
            results = search_items(term)
            if results:
                st.success(f"Found {len(results)} item(s)")
                for item in results:
                    display_item_card(item, key_prefix="search_")
            else:
                st.warning("No items found.")

    # ────────────── Add New Item ──────────────
    elif menu == "Add New Item":
        st.header("Add New Stationary Item")
        form_number = st.text_input("Form Number (unique)")
        name = st.text_input("Item Name")
        shelf = st.number_input("Shelf", min_value=1, step=1)
        row = st.number_input("Row", min_value=1, step=1)
        price = st.number_input("Price per unit", min_value=0.0, step=0.01)
        initial_stock = st.number_input("Initial Stock", min_value=0, step=1)
        threshold = st.number_input("Low Stock Threshold", min_value=1, step=1, value=10)

        if st.button("Add Item"):
            if form_number and name:
                item_id, qr_data = add_item(form_number, name, shelf, row, price, initial_stock, threshold)
                if item_id:
                    st.success(f"Added successfully – ID: {item_id}")
                    col_qr, col_info = st.columns([1, 3])
                    with col_qr:
                        st.image(qr_data, use_container_width=True)
                    with col_info:
                        st.write(f"**{name}**")
                        st.caption(f"Form: {form_number} | Location: Shelf {shelf}, Row {row}")
                        st.metric("Price", f"LKR {price:,.2f}")
                        st.metric("Stock", initial_stock)
                    st.download_button("Download QR", qr_data, f"qr_{item_id}_{form_number}.png", "image/png")
            else:
                st.error("Form number and name are required.")

    # Other menu options remain the same...
    # (Generate Report, Reorder Reminders, QR Code List, Admin Panel)
    # ... add your existing code for these sections here ...
