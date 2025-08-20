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
