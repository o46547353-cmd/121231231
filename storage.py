### slash_vpn_bot/storage.py
import sqlite3, json
from datetime import datetime

DB_FILE = 'slash_vpn_bot.db'

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()

# --- Таблицы ---
c.execute('''CREATE TABLE IF NOT EXISTS accounts (
    login TEXT PRIMARY KEY,
    session_id TEXT,
    csrf_token TEXT,
    user_id TEXT,
    username TEXT,
    account_prompt TEXT DEFAULT '',
    topic_prompt TEXT DEFAULT ''
)''')
c.execute('''CREATE TABLE IF NOT EXISTS posts_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_login TEXT,
    post_json TEXT,
    added_at TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS images (
    account_login TEXT PRIMARY KEY,
    path TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_login TEXT,
    post_json TEXT,
    posted_at TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)''')
conn.commit()

# --- Аккаунты ---

def get_all_accounts():
    c.execute('SELECT login FROM accounts')
    return [{'login': row[0]} for row in c.fetchall()]

def get_account(login):
    c.execute('SELECT * FROM accounts WHERE login=?', (login,))
    row = c.fetchone()
    if not row:
        return None
    keys = ['login', 'session_id', 'csrf_token', 'user_id', 'username', 'account_prompt', 'topic_prompt']
    return dict(zip(keys, row))

def save_account(account):
    c.execute('INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?,?)', (
        account['login'],
        account.get('session_id', ''),
        account.get('csrf_token', ''),
        account.get('user_id', ''),
        account.get('username', account['login']),
        account.get('account_prompt', ''),
        account.get('topic_prompt', '')
    ))
    conn.commit()

def add_account_prompt(login, account_prompt, topic_prompt):
    c.execute('UPDATE accounts SET account_prompt=?, topic_prompt=? WHERE login=?',
              (account_prompt, topic_prompt, login))
    conn.commit()

def update_account_cookies(login, session_id, csrf_token, user_id, username):
    c.execute('''UPDATE accounts SET session_id=?, csrf_token=?, user_id=?, username=?
                 WHERE login=?''', (session_id, csrf_token, user_id, username, login))
    conn.commit()

# --- Очередь постов ---

def add_series(series, account_login):
    c.execute('INSERT INTO posts_queue(account_login, post_json, added_at) VALUES(?,?,?)',
              (account_login, json.dumps(series, ensure_ascii=False), datetime.now().isoformat()))
    conn.commit()

def pop():
    c.execute('SELECT id, post_json, account_login FROM posts_queue ORDER BY id ASC LIMIT 1')
    row = c.fetchone()
    if not row:
        return None
    post_id, post_json, account_login = row
    c.execute('DELETE FROM posts_queue WHERE id=?', (post_id,))
    conn.commit()
    return {'id': post_id, 'posts': json.loads(post_json), 'account_login': account_login}

def count():
    c.execute('SELECT COUNT(*) FROM posts_queue')
    return c.fetchone()[0]

def get_queue(account_login=None):
    if account_login:
        c.execute('SELECT id, account_login, post_json, added_at FROM posts_queue WHERE account_login=? ORDER BY id ASC', (account_login,))
    else:
        c.execute('SELECT id, account_login, post_json, added_at FROM posts_queue ORDER BY id ASC')
    rows = c.fetchall()
    result = []
    for row in rows:
        posts = json.loads(row[2])
        result.append({
            'id': row[0],
            'account_login': row[1],
            'topic': posts.get('topic', '—'),
            'added_at': row[3]
        })
    return result

def delete_queue_item(item_id):
    c.execute('DELETE FROM posts_queue WHERE id=?', (item_id,))
    conn.commit()

# --- Изображения ---

def set_image(account_login, path):
    c.execute('INSERT OR REPLACE INTO images(account_login, path) VALUES(?,?)', (account_login, path))
    conn.commit()

def get_image(account_login):
    c.execute('SELECT path FROM images WHERE account_login=?', (account_login,))
    row = c.fetchone()
    return row[0] if row else None

# --- Настройки ---

def get_setting(key, default=None):
    c.execute('SELECT value FROM settings WHERE key=?', (key,))
    row = c.fetchone()
    return row[0] if row else default

def set_setting(key, value):
    c.execute('INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)', (key, str(value)))
    conn.commit()

# --- Архив ---

def archive_item(series, account_login):
    c.execute('INSERT INTO archive(account_login, post_json, posted_at) VALUES(?,?,?)',
              (account_login, json.dumps(series, ensure_ascii=False), datetime.now().isoformat()))
    conn.commit()

def get_archive(limit=20):
    c.execute('SELECT account_login, post_json, posted_at FROM archive ORDER BY id DESC LIMIT ?', (limit,))
    rows = c.fetchall()
    result = []
    for row in rows:
        posts = json.loads(row[1])
        result.append({
            'account_login': row[0],
            'topic': posts.get('topic', '—'),
            'posted_at': row[2]
        })
    return result
