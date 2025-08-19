import streamlit as st
import sqlite3
import qrcode
from io import BytesIO
from PIL import Image
from pyzbar.pyzbar import decode
import datetime
from fpdf import FPDF
import hashlib
import os

# Connect to SQLite database (creates if not exists)
conn = sqlite3.connect('stationary.db', check_same_thread=False)  # Allow multi-thread for Streamlit
cur = conn.cursor()

# Create tables if they don't exist
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
        trans_type TEXT NOT NULL,  -- 'add' or 'remove'
        user TEXT NOT NULL
    )
''')
conn.commit()

# Function to hash password
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Function to add a new user
def add_user(username, password):
    password_hash = hash_password(password)
    try:
        cur.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # Username exists

# Function to verify user
def verify_user(username, password):
    password_hash = hash_password(password)
    cur.execute("SELECT * FROM users WHERE username = ? AND password_hash = ?", (username, password_hash))
    return cur.fetchone() is not None

# Function to add a new item and generate QR
def add_item(name, shelf, row, price, initial_stock, low_stock_threshold):
    cur.execute(
        "INSERT INTO items (name, shelf, row, price, stock, low_stock_threshold) VALUES (?, ?, ?, ?, ?, ?)",
        (name, shelf, row, price, initial_stock, low_stock_threshold)
    )
    conn.commit()
    item_id = cur.lastrowid
    
    # Generate QR code with item ID
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(str(item_id))
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    
    # Convert to bytes for display/download
    buf = BytesIO()
    img.save(buf, format="PNG")
    byte_im = buf.getvalue()
    
    return item_id, byte_im

# Function to update stock and log transaction
def update_stock(item_id, quantity, user):
    trans_type = 'add' if quantity > 0 else 'remove'
    cur.execute("UPDATE items SET stock = stock + ? WHERE id = ?", (quantity, item_id))
    cur.execute(
        "INSERT INTO transactions (item_id, trans_date, quantity, trans_type, user) VALUES (?, ?, ?, ?, ?)",
        (item_id, datetime.date.today(), abs(quantity), trans_type, user)
    )
    conn.commit()

# Function to get monthly usage (total quantity removed in a month/year)
def get_monthly_usage(month, year):
    cur.execute("""
        SELECT SUM(quantity) FROM transactions 
        WHERE trans_type = 'remove' 
        AND strftime('%m', trans_date) = ? 
        AND strftime('%Y', trans_date) = ?
    """, (f"{month:02d}", str(year)))
    usage = cur.fetchone()[0] or 0
    return usage

# Function to get current stock value (sum of stock * price for all items)
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
            pdf.cell(200, 10, txt=f"ID: {item[0]}, Name: {item[1]}, Stock: {item[2]} (Threshold: {item[3]})", ln=1)
    else:
        pdf.cell(200, 10, txt="No low stock items.", ln=1)
    
    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()

# Streamlit App Layout
st.title("Stationary Management System")

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

    menu = st.sidebar.selectbox("Menu", ["Add New Item", "Add Stock", "Remove Stock", "Generate Report", "Reorder Reminders"])

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
                st.image(qr_bytes, caption="QR Code for Item", use_column_width=True)
                st.download_button(
                    label="Download QR Code",
                    data=qr_bytes,
                    file_name=f"qr_{item_id}.png",
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
                
                # Fetch current stock for display
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