import sqlite3
from config import DB_PATH

from config import DB_PATH, ADMIN_ID  # ✅ ADMIN_ID added here


# 🔧 Initialize DB (run once at startup)
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER,
            session_string TEXT,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# 💾 Save new session
def save_session(user_id, session_string):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO sessions (user_id, session_string) VALUES (?, ?)", (user_id, session_string))
    conn.commit()
    conn.close()

# 📥 Get session by user_id
def get_session(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT session_string FROM sessions WHERE user_id = ? ORDER BY created DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

# 📋 Get all sessions
def get_all_sessions():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, session_string FROM sessions")
    rows = c.fetchall()
    conn.close()
    return rows
# ❌ Delete session by string (used to remove dead sessions)
def delete_session_by_string(session_string):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE session_string = ?", (session_string,))
    conn.commit()
    conn.close()
def init_admins():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()

def add_admin(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def remove_admin(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_all_admins():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM admins")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def is_admin(user_id):
    return user_id == ADMIN_ID or user_id in get_all_admins()
def delete_session_by_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    rows_affected = c.rowcount
    conn.close()
    return rows_affected > 0
