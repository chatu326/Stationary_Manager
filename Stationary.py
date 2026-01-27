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
    st.warning(f"GitHub secrets missing: {e}. Falling back to local database (no persistence across redeployments).")
    REPO_OWNER = "local"
    REPO_NAME = "local"
    BRANCH = "main"
    GITHUB_TOKEN = ""
    USE_GITHUB = False

REPO_PATH = "./temp_repo"

def update_db_schema():
    conn = sqlite3.connect('stationary.db', check_same_thread=False)
    cur = conn.cursor()
    
    # form_number column + unique index
    cur.execute("PRAGMA table_info(items)")
    cols = [c[1] for c in cur.fetchall()]
    if 'form_number' not in cols:
        try:
            cur.execute("ALTER TABLE items ADD COLUMN form_number TEXT")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_form_number ON items(form_number)")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    
    # is_admin column
    cur.execute("PRAGMA table_info(users)")
    cols = [c[1] for c in cur.fetchall()]
    if 'is_admin' not in cols:
        try:
            cur.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    
    # Default admin user (admin / Admin123!)
    admin_pw_hash = hashlib.sha256("Admin123!".encode()).hexdigest()
    cur.execute("SELECT * FROM users WHERE username = 'admin'")
    if not cur.fetchone():
        try:
            cur.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                        ("admin", admin_pw_hash))
            conn.commit()
        except sqlite3.IntegrityError:
            pass
    
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
# Core Functions
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

def add_user(username, password):
    pw_hash = hash_password(password)
    try:
        cur.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 0)", 
                    (username, pw_hash))
        conn.commit()
        sync_db_to_github()
        return True
    except sqlite3.IntegrityError:
        return False

def delete_user(username):
    try:
        cur.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        sync_db_to_github()
        return True
    except sqlite3.Error as e:
        st.error(f"Failed to delete user: {e}")
        return False

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
        st.error("Form number already exists.")
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

def get_all_items():
    cur.execute("SELECT id, form_number, name, shelf, row, price, stock, low_stock_threshold FROM items")
    return cur.fetchall()

def get_low_stock_items():
    cur.execute("SELECT id, name, stock, low_stock_threshold FROM items WHERE stock < low_stock_threshold")
    return cur.fetchall()

def get_monthly_usage(month, year):
    cur.execute("""
        SELECT SUM(quantity) FROM transactions 
        WHERE trans_type = 'remove' 
        AND strftime('%m', trans_date) = ? 
        AND strftime('%Y', trans_date) = ?
    """, (f"{month:02d}", str(year)))
    return cur.fetchone()[0] or 0

def get_current_stock_value():
    cur.execute("SELECT SUM(stock * price) FROM items")
    return cur.fetchone()[0] or 0

def delete_item(item_id):
    try:
        cur.execute("DELETE FROM items WHERE id = ?", (item_id,))
        cur.execute("DELETE FROM transactions WHERE item_id = ?", (item_id,))
        conn.commit()
        sync_db_to_github()
        return True
    except sqlite3.Error as e:
        st.error(f"Failed to delete item: {e}")
        return False

def update_item(item_id, form_number, name, shelf, row, price, low_stock_threshold):
    try:
        cur.execute("""
            UPDATE items 
            SET form_number = ?, name = ?, shelf = ?, row = ?, price = ?, low_stock_threshold = ?
            WHERE id = ?
        """, (form_number, name, shelf, row, price, low_stock_threshold, item_id))
        conn.commit()
        sync_db_to_github()
        return True
    except sqlite3.IntegrityError:
        st.error("Form number already exists.")
        return False
    except sqlite3.Error as e:
        st.error(f"Database error: {e}")
        return False

# ─────────────────────────────────────────────────────────────
# Graphical Card Display
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
            c3.metric("Stock", stock)

            if stock <= threshold:
                st.error(f"Low stock alert! Only {stock} left (threshold: {threshold})")
            else:
                st.success(f"Stock OK (threshold: {threshold})")

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

    # Handle stock action from card buttons
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

            col_confirm, col_cancel = st.columns(2)
            with col_confirm:
                if st.button(f"Confirm {'Add' if action == 'add' else 'Remove'}", type="primary"):
                    change = qty if action == "add" else -qty
                    update_stock(item_id, change, st.session_state.user)
                    # After update → show updated card
                    st.session_state["view_item_id"] = item_id
                    for k in ["action_item_id", "action_type"]:
                        st.session_state.pop(k, None)
                    st.rerun()
            with col_cancel:
                if st.button("Cancel"):
                    for k in ["action_item_id", "action_type"]:
                        st.session_state.pop(k, None)
                    st.rerun()
        else:
            st.error("Item not found.")
            for k in ["action_item_id", "action_type"]:
                st.session_state.pop(k, None)

    # Show single item card after stock update or scan
    if "view_item_id" in st.session_state:
        item = get_item_by_id(st.session_state["view_item_id"])
        if item:
            st.header("Item Details (Updated)")
            display_item_card(item, key_prefix="view_")
        if st.button("Close Item View"):
            st.session_state.pop("view_item_id", None)
            st.rerun()

    # Menu
    menu_options = ["Search Items", "Add New Item", "Generate Report", "Reorder Reminders", "QR Code List"]
    if is_admin_user(st.session_state.user):
        menu_options.append("Admin Panel")
    menu = st.sidebar.selectbox("Menu", menu_options)

    # ────────────── Search Items (with QR Scan) ──────────────
    if menu == "Search Items":
        st.header("Search or Scan Stationary Item")

        col_text, col_scan = st.columns([3, 1])
        with col_text:
            search_term = st.text_input("Search by Name or Form Number", key="search_text")
        with col_scan:
            st.write("")  # spacing
            st.write("")  # spacing
            st.caption("Or scan QR")

        img_file = st.camera_input("Scan QR Code", key="qr_scan_camera")

        found_item = None

        # QR Scan handling
        if img_file is not None:
            img = Image.open(img_file)
            decoded_objects = decode(img)
            if decoded_objects:
                try:
                    item_id = int(decoded_objects[0].data.decode('utf-8'))
                    item = get_item_by_id(item_id)
                    if item:
                        found_item = item
                        st.success(f"Scanned successfully – Item ID: {item_id}")
                    else:
                        st.error("No item found with this QR code.")
                except ValueError:
                    st.error("Invalid QR code format.")
            else:
                st.error("No QR code detected in image. Try again.")

        # Text search handling
        elif search_term:
            results = search_items(search_term)
            if results:
                st.success(f"Found {len(results)} matching item(s)")
                for item in results:
                    display_item_card(item, key_prefix="search_")
                    st.markdown("---")
            else:
                st.warning("No items found matching the search term.")

        # Show single item card (from scan or single search result)
        if found_item:
            st.markdown("### Item Details")
            display_item_card(found_item, key_prefix="scan_")

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
                st.error("Form number and name required.")

    # ────────────── Generate Report ──────────────
    elif menu == "Generate Report":
        st.header("Generate Report")
        report_type = st.selectbox("Report Type", ["Monthly Usage Report", "All Items Report"])

        if report_type == "Monthly Usage Report":
            month = st.number_input("Month (1-12)", min_value=1, max_value=12, step=1)
            year = st.number_input("Year", min_value=2000, step=1, value=datetime.date.today().year)

            if st.button("Generate"):
                usage = get_monthly_usage(month, year)
                value = get_current_stock_value()
                low_stock = get_low_stock_items()
                st.write(f"Monthly Usage: {usage}")
                st.write(f"Current Stock Value: LKR {value:,.2f}")

                pdf_bytes = generate_monthly_report(month, year, usage, value, low_stock)
                st.download_button(
                    "Download Monthly Report",
                    pdf_bytes,
                    f"monthly_report_{month}_{year}.pdf",
                    "application/pdf"
                )
        else:
            if st.button("Generate All Items Report"):
                items = get_all_items()
                pdf_bytes = generate_all_items_report(items)
                st.download_button(
                    "Download All Items Report",
                    pdf_bytes,
                    f"all_items_{datetime.date.today()}.pdf",
                    "application/pdf"
                )

    # ────────────── Reorder Reminders ──────────────
    elif menu == "Reorder Reminders":
        st.header("Reorder Reminders")
        items = get_low_stock_items()
        if items:
            for item in items:
                st.warning(f"ID: {item[0]} | {item[1]} | Stock: {item[2]} (Threshold: {item[3]})")
        else:
            st.success("No items below threshold.")

    # ────────────── QR Code List ──────────────
    elif menu == "QR Code List":
        st.header("All QR Codes")
        items = get_all_items()
        if items:
            for item in items:
                item_id, form_number, name, shelf, row, price, stock, threshold = item
                form_number = form_number or "N/A"
                st.write(f"**{name}** | Form: {form_number} | Stock: {stock}")
                qr_data = generate_qr(item_id)
                st.image(qr_data, width=200)
                st.download_button(
                    f"Download QR – {name}",
                    qr_data,
                    f"qr_{item_id}_{form_number}.png",
                    "image/png"
                )
                st.markdown("---")
        else:
            st.info("No items yet.")

    # ────────────── Admin Panel ──────────────
    elif menu == "Admin Panel":
        st.header("Admin Panel")
        if not is_admin_user(st.session_state.user):
            st.error("Access denied.")
            st.stop()

        tab1, tab2 = st.tabs(["Users", "Items"])

        with tab1:
            st.subheader("Manage Users")
            cur.execute("SELECT username, is_admin FROM users")
            users = cur.fetchall()
            for u in users:
                username, is_adm = u
                col1, col2 = st.columns([3,1])
                col1.write(f"**{username}** {'(Admin)' if is_adm else ''}")
                if username != st.session_state.user:
                    if col2.button("Delete", key=f"del_u_{username}"):
                        delete_user(username)
                        st.rerun()

        with tab2:
            st.subheader("Manage Items")
            items = get_all_items()
            for item in items:
                item_id, form_number, name, shelf, row, price, stock, threshold = item
                form_number = form_number or "N/A"
                
                with st.expander(f"{name} – Form {form_number} – ID {item_id}"):
                    st.write(f"Location: Shelf {shelf}, Row {row}")
                    st.write(f"Price: LKR {price:,.2f} | Stock: {stock} | Threshold: {threshold}")

                    with st.form(key=f"edit_{item_id}"):
                        nf = st.text_input("Form Number", value=form_number, key=f"nf_{item_id}")
                        nn = st.text_input("Name", value=name, key=f"nn_{item_id}")
                        ns = st.number_input("Shelf", value=shelf, key=f"ns_{item_id}")
                        nr = st.number_input("Row", value=row, key=f"nr_{item_id}")
                        np = st.number_input("Price", value=float(price), step=0.01, key=f"np_{item_id}")
                        nt = st.number_input("Threshold", value=threshold, key=f"nt_{item_id}")

                        if st.form_submit_button("Save"):
                            if nf == "":
                                st.error("Form number required")
                            elif update_item(item_id, nf, nn, ns, nr, np, nt):
                                st.success("Updated!")
                                st.rerun()

                    if st.button("Delete Item", key=f"del_i_{item_id}"):
                        delete_item(item_id)
                        st.rerun()
