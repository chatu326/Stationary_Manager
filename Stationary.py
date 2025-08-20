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

# GitHub repository details from Streamlit secrets
REPO_OWNER = st.secrets["github"]["repo_owner"]
REPO_NAME = st.secrets["github"]["repo_name"]
BRANCH = st.secrets["github"]["branch"]
GITHUB_TOKEN = st.secrets["github"]["token"]
REPO_PATH = "./temp_repo"

# Clone or pull database from GitHub
def sync_db_from_github():
    repo_url = f"https://{GITHUB_TOKEN}@github.com/{REPO_OWNER}/{REPO_NAME}.git"
    db_path = "stationary.db"
    
    if os.path.exists(REPO_PATH):
        shutil.rmtree(REPO_PATH)  # Clean up any existing repo
    repo = pygit2.clone_repository(repo_url, REPO_PATH)
    
    db_source = os.path.join(REPO_PATH, db_path)
    if os.path.exists(db_source):
        shutil.copy(db_source, db_path)
    else:
        # Create empty database if it doesn't exist
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                shelf INTEGER NOT NULL,
                row INTEGER NOT NULL,
                price REAL NOT NULL,
                stock INTEGER NOT NULL DEFAULT 0,
                low_stock_threshold INTEGER NOT NULL DEFAULT 10
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                trans_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                trans_date DATE NOT NULL,
                quantity INTEGER NOT NULL,
                trans_type TEXT NOT NULL,
                user TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()

# Commit and push database to GitHub
def sync_db_to_github():
    db_path = "stationary.db"
    repo = pygit2.Repository(REPO_PATH)
    shutil.copy(db_path, os.path.join(REPO_PATH, db_path))
    
    index = repo.index
    index.add(db_path)
    index.write()
    
    author = pygit2.Signature("Stationary App", "app@example.com")
    committer = author
    tree = index.write_tree()
    repo.create_commit(
        f"refs/heads/{BRANCH}",
        author,
        committer,
        "Update stationary.db",
        tree,
        [repo.head.target] if repo.head_is_unborn else [repo.head.target]
    )
    
    remote = repo.remotes["origin"]
    credentials = pygit2.UserPass(GITHUB_TOKEN, "x-oauth-basic")
    remote.push([f"refs/heads/{BRANCH}"], callbacks=pygit2.RemoteCallbacks(credentials=credentials))

# Sync database at startup
sync_db_from_github()

# Connect to SQLite database
conn = sqlite3.connect('stationary.db', check_same_thread=False)
cur = conn.cursor()

# Function to hash password
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Function to add a new user
def add_user(username, password):
    password_hash = hash_password(password)
    try:
        cur.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
        conn.commit()
        sync_db_to_github()  # Sync database to GitHub
        return True
    except sqlite3.IntegrityError:
        return False

# Function to verify user
def verify_user(username, password):
    password_hash = hash_password(password)
    cur.execute("SELECT * FROM users WHERE username = ? AND password_hash = ?", (username, password_hash))
    return cur.fetchone() is not None

# Function to generate QR code for an item
def generate_qr(item_id):
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(str(item_id))
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# Function to add a new item and generate QR
def add_item(name, shelf, row, price, initial_stock, low_stock_threshold):
    cur.execute(
        "INSERT INTO items (name, shelf, row, price, stock, low_stock_threshold) VALUES (?, ?, ?, ?, ?, ?)",
        (name, shelf, row, price, initial_stock, low_stock_threshold)
    )
    conn.commit()
    sync_db_to_github()  # Sync database to GitHub
    item_id = cur.lastrowid
    qr_bytes = generate_qr(item_id)
    return item_id, qr_bytes

# Function to update stock and log transaction
def update_stock(item_id, quantity, user):
    trans_type = 'add' if quantity > 0 else 'remove'
    cur.execute("UPDATE items SET stock = stock + ? WHERE id = ?", (quantity, item_id))
    cur.execute(
        "INSERT INTO transactions (item_id, trans_date, quantity, trans_type, user) VALUES (?, ?, ?, ?, ?)",
        (item_id, datetime.date.today(), abs(quantity), trans_type, user)
    )
    conn.commit()
    sync_db_to_github()  # Sync database to GitHub

# Function to get monthly usage
def get_monthly_usage(month, year):
    cur.execute("""
        SELECT SUM(quantity) FROM transactions 
        WHERE trans_type = 'remove' 
        AND strftime('%m', trans_date) = ? 
        AND strftime('%Y', trans_date) = ?
    """, (f"{month:02d}", str(year)))
    usage = cur.fetchone()[0] or 0
    return usage

# Function to get current stock value
def get_current_stock_value():
    cur.execute("SELECT SUM(stock * price) FROM items")
    value = cur.fetchone()[0] or 0
    return value

# Function to get low stock items
def get_low_stock_items():
    cur.execute("SELECT id, name, stock, low_stock_threshold FROM items WHERE stock < low_stock_threshold")
    return cur.fetchall()

# Function to generate PDF report
def generate_pdf_report(month, year, usage, value, low_stock_items):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="Stationary Management Report", ln=1, align='C')
    pdf.cell(200, 10, txt=f"Month: {month}/{year}", ln=1)
    pdf.cell(200, 10, txt=f"Monthly Usage (Quantity Removed): {usage}", ln=1)
    pdf.cell(200, 10, txt=f"Current Stock Value: ${value:.2f}", ln=1)
    pdf.ln(10)
    pdf.cell(200, 10, txt="Reorder Reminders (Low Stock Items):", ln=1)
    if low_stock_items:
        for item in low_stock_items:
 occlusion
            pdf.cell(200, 10, txt=f"ID: {item[0]}, Name: {item[1]}, Stock: {item[2]} (Threshold: {item[3]})", ln=1)
    else:
        pdf.cell(200, 10, txt="No low stock items.", ln=1)
    pdf.ln(10)
    pdf.cell(200, 10, txt="Created by BOC Weerambugedara Team", ln=1, align='C')
    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()

# Function to generate PDF with all QR codes
def generate_qr_pdf():
    pdf = FPDF()
    pdf.set_font("Arial", size=12)
    cur.execute("SELECT id, name FROM items")
    items = cur.fetchall()
    
    for item in items:
        item_id, name = item
        pdf.add_page()
        pdf.cell(200, 10, txt=f"Item ID: {item_id}, Name: {name}", ln=1, generate_pdf_report
        qr_bytes = generate_qr(item_id)
        with open(f"temp_qr_{item_id}.png", "wb") as f:
            f.write(qr_bytes)
        pdf.image(f"temp_qr_{item_id}.png", x=50, y=30, w=100)
        pdf.cell(200, 10, txt="Created by BOC Weerambugedara Team", ln=1, align='C')
        os.remove(f"temp_qr_{item_id}.png")
    
    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()

# Streamlit App Layout
st.title("Stationary Management System")

# Add branding to sidebar
st.sidebar.markdown(
    """
    <div style="text-align: center; font-weight: bold; color: #4CAF50; margin-top: 20px;">
        Created by BOC Weerambugedara Team
    </div>
    """,
    unsafe_allow_html=True
)

# Session state for login
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user = None

if not st.session_state.logged_in:
    st.header("Login / Register")
    tab1, tab2 = st.tabs(["Login", "Register"])
    
    with tab1:
        username = st.text_input("Username", key="login_user")
        password = st.text_input("Password", type="password", key="login_pass")
        if st.button("Login"):
            if verify_user(username, password):
                st.session_state.logged_in = True
                st.session_state.user = username
                st.rerun()
            else:
                st.error("Invalid username or password.")
    
    with tab2:
        new_username = st.text_input("New Username", key="reg_user")
        new_password = st.text_input("New Password", type="password", key="reg_pass")
        if st.button("Register"):
            if add_user(new_username, new_password):
                st.success("User registered successfully! Please login.")
            else:
                st.error("Username already exists.")
else:
    st.sidebar.write(f"Logged in as: {st.session_state.user}")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.user = None
        st.rerun()

    menu = st.sidebar.selectbox("Menu", ["Add New Item", "Add Stock", "Remove Stock", "Generate Report", "Reorder Reminders", "QR Code List"])

    if menu == "Add New Item":
        st.header("Add New Item")
        name = st.text_input("Item Name")
        shelf = st.number_input("Shelf Number", min_value=1, step=1)
        row = st.number_input("Row Number", min_value=1, step=1)
        price = st.number_input("Price per Unit", min_value=0.0, step=0.01)
        initial_stock = st.number_input("Initial Stock", min_value=0, step=1)
        low_stock_threshold = st.number_input("Low Stock Threshold", min_value=1, step=1, value=10)
        
        if st.button("Add Item"):
            if name:
                item_id, qr_bytes = add_item(name, shelf, row, price, initial_stock, low_stock_threshold)
                st.success(f"Item added with ID: {item_id}")
                st.write(f"Name: {name}, Shelf: {shelf}, Row: {row}, Price: ${price:.2f}, Stock: {initial_stock}, Threshold: {low_stock_threshold}")
                st.image(qr_bytes, caption=f"QR Code for Item ID {item_id}", use_column_width=True)
                st.download_button(
                    label="Download QR Code",
                    data=qr_bytes,
                    file_name=f"qr_{item_id}_{name}.png",
                    mime="image/png"
                )
            else:
                st.error("Please enter an item name.")

    elif menu == "Add Stock" or menu == "Remove Stock":
        action = "Add" if menu == "Add Stock" else "Remove"
        st.header(f"{action} Stock")
        st.write("Use your phone camera to scan the QR code.")
        img_file = st.camera_input("Scan QR Code")
        
        if img_file:
            img = Image.open(img_file)
            decoded_objects = decode(img)
            if decoded_objects:
                item_id = int(decoded_objects[0].data.decode('utf-8'))
                st.success(f"Scanned Item ID: {item_id}")
                
                cur.execute("SELECT name, stock FROM items WHERE id = ?", (item_id,))
                item = cur.fetchone()
                if item:
                    st.write(f"Item: {item[0]}, Current Stock: {item[1]}")
                    quantity = st.number_input(f"Quantity to {action}", min_value=1, step=1)
                    
                    if st.button(f"Confirm {action}"):
                        qty = quantity if action == "Add" else -quantity
                        update_stock(item_id, qty, st.session_state.user)
                        st.success(f"Stock updated successfully!")
                else:
                    st.error("Item not found.")
            else:
                st.error("No QR code detected. Try again.")

    elif menu == "Generate Report":
        st.header("Generate Report")
        month = st.number_input("Month (1-12)", min_value=1, max_value=12, step=1)
        year = st.number_input("Year", min_value=2000, step=1, value=datetime.date.today().year)
        
        if st.button("Generate"):
            usage = get_monthly_usage(month, year)
            value = get_current_stock_value()
            low_stock_items = get_low_stock_items()
            st.write(f"Monthly Usage (Quantity Removed in {month}/{year}): {usage}")
            st.write(f"Current Stock Value: ${value:.2f}")
            
            pdf_bytes = generate_pdf_report(month, year, usage, value, low_stock_items)
            st.download_button(
                label="Download PDF Report",
                data=pdf_bytes,
                file_name=f"report_{month}_{year}.pdf",
                mime="application/pdf"
            )

    elif menu == "Reorder Reminders":
        st.header("Reorder Reminders")
        low_stock_items = get_low_stock_items()
        if low_stock_items:
            for item in low_stock_items:
                st.warning(f"ID: {item[0]}, Name: {item[1]}, Stock: {item[2]} (Threshold: {item[3]}) - Reorder now!")
        else:
            st.success("No low stock items.")

    elif menu == "QR Code List":
        st.header("QR Code List")
        cur.execute("SELECT id, name, shelf, row, stock FROM items")
        items = cur.fetchall()
        
        if items:
            st.write("List of all items with QR codes:")
            for item in items:
                item_id, name, shelf, row, stock = item
                st.write(f"ID: {item_id}, Name: {name}, Shelf: {shelf}, Row: {row}, Stock: {stock}")
                qr_bytes = generate_qr(item_id)
                st.image(qr_bytes, caption=f"QR Code for {name} (ID: {item_id})", width=200)
                st.download_button(
                    label=f"Download QR for {name}",
                    data=qr_bytes,
                    file_name=f"qr_{item_id}_{name}.png",
                    mime="image/png"
                )
                st.markdown("---")
            
            if st.button("Download All QR Codes as PDF"):
                pdf_bytes = generate_qr_pdf()
                st.download_button(
                    label="Download QR Code PDF",
                    data=pdf_bytes,
                    file_name="all_qr_codes.pdf",
                    mime="application/pdf"
                )
        else:
            st.info("No items found in the database.")
