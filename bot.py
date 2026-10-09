# ============================================================
#   EMPEROR HOSTING BOT — FULL WORKING EDITION
#   Approval-First Flow (No bypass, no auto-install before approve)
#   Force Join + Ban + Auto Install + Delete All (Stop → Delete)
# ============================================================

import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import threading
import re
import sys
import uuid
import atexit
import html as html_lib
import requests

# --- Flask Keep Alive ---
from flask import Flask

flask_app = Flask('')


@flask_app.route('/')
def home():
    return "Emperor Hosting is Alive"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)


def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Flask Keep-Alive started.")


# ============================================================
# CONFIG
# ============================================================
TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

YOUR_USERNAME = os.getenv("YOUR_USERNAME", "@username")
UPDATE_CHANNEL = os.getenv("UPDATE_CHANNEL", "")

FORCE_CHANNEL_ID = int(os.getenv("FORCE_CHANNEL_ID", "0"))
FORCE_CHANNEL_LINK = os.getenv("FORCE_CHANNEL_LINK", "")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
PENDING_DIR = os.path.join(BASE_DIR, 'pending_uploads')   # files wait here until approved
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

FREE_USER_LIMIT = 1
SUBSCRIBED_USER_LIMIT = 10
ADMIN_LIMIT = 999
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(PENDING_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)


# ============================================================
# DATA
# ============================================================

bot_scripts = {}                # key -> script info (running)
user_subscriptions = {}         # uid -> {"expiry": datetime}
user_files = {}                 # uid -> [(file_name, file_type), ...]  (only approved files go here)
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
banned_users = set()
bot_locked = False
pending_approvals = {}          # aid -> approval dict
handled_approvals = {}          # aid -> "approved" / "rejected"
approved_files = {}             # uid -> set(file_name)
rejected_files = {}             # uid -> set(file_name)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def safe_html(value):
    return html_lib.escape(str(value), quote=False)


# ============================================================
# RAW API (button style support)
# ============================================================

def raw_send(chat_id, text, reply_keyboard=None, parse_mode="HTML"):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_keyboard:
        payload["reply_markup"] = {
            "keyboard": reply_keyboard,
            "resize_keyboard": True,
            "is_persistent": True
        }
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          json=payload, timeout=60)
        return r.json()
    except Exception as e:
        logger.error(f"raw_send err: {e}")
        return {}


def raw_forward(chat_id, from_chat_id, message_id):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/forwardMessage",
                          json={
                              "chat_id": chat_id,
                              "from_chat_id": from_chat_id,
                              "message_id": message_id
                          }, timeout=60)
        return r.json()
    except Exception as e:
        logger.error(f"raw_forward err: {e}")
        return {}


def raw_send_photo(chat_id, photo, caption=None, parse_mode="HTML",
                   reply_keyboard=None):
    payload = {
        "chat_id": chat_id,
        "photo": photo,
        "parse_mode": parse_mode
    }
    if caption:
        payload["caption"] = caption
    if reply_keyboard:
        payload["reply_markup"] = {
            "keyboard": reply_keyboard,
            "resize_keyboard": True,
            "is_persistent": True
        }
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                          json=payload, timeout=60)
        return r.json()
    except Exception as e:
        logger.error(f"raw_send_photo err: {e}")
        return {}


def raw_answer_cb(cb_id, text="", alert=False):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery",
                      json={"callback_query_id": cb_id, "text": text, "show_alert": alert},
                      timeout=30)
    except:
        pass


def rbtn(text, style=None):
    b = {"text": text}
    if style:
        b["style"] = style
    return b


def ibtn(text, callback_data=None, style=None, url=None):
    b = {"text": text}
    if callback_data:
        b["callback_data"] = callback_data
    if url:
        b["url"] = url
    if style:
        b["style"] = style
    return b


def send_inline(chat_id, text, inline_rows, parse_mode="HTML"):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": inline_rows},
    }
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          json=payload, timeout=60)
        return r.json()
    except Exception as e:
        logger.error(f"send_inline err: {e}")
        return {}


def edit_inline(chat_id, message_id, text, inline_rows, parse_mode="HTML"):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": inline_rows},
    }
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/editMessageText",
                          json=payload, timeout=60)
        return r.json()
    except Exception as e:
        logger.error(f"edit_inline err: {e}")
        return {}


# ============================================================
# REPLY KEYBOARDS
# ============================================================

def get_main_kb(user_id):
    if user_id in admin_ids:
        return [
            [rbtn("📢 Updates Channel", "primary")],
            [rbtn("📤 Upload File", "primary"),
             rbtn("📂 Check Files", "primary")],
            [rbtn("⚡ Bot Speed", "primary"),
             rbtn("📊 Statistics", "success")],
            [rbtn("💳 Subscriptions", "primary"),
             rbtn("📢 Broadcast", "success")],
            [rbtn("🔒 Lock Bot", "danger"),
             rbtn("🟢 Running All Code", "success")],
            [rbtn("👑 Admin Panel", "primary"),
             rbtn("📞 Contact Owner", "primary")]
        ]
    else:
        return [
            [rbtn("📢 Updates Channel", "primary")],
            [rbtn("📤 Upload File", "primary"),
             rbtn("📂 Check Files", "primary")],
            [rbtn("⚡ Bot Speed", "primary"),
             rbtn("📊 Statistics", "success")],
            [rbtn("📞 Contact Owner", "primary")]
        ]


def get_admin_kb():
    return [
        [rbtn("👥 Total Users", "primary"),
         rbtn("📂 Total Files", "primary")],
        [rbtn("📢 Broadcast", "success")],
        [rbtn("🗑️ Delete All Code", "danger")],
        [rbtn("🔙 Back", "primary")]
    ]


def get_join_kb():
    return [
        [rbtn("➡️ Join", "primary")]
    ]


# ============================================================
# FORCE JOIN
# ============================================================

def is_user_joined(user_id):
    if user_id in admin_ids:
        return True
    try:
        m = bot.get_chat_member(FORCE_CHANNEL_ID, user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"force join check failed for {user_id}: {e}")
        return False


def force_join_prompt(chat_id):
    txt = (
        "⚠️ <b>Join Required!</b>\n\n"
        "➡️ Please join our channel to use this bot.\n"
        "✅ After joining, tap <b>Verify</b> to continue."
    )
    inline_rows = [[
        ibtn("➡️ Join Channel", None, "primary", url=FORCE_CHANNEL_LINK),
        ibtn("✅ Verify", "verify_join", "success")
    ]]
    send_inline(chat_id, txt, inline_rows)
    raw_send(chat_id,
             "⚠️ Tap <b>Join</b> below, then <b>Verify</b>.",
             reply_keyboard=get_join_kb())


# ============================================================
# DB
# ============================================================

def init_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expiry TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS user_files (user_id INTEGER, file_name TEXT, file_type TEXT, status TEXT DEFAULT "pending", PRIMARY KEY (user_id, file_name))')
        c.execute('CREATE TABLE IF NOT EXISTS active_users (user_id INTEGER PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY)')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        try:
            c.execute('ALTER TABLE user_files ADD COLUMN status TEXT DEFAULT "pending"')
        except:
            pass
        conn.commit()
        conn.close()
        logger.info("DB OK.")
    except Exception as e:
        logger.error(f"DB err: {e}")


def load_data():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for u, e in c.fetchall():
            try:
                user_subscriptions[u] = {'expiry': datetime.fromisoformat(e)}
            except:
                pass
        c.execute('SELECT user_id, file_name, file_type, status FROM user_files')
        for u, fn, ft, st in c.fetchall():
            if st == 'approved':
                user_files.setdefault(u, []).append((fn, ft))
                approved_files.setdefault(u, set()).add(fn)
            elif st == 'rejected':
                rejected_files.setdefault(u, set()).add(fn)
        c.execute('SELECT user_id FROM active_users')
        active_users.update(x for (x,) in c.fetchall())
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(x for (x,) in c.fetchall())
        c.execute('SELECT user_id FROM banned')
        banned_users.update(x for (x,) in c.fetchall())
        conn.close()
        logger.info(f"Loaded: {len(active_users)} users, {len(banned_users)} banned")
    except Exception as e:
        logger.error(f"Load err: {e}")


init_db()
load_data()


# ============================================================
# HELPERS
# ============================================================

def get_user_folder(uid):
    f = os.path.join(UPLOAD_BOTS_DIR, str(uid))
    os.makedirs(f, exist_ok=True)
    return f


def get_pending_folder(uid):
    f = os.path.join(PENDING_DIR, str(uid))
    os.makedirs(f, exist_ok=True)
    return f


def get_user_file_limit(uid):
    if uid == OWNER_ID:
        return OWNER_LIMIT
    if uid in admin_ids:
        return ADMIN_LIMIT
    if uid in user_subscriptions and user_subscriptions[uid]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT


def get_user_file_count(uid):
    return len(user_files.get(uid, []))


def is_file_approved(uid, fn):
    return fn in approved_files.get(uid, set())


def is_file_rejected(uid, fn):
    return fn in rejected_files.get(uid, set())


def is_bot_running(owner, fn):
    key = f"{owner}_{fn}"
    info = bot_scripts.get(key)
    if info and info.get('process'):
        try:
            p = psutil.Process(info['process'].pid)
            r = p.is_running() and p.status() != psutil.STATUS_ZOMBIE
            if not r:
                if 'log_file' in info and hasattr(info['log_file'], 'close') and not info['log_file'].closed:
                    try:
                        info['log_file'].close()
                    except:
                        pass
                bot_scripts.pop(key, None)
            return r
        except psutil.NoSuchProcess:
            if 'log_file' in info and hasattr(info['log_file'], 'close') and not info['log_file'].closed:
                try:
                    info['log_file'].close()
                except:
                    pass
            bot_scripts.pop(key, None)
            return False
        except:
            return False
    return False


def kill_process_tree(info):
    try:
        if 'log_file' in info and hasattr(info['log_file'], 'close') and not info['log_file'].closed:
            try:
                info['log_file'].close()
            except:
                pass
        p = info.get('process')
        if p and hasattr(p, 'pid'):
            pid = p.pid
            if pid:
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    for ch in children:
                        try:
                            ch.terminate()
                        except:
                            try:
                                ch.kill()
                            except:
                                pass
                    gone, alive = psutil.wait_procs(children, timeout=1)
                    for x in alive:
                        try:
                            x.kill()
                        except:
                            pass
                    try:
                        parent.terminate()
                        try:
                            parent.wait(timeout=1)
                        except psutil.TimeoutExpired:
                            parent.kill()
                    except psutil.NoSuchProcess:
                        pass
                    except:
                        try:
                            parent.kill()
                        except:
                            pass
                except psutil.NoSuchProcess:
                    pass
    except Exception as e:
        logger.error(f"kill err: {e}")


# ============================================================
# AUTO INSTALL
# ============================================================

TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI', 'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram', 'pyrogram': 'pyrogram', 'telethon': 'telethon',
    'requests': 'requests', 'bs4': 'beautifulsoup4', 'pillow': 'Pillow',
    'cv2': 'opencv-python', 'yaml': 'PyYAML', 'dotenv': 'python-dotenv',
    'pandas': 'pandas', 'numpy': 'numpy', 'flask': 'Flask',
    'psutil': 'psutil', 'uuid': None, 'asyncio': None, 'json': None,
    'datetime': None, 'os': None, 'sys': None, 're': None, 'time': None,
    'math': None, 'random': None, 'logging': None, 'threading': None,
    'subprocess': None, 'zipfile': None, 'tempfile': None, 'shutil': None,
    'sqlite3': None, 'atexit': None
}


def attempt_install_pip(mod, msg):
    pkg = TELEGRAM_MODULES.get(mod.lower(), mod)
    if pkg is None:
        return False
    try:
        bot.reply_to(msg, f"🐍 Installing `{pkg}`...", parse_mode='Markdown')
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', pkg],
                           capture_output=True, text=True, check=False,
                           encoding='utf-8', errors='ignore')
        if r.returncode == 0:
            bot.reply_to(msg, "✅ Installed `" + pkg + "`.", parse_mode='HTML')
            return True
        else:
            err = "❌ Failed.\n<pre>" + safe_html(r.stderr or r.stdout) + "</pre>"
            if len(err) > 4000:
                err = err[:4000] + "\n..."
            bot.reply_to(msg, err, parse_mode='HTML')
            return False
    except Exception as e:
        bot.reply_to(msg, "❌ Error: " + safe_html(e), parse_mode='HTML')
        return False


def attempt_install_npm(mod, folder, msg):
    try:
        bot.reply_to(msg, f"🟠 Installing npm `{mod}`...")
        r = subprocess.run(['npm', 'install', mod], capture_output=True, text=True,
                           check=False, cwd=folder, encoding='utf-8', errors='ignore')
        if r.returncode == 0:
            bot.reply_to(msg, "✅ npm `" + mod + "` installed.", parse_mode='HTML')
            return True
        else:
            err = "❌ npm failed.\n<pre>" + safe_html(r.stderr or r.stdout) + "</pre>"
            if len(err) > 4000:
                err = err[:4000] + "\n..."
            bot.reply_to(msg, err, parse_mode='HTML')
            return False
    except FileNotFoundError:
        bot.reply_to(msg, "❌ 'npm' not found.", parse_mode='HTML')
        return False
    except Exception as e:
        bot.reply_to(msg, "❌ Error: " + safe_html(e), parse_mode='HTML')
        return False


# ============================================================
# RUN SCRIPTS (only if approved)
# ============================================================

def run_script(path, owner, folder, fn, msg_obj, attempt=1, force=False):
    if not force and not is_file_approved(owner, fn):
        bot.reply_to(msg_obj,
                     "🔒 <b>Not approved yet!</b>\n\n"
                     "This file is still awaiting admin approval. You cannot run it now.",
                     parse_mode='HTML')
        return
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(msg_obj,
                     "❌ Failed after " + str(max_attempts) + " attempts.",
                     parse_mode='HTML')
        return
    key = f"{owner}_{fn}"
    if is_bot_running(owner, fn):
        bot.reply_to(msg_obj,
                     "⚠️ <b>Already running!</b>\n\n"
                     "File <code>" + safe_html(fn) + "</code> is already running for this user.\n"
                     "Stop it first or change the filename.",
                     parse_mode='HTML')
        return
    try:
        if not os.path.exists(path):
            bot.reply_to(msg_obj, "❌ Script not found.", parse_mode='HTML')
            if owner in user_files:
                user_files[owner] = [f for f in user_files.get(owner, []) if f[0] != fn]
            remove_user_file_db(owner, fn)
            return
        if attempt == 1:
            cp = None
            try:
                cp = subprocess.Popen([sys.executable, path], cwd=folder,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding='utf-8', errors='ignore')
                out, err = cp.communicate(timeout=5)
                rc = cp.returncode
                if rc != 0 and err:
                    m = re.search(r"ModuleNotFoundError: No module named '(.+?)'", err)
                    if m:
                        mod = m.group(1).strip().strip("'\"")
                        if attempt_install_pip(mod, msg_obj):
                            bot.reply_to(msg_obj, "⏳ Retrying...", parse_mode='HTML')
                            time.sleep(2)
                            threading.Thread(target=run_script,
                                             args=(path, owner, folder, fn, msg_obj, attempt + 1, True)).start()
                            return
                        return
                    else:
                        bot.reply_to(msg_obj,
                                     "❌ Error:\n<pre>" + safe_html(err[:500]) + "</pre>",
                                     parse_mode='HTML')
                        return
            except subprocess.TimeoutExpired:
                if cp and cp.poll() is None:
                    cp.kill()
                    cp.communicate()
            except FileNotFoundError:
                bot.reply_to(msg_obj, "❌ Python not found.", parse_mode='HTML')
                return
            except Exception as e:
                bot.reply_to(msg_obj, "❌ Error: " + safe_html(e), parse_mode='HTML')
                return
            finally:
                if cp and cp.poll() is None:
                    cp.kill()
                    cp.communicate()
        log_path = os.path.join(folder, f"{os.path.splitext(fn)[0]}.log")
        lf = None
        pr = None
        try:
            lf = open(log_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(msg_obj, "❌ Log open failed: " + safe_html(e), parse_mode='HTML')
            return
        try:
            si = None
            cf = 0
            if os.name == 'nt':
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = subprocess.SW_HIDE
            pr = subprocess.Popen([sys.executable, path], cwd=folder,
                                  stdout=lf, stderr=lf, stdin=subprocess.PIPE,
                                  startupinfo=si, creationflags=cf,
                                  encoding='utf-8', errors='ignore')
            bot_scripts[key] = {
                'process': pr, 'log_file': lf, 'file_name': fn,
                'chat_id': msg_obj.chat.id, 'script_owner_id': owner,
                'start_time': datetime.now(), 'user_folder': folder,
                'type': 'py', 'script_key': key
            }
            bot.reply_to(msg_obj,
                         "✅ Python script '" + safe_html(fn) + "' started! (PID: " + str(pr.pid) + ")",
                         parse_mode='HTML')
        except FileNotFoundError:
            if lf and not lf.closed:
                lf.close()
            bot.reply_to(msg_obj, "❌ Python not found.", parse_mode='HTML')
            bot_scripts.pop(key, None)
        except Exception as e:
            if lf and not lf.closed:
                lf.close()
            bot.reply_to(msg_obj, "❌ Error: " + safe_html(e), parse_mode='HTML')
            if pr and pr.poll() is None:
                kill_process_tree({'process': pr, 'log_file': lf, 'script_key': key})
            bot_scripts.pop(key, None)
    except Exception as e:
        bot.reply_to(msg_obj, "❌ Error: " + safe_html(e), parse_mode='HTML')
        if key in bot_scripts:
            kill_process_tree(bot_scripts[key])
            del bot_scripts[key]


def run_js_script(path, owner, folder, fn, msg_obj, attempt=1, force=False):
    if not force and not is_file_approved(owner, fn):
        bot.reply_to(msg_obj,
                     "🔒 <b>Not approved yet!</b>\n\n"
                     "This file is still awaiting admin approval. You cannot run it now.",
                     parse_mode='HTML')
        return
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(msg_obj,
                     "❌ Failed after " + str(max_attempts) + " attempts.",
                     parse_mode='HTML')
        return
    key = f"{owner}_{fn}"
    if is_bot_running(owner, fn):
        bot.reply_to(msg_obj,
                     "⚠️ <b>Already running!</b>\n\n"
                     "File <code>" + safe_html(fn) + "</code> is already running for this user.\n"
                     "Stop it first or change the filename.",
                     parse_mode='HTML')
        return
    try:
        if not os.path.exists(path):
            bot.reply_to(msg_obj, "❌ Script not found.", parse_mode='HTML')
            if owner in user_files:
                user_files[owner] = [f for f in user_files.get(owner, []) if f[0] != fn]
            remove_user_file_db(owner, fn)
            return
        if attempt == 1:
            cp = None
            try:
                cp = subprocess.Popen(['node', path], cwd=folder,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding='utf-8', errors='ignore')
                out, err = cp.communicate(timeout=5)
                rc = cp.returncode
                if rc != 0 and err:
                    m = re.search(r"Cannot find module '(.+?)'", err)
                    if m:
                        mod = m.group(1).strip().strip("'\"")
                        if not mod.startswith('.') and not mod.startswith('/'):
                            if attempt_install_npm(mod, folder, msg_obj):
                                bot.reply_to(msg_obj, "⏳ Retrying...", parse_mode='HTML')
                                time.sleep(2)
                                threading.Thread(target=run_js_script,
                                                 args=(path, owner, folder, fn, msg_obj, attempt + 1, True)).start()
                                return
                            return
                    bot.reply_to(msg_obj,
                                 "❌ Error:\n<pre>" + safe_html(err[:500]) + "</pre>",
                                 parse_mode='HTML')
                    return
            except subprocess.TimeoutExpired:
                if cp and cp.poll() is None:
                    cp.kill()
                    cp.communicate()
            except FileNotFoundError:
                bot.reply_to(msg_obj, "❌ 'node' not found.", parse_mode='HTML')
                return
            except Exception as e:
                bot.reply_to(msg_obj, "❌ Error: " + safe_html(e), parse_mode='HTML')
                return
            finally:
                if cp and cp.poll() is None:
                    cp.kill()
                    cp.communicate()
        log_path = os.path.join(folder, f"{os.path.splitext(fn)[0]}.log")
        lf = None
        pr = None
        try:
            lf = open(log_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(msg_obj, "❌ Log: " + safe_html(e), parse_mode='HTML')
            return
        try:
            si = None
            cf = 0
            if os.name == 'nt':
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = subprocess.SW_HIDE
            pr = subprocess.Popen(['node', path], cwd=folder,
                                  stdout=lf, stderr=lf, stdin=subprocess.PIPE,
                                  startupinfo=si, creationflags=cf,
                                  encoding='utf-8', errors='ignore')
            bot_scripts[key] = {
                'process': pr, 'log_file': lf, 'file_name': fn,
                'chat_id': msg_obj.chat.id, 'script_owner_id': owner,
                'start_time': datetime.now(), 'user_folder': folder,
                'type': 'js', 'script_key': key
            }
            bot.reply_to(msg_obj,
                         "✅ JS script '" + safe_html(fn) + "' started! (PID: " + str(pr.pid) + ")",
                         parse_mode='HTML')
        except FileNotFoundError:
            if lf and not lf.closed:
                lf.close()
            bot.reply_to(msg_obj, "❌ 'node' not found.", parse_mode='HTML')
            bot_scripts.pop(key, None)
        except Exception as e:
            if lf and not lf.closed:
                lf.close()
            bot.reply_to(msg_obj, "❌ Error: " + safe_html(e), parse_mode='HTML')
            if pr and pr.poll() is None:
                kill_process_tree({'process': pr, 'log_file': lf, 'script_key': key})
            bot_scripts.pop(key, None)
    except Exception as e:
        bot.reply_to(msg_obj, "❌ Error: " + safe_html(e), parse_mode='HTML')
        if key in bot_scripts:
            kill_process_tree(bot_scripts[key])
            del bot_scripts[key]


# ============================================================
# APPROVAL-TIME PROCESSING (called ONLY after Approve)
# ============================================================

def process_approved_file(ap):
    """Called after admin approves. Extracts zip, installs deps, moves to user folder,
    adds to user_files, then starts the script. All inside a thread."""
    uid = ap['user_id']
    fn = ap['file_name']
    ft = ap['file_type']
    is_zip = ap.get('is_zip', False)
    pending_folder = ap['pending_folder']
    src_path = ap['src_path']
    message = ap.get('message_info')
    user_folder = get_user_folder(uid)

    try:
        if is_zip:
            tdir = tempfile.mkdtemp(prefix=f"u_{uid}_ext_")
            try:
                with zipfile.ZipFile(src_path, 'r') as z:
                    for m in z.infolist():
                        mp = os.path.abspath(os.path.join(tdir, m.filename))
                        if not mp.startswith(os.path.abspath(tdir)):
                            raise zipfile.BadZipFile(f"Unsafe: {m.filename}")
                    z.extractall(tdir)
                items = os.listdir(tdir)
                req = 'requirements.txt' if 'requirements.txt' in items else None
                pkg = 'package.json' if 'package.json' in items else None
                if req:
                    rp = os.path.join(tdir, req)
                    try:
                        if message:
                            bot.reply_to(message,
                                         "⏳ Installing Python deps...",
                                         parse_mode='HTML')
                        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', rp],
                                       capture_output=True, text=True, check=True,
                                       encoding='utf-8', errors='ignore')
                        if message:
                            bot.reply_to(message, "✅ Python deps installed.", parse_mode='HTML')
                    except subprocess.CalledProcessError as e:
                        err = "❌ Failed.\n<pre>" + safe_html(e.stderr or e.stdout) + "</pre>"
                        if len(err) > 4000:
                            err = err[:4000] + "\n..."
                        if message:
                            bot.reply_to(message, err, parse_mode='HTML')
                if pkg:
                    try:
                        if message:
                            bot.reply_to(message, "⏳ Installing Node deps...", parse_mode='HTML')
                        subprocess.run(['npm', 'install'], capture_output=True, text=True,
                                       check=True, cwd=tdir, encoding='utf-8', errors='ignore')
                        if message:
                            bot.reply_to(message, "✅ Node deps installed.", parse_mode='HTML')
                    except FileNotFoundError:
                        if message:
                            bot.reply_to(message, "❌ 'npm' not found.", parse_mode='HTML')
                    except subprocess.CalledProcessError as e:
                        err = "❌ Failed.\n<pre>" + safe_html(e.stderr or e.stdout) + "</pre>"
                        if len(err) > 4000:
                            err = err[:4000] + "\n..."
                        if message:
                            bot.reply_to(message, err, parse_mode='HTML')
                # Move extracted files into user folder
                for name in os.listdir(tdir):
                    src = os.path.join(tdir, name)
                    dst = os.path.join(user_folder, name)
                    if os.path.isdir(dst):
                        shutil.rmtree(dst, ignore_errors=True)
                    elif os.path.exists(dst):
                        try:
                            os.remove(dst)
                        except:
                            pass
                    shutil.move(src, dst)
            finally:
                shutil.rmtree(tdir, ignore_errors=True)
        else:
            # Simple .py / .js file — move from pending to user folder
            dst = os.path.join(user_folder, fn)
            if os.path.exists(dst):
                try:
                    os.remove(dst)
                except:
                    pass
            shutil.move(src_path, dst)

        # Persist approved status
        save_user_file(uid, fn, ft, 'approved')
        set_file_status(uid, fn, 'approved')

        # Cleanup pending folder if empty
        try:
            if os.path.isdir(pending_folder) and not os.listdir(pending_folder):
                os.rmdir(pending_folder)
        except:
            pass

        # Start the script
        real_path = os.path.join(user_folder, fn)
        if ft == 'py':
            run_script(real_path, uid, user_folder, fn, _FakeMsg(uid), 1, True)
        elif ft == 'js':
            run_js_script(real_path, uid, user_folder, fn, _FakeMsg(uid), 1, True)

        notify_user_approved(uid, fn, ft)
    except Exception as e:
        logger.error(f"process_approved_file err: {e}", exc_info=True)
        try:
            raw_send(uid,
                     "❌ Error while processing approved file: " + safe_html(e),
                     reply_keyboard=get_main_kb(uid))
        except:
            pass


# ============================================================
# DB OPS
# ============================================================

DB_LOCK = threading.Lock()


def save_user_file(uid, fn, ft='py', status='pending'):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type, status) VALUES (?, ?, ?, ?)',
                      (uid, fn, ft, status))
            conn.commit()
            if status == 'approved':
                if uid not in user_files:
                    user_files[uid] = []
                user_files[uid] = [(n, t) for n, t in user_files[uid] if n != fn]
                user_files[uid].append((fn, ft))
        except Exception as e:
            logger.error(f"save_user_file: {e}")
        finally:
            conn.close()


def set_file_status(uid, fn, status):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('UPDATE user_files SET status = ? WHERE user_id = ? AND file_name = ?',
                      (status, uid, fn))
            conn.commit()
        except Exception as e:
            logger.error(f"set_file_status: {e}")
        finally:
            conn.close()
    if status == 'approved':
        approved_files.setdefault(uid, set()).add(fn)
        rejected_files.get(uid, set()).discard(fn)
    elif status == 'rejected':
        rejected_files.setdefault(uid, set()).add(fn)
        approved_files.get(uid, set()).discard(fn)


def remove_user_file_db(uid, fn):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (uid, fn))
            conn.commit()
            if uid in user_files:
                user_files[uid] = [f for f in user_files[uid] if f[0] != fn]
                if not user_files[uid]:
                    del user_files[uid]
        except Exception as e:
            logger.error(f"remove_user_file: {e}")
        finally:
            conn.close()
    approved_files.get(uid, set()).discard(fn)
    rejected_files.get(uid, set()).discard(fn)


def add_active_user(uid):
    active_users.add(uid)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (uid,))
            conn.commit()
        except Exception as e:
            logger.error(f"add_active_user: {e}")
        finally:
            conn.close()


def add_banned_db(uid):
    banned_users.add(uid)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO banned (user_id) VALUES (?)', (uid,))
            conn.commit()
        except Exception as e:
            logger.error(f"add_banned: {e}")
        finally:
            conn.close()


def remove_banned_db(uid):
    banned_users.discard(uid)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM banned WHERE user_id = ?', (uid,))
            conn.commit()
        except Exception as e:
            logger.error(f"remove_banned: {e}")
        finally:
            conn.close()


def save_subscription(uid, exp):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)',
                      (uid, exp.isoformat()))
            conn.commit()
            user_subscriptions[uid] = {'expiry': exp}
        except Exception as e:
            logger.error(f"save_sub: {e}")
        finally:
            conn.close()


def remove_subscription_db(uid):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (uid,))
            conn.commit()
            user_subscriptions.pop(uid, None)
        except Exception as e:
            logger.error(f"remove_sub: {e}")
        finally:
            conn.close()


def add_admin_db(uid):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (uid,))
            conn.commit()
            admin_ids.add(uid)
        except Exception as e:
            logger.error(f"add_admin: {e}")
        finally:
            conn.close()


def remove_admin_db(uid):
    if uid == OWNER_ID:
        return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        removed = False
        try:
            c.execute('DELETE FROM admins WHERE user_id = ?', (uid,))
            conn.commit()
            removed = c.rowcount > 0
            if removed:
                admin_ids.discard(uid)
            return removed
        except Exception as e:
            logger.error(f"remove_admin: {e}")
            return False
        finally:
            conn.close()


def clear_all_user_files_db():
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files')
            conn.commit()
        except Exception as e:
            logger.error(f"clear_all_user_files_db: {e}")
        finally:
            conn.close()
    user_files.clear()
    approved_files.clear()
    rejected_files.clear()


# ============================================================
# NOTIFICATIONS
# ============================================================

def notify_admin_new_file(aid, uid, uname, fname, fn, ft, is_zip=False,
                          src_chat_id=None, src_message_id=None):
    txt = (
        "🔔 <b>New File Upload — Approval Needed!</b>\n\n"
        "👤 Name: <b>" + safe_html(fname) + "</b>\n"
        "👤 Username: @" + safe_html(uname or 'N/A') + "\n"
        "👤 User ID: <code>" + str(uid) + "</code>\n\n"
        "📁 File: <code>" + safe_html(fn) + "</code>\n"
        "⚙️ Type: <b>" + ft.upper() + "</b>"
        + ("\n📦 (ZIP Archive)" if is_zip else "") +
        "\n\n📌 Approval ID: <code>" + aid + "</code>\n\n"
        "🔒 <i>File is NOT processed yet. Nothing has been extracted or installed.</i>"
    )
    inline_rows = [[
        ibtn("✅ Approve", f"approve_{aid}", "success"),
        ibtn("❌ Reject", f"reject_{aid}", "danger"),
    ]]
    targets = set([int(ADMIN_ID), int(OWNER_ID)]) | set(admin_ids)
    for admin in targets:
        try:
            if src_chat_id is not None and src_message_id is not None:
                raw_forward(admin, src_chat_id, src_message_id)
        except Exception as e:
            logger.error(f"forward file to {admin}: {e}")
        try:
            send_inline(admin, txt, inline_rows)
        except Exception as e:
            logger.error(f"notify admin {admin}: {e}")


def notify_user_approved(uid, fn, ft):
    try:
        txt = (
            "✅ <b>File Approved & Started!</b>\n\n"
            "📁 File: <code>" + safe_html(fn) + "</code>\n"
            "⚙️ Type: " + ft.upper() + "\n\n"
            "🚀 Your file is now running!"
        )
        raw_send(uid, txt, reply_keyboard=get_main_kb(uid))
    except Exception as e:
        logger.error(f"notify_user_approved: {e}")


def notify_user_rejected(uid, fn, reason="Admin rejected the file."):
    try:
        txt = (
            "❌ <b>File Rejected!</b>\n\n"
            "📁 File: <code>" + safe_html(fn) + "</code>\n\n"
            "⚠️ <b>Reason:</b> " + safe_html(reason) + "\n\n"
            "🚫 <b>Warning:</b> Do not upload such files again.\n"
            "⚠️ If you keep uploading invalid files, you may be banned from this bot."
        )
        raw_send(uid, txt, reply_keyboard=get_main_kb(uid))
    except Exception as e:
        logger.error(f"notify_user_rejected: {e}")


def notify_user_pending(uid, fn, ft):
    try:
        txt = (
            "⏳ <b>File sent for approval!</b>\n\n"
            "📁 File: <code>" + safe_html(fn) + "</code>\n"
            "⚙️ Type: " + ft.upper() + "\n\n"
            "🔒 Nothing has been started yet.\n"
            "Admin will review and approve/reject."
        )
        raw_send(uid, txt, reply_keyboard=get_main_kb(uid))
    except Exception as e:
        logger.error(f"notify_user_pending: {e}")


# ============================================================
# FILE HANDLING — NO processing until approved
# ============================================================

def save_pending_file(uid, fn, content):
    """Save raw file into pending folder (not user folder)."""
    pf = get_pending_folder(uid)
    p = os.path.join(pf, fn)
    with open(p, 'wb') as f:
        f.write(content)
    return p


def handle_doc_new(m):
    """All uploads go to pending folder + notify admin. Nothing runs yet."""
    uid = m.from_user.id

    if uid in banned_users and uid not in admin_ids:
        raw_send(m.chat.id, "🚫 You are banned from this bot.")
        return

    if not is_user_joined(uid):
        force_join_prompt(m.chat.id)
        return

    doc = m.document
    if bot_locked and uid not in admin_ids:
        raw_send(m.chat.id, "⚠️ Bot locked.", reply_keyboard=get_main_kb(uid))
        return

    limit = get_user_file_limit(uid)
    curr = get_user_file_count(uid)
    if curr >= limit:
        lim_str = str(limit) if limit != float('inf') else "Unlimited"
        raw_send(m.chat.id,
                 "⚠️ Limit (" + str(curr) + "/" + lim_str + ").",
                 reply_keyboard=get_main_kb(uid))
        return

    fn = doc.file_name
    if not fn:
        raw_send(m.chat.id, "❌ No file name.", reply_keyboard=get_main_kb(uid))
        return
    ext = os.path.splitext(fn)[1].lower()
    if ext not in ['.py', '.js', '.zip']:
        raw_send(m.chat.id, "❌ Only .py, .js, .zip.", reply_keyboard=get_main_kb(uid))
        return
    if doc.file_size > 20 * 1024 * 1024:
        raw_send(m.chat.id, "❌ Max 20MB.", reply_keyboard=get_main_kb(uid))
        return

    # Check duplicate pending / approved names
    existing_names = [f[0] for f in user_files.get(uid, [])]
    for aid, ap in pending_approvals.items():
        if ap['user_id'] == uid and ap['file_name'] == fn:
            raw_send(m.chat.id,
                     "⚠️ <b>File name already pending!</b>\n\n"
                     "You already have <code>" + safe_html(fn) + "</code> waiting for approval.\n"
                     "Change the name or wait for approval.",
                     reply_keyboard=get_main_kb(uid))
            return
    if ext in ('.py', '.js') and fn in existing_names:
        raw_send(m.chat.id,
                 "⚠️ <b>File name already exists!</b>\n\n"
                 "You already have <code>" + safe_html(fn) + "</code>.\n"
                 "Please change the filename.",
                 reply_keyboard=get_main_kb(uid))
        return

    try:
        wm = bot.reply_to(m,
                          "⏳ Downloading <code>" + safe_html(fn) + "</code>...",
                          parse_mode='HTML')
        fi = bot.get_file(doc.file_id)
        content = bot.download_file(fi.file_path)
        bot.edit_message_text("✅ Downloaded. Sending to admin for approval...",
                              m.chat.id, wm.message_id, parse_mode='HTML')

        # Save to PENDING folder — no extract, no install, no run
        src_path = save_pending_file(uid, fn, content)
        ft = 'js' if ext == '.js' else ('py' if ext == '.py' else 'zip')

        aid = str(uuid.uuid4())[:8].upper()
        pending_approvals[aid] = {
            'user_id': uid,
            'file_name': fn if ext != '.zip' else fn,  # for zip, main script resolved on approval
            'file_type': ft,
            'src_path': src_path,
            'pending_folder': get_pending_folder(uid),
            'user_folder': get_user_folder(uid),
            'message_info': m,
            'is_zip': (ext == '.zip'),
            'timestamp': time.time()
        }

        notify_user_pending(uid, fn, ft)
        notify_admin_new_file(aid, uid, m.from_user.username,
                              m.from_user.first_name, fn, ft,
                              is_zip=(ext == '.zip'),
                              src_chat_id=m.chat.id,
                              src_message_id=m.message_id)
    except telebot.apihelper.ApiTelegramException as e:
        if "file is too big" in str(e).lower():
            raw_send(m.chat.id, "❌ File too large.", reply_keyboard=get_main_kb(uid))
        else:
            raw_send(m.chat.id, "❌ TG Error: " + safe_html(e), reply_keyboard=get_main_kb(uid))
    except Exception as e:
        logger.error(f"doc err: {e}", exc_info=True)
        raw_send(m.chat.id, "❌ Error: " + safe_html(e), reply_keyboard=get_main_kb(uid))


# ============================================================
# USER PROFILE PHOTO
# ============================================================

def get_user_photo_url(user_id):
    try:
        photos = bot.get_user_profile_photos(user_id, limit=1)
        if photos and photos.total_count > 0:
            file_id = photos.photos[0][-1].file_id
            f = bot.get_file(file_id)
            return "https://api.telegram.org/file/bot" + TOKEN + "/" + f.file_path
    except Exception as e:
        logger.warning(f"photo fetch failed for {user_id}: {e}")
    return None


# ============================================================
# WELCOME
# ============================================================

def send_welcome(chat_id, user_id, first_name, username):
    global bot_locked

    if user_id in banned_users and user_id not in admin_ids:
        raw_send(chat_id,
                 "🚫 <b>You are banned from this bot.</b>\n\n"
                 "Contact owner if you think this is a mistake.")
        return

    if bot_locked and user_id not in admin_ids:
        raw_send(chat_id, "⚠️ Bot locked by admin.",
                 reply_keyboard=get_main_kb(user_id))
        return

    if not is_user_joined(user_id):
        force_join_prompt(chat_id)
        return

    if user_id not in active_users:
        add_active_user(user_id)
        try:
            bot.send_message(OWNER_ID,
                             "🎉 New user!\n👤 " + safe_html(first_name) + "\n✳️ @" + safe_html(username or 'N/A') + "\n🆔 <code>" + str(user_id) + "</code>",
                             parse_mode='HTML')
        except:
            pass

    limit = get_user_file_limit(user_id)
    curr = get_user_file_count(user_id)
    lim_str = str(limit) if limit != float('inf') else "Unlimited"
    exp_info = ""
    if user_id == OWNER_ID:
        st = "👑 Owner"
    elif user_id in admin_ids:
        st = "📊 Admin"
    elif user_id in user_subscriptions:
        e = user_subscriptions[user_id].get('expiry')
        if e and e > datetime.now():
            st = "✅ Premium"
            dl = (e - datetime.now()).days
            exp_info = "\n⏳ Expires in " + str(dl) + " days"
        else:
            st = "👤 Free (Expired)"
            remove_subscription_db(user_id)
    else:
        st = "👤 Free User"

    caption = (
        "┏━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃ 🚀 <b>EMPEROR HOSTING</b> ┃\n"
        "┃ <b>VERSION 1.0</b> ┃\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "👋 <b>Welcome, " + safe_html(first_name) + "!</b>\n\n"
        "🆔 ID: <code>" + str(user_id) + "</code>\n"
        "✳️ Username: @" + safe_html(username or 'Not set') + "\n"
        "🔰 Status: " + st + exp_info + "\n"
        "📁 Files: " + str(curr) + " / " + lim_str + "\n\n"
        "⚙️ Host & run Python (.py) or JS (.js) scripts.\n"
        "⚠️ Every file goes to <b>Admin Approval</b> first. Nothing runs until approved.\n\n"
        "💎 Use buttons below."
    )

    photo_url = get_user_photo_url(user_id)
    if photo_url:
        res = raw_send_photo(chat_id, photo_url, caption=caption,
                             parse_mode="HTML",
                             reply_keyboard=get_main_kb(user_id))
        if not res.get("ok"):
            raw_send(chat_id, caption, reply_keyboard=get_main_kb(user_id))
    else:
        raw_send(chat_id, caption, reply_keyboard=get_main_kb(user_id))


# ============================================================
# COMMANDS
# ============================================================

@bot.message_handler(commands=['start', 'help'])
def cmd_start(m):
    send_welcome(m.chat.id, m.from_user.id, m.from_user.first_name, m.from_user.username)


@bot.message_handler(commands=['admin'])
def cmd_admin(m):
    if m.from_user.id in admin_ids:
        raw_send(m.chat.id,
                 "📊 <b>Admin Panel</b>\n📌 Use buttons below.",
                 reply_keyboard=get_admin_kb())
    else:
        bot.reply_to(m, "❌ Not admin.", parse_mode='HTML')


@bot.message_handler(commands=['ban'])
def cmd_ban(m):
    uid = m.from_user.id
    if uid not in admin_ids:
        bot.reply_to(m, "❌ Admin only.", parse_mode='HTML')
        return
    parts = m.text.strip().split()
    if len(parts) != 2 or not parts[1].lstrip('-').isdigit():
        bot.reply_to(m, "⚠️ Usage: <code>/ban USER_ID</code>", parse_mode='HTML')
        return
    tid = int(parts[1])
    if tid == OWNER_ID:
        bot.reply_to(m, "❌ Cannot ban owner.", parse_mode='HTML')
        return
    add_banned_db(tid)
    bot.reply_to(m, "✅ User <code>" + str(tid) + "</code> banned.", parse_mode='HTML')
    try:
        bot.send_message(tid, "🚫 <b>You are banned from this bot.</b>", parse_mode='HTML')
    except:
        pass


@bot.message_handler(commands=['unban'])
def cmd_unban(m):
    uid = m.from_user.id
    if uid not in admin_ids:
        bot.reply_to(m, "❌ Admin only.", parse_mode='HTML')
        return
    parts = m.text.strip().split()
    if len(parts) != 2 or not parts[1].lstrip('-').isdigit():
        bot.reply_to(m, "⚠️ Usage: <code>/unban USER_ID</code>", parse_mode='HTML')
        return
    tid = int(parts[1])
    remove_banned_db(tid)
    bot.reply_to(m, "✅ User <code>" + str(tid) + "</code> unbanned.", parse_mode='HTML')
    try:
        bot.send_message(tid, "✅ You have been unbanned. Send /start.", parse_mode='HTML')
    except:
        pass


# ============================================================
# APPROVAL (text fallback)
# ============================================================

@bot.message_handler(func=lambda m: m.text and (
    m.text.lower().startswith("approve ") or m.text.lower().startswith("reject ")))
def handle_approval_text(m):
    uid = m.from_user.id
    if uid not in admin_ids:
        bot.reply_to(m, "❌ Admin only.", parse_mode='HTML')
        return
    parts = m.text.strip().split()
    if len(parts) != 2:
        bot.reply_to(m,
                     "⚠️ Use: <code>approve ABCD1234</code> or <code>reject ABCD1234</code>",
                     parse_mode='HTML')
        return
    action = parts[0].lower()
    aid = parts[1].upper()
    if aid in handled_approvals:
        bot.reply_to(m, "🔒 This approval is already handled and locked.", parse_mode='HTML')
        return
    if aid not in pending_approvals:
        bot.reply_to(m, "❌ Approval ID not found.", parse_mode='HTML')
        return
    ap = pending_approvals[aid]
    file_uid = ap['user_id']
    fn = ap['file_name']
    ft = ap['file_type']

    if action == "approve":
        del pending_approvals[aid]
        handled_approvals[aid] = "approved"
        threading.Thread(target=process_approved_file, args=(ap,)).start()
        bot.reply_to(m,
                     "✅ <b>Approved! Processing...</b>\n\n"
                     "👤 User: <code>" + str(file_uid) + "</code>\n"
                     "📁 File: <code>" + safe_html(fn) + "</code>",
                     parse_mode='HTML')
    elif action == "reject":
        del pending_approvals[aid]
        handled_approvals[aid] = "rejected"
        set_file_status(file_uid, fn, 'rejected')
        # Delete pending file
        try:
            if os.path.exists(ap['src_path']):
                os.remove(ap['src_path'])
        except Exception as e:
            logger.error(f"reject del: {e}")
        notify_user_rejected(file_uid, fn, reason="Admin rejected the file.")
        bot.reply_to(m,
                     "❌ <b>Rejected & Deleted!</b>\n\n"
                     "👤 User: <code>" + str(file_uid) + "</code>\n"
                     "📁 File: <code>" + safe_html(fn) + "</code>",
                     parse_mode='HTML')


# ============================================================
# INLINE CALLBACK
# ============================================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data or ""
    uid = call.from_user.id

    # -------- FORCE JOIN VERIFY --------
    if data == "verify_join":
        if is_user_joined(uid):
            raw_answer_cb(call.id, "Verified!", alert=False)
            try:
                edit_inline(call.message.chat.id, call.message.message_id,
                            "✅ <b>Verified!</b> You can now use the bot.", [])
            except:
                pass
            send_welcome(call.message.chat.id, uid,
                         call.from_user.first_name, call.from_user.username)
        else:
            raw_answer_cb(call.id, "❌ You haven't joined yet. Please join first.", alert=True)
        return

    # -------- APPROVE / REJECT --------
    if data.startswith("approve_") or data.startswith("reject_"):
        if uid not in admin_ids:
            raw_answer_cb(call.id, "Admin only.", alert=True)
            return
        action, aid = data.split("_", 1)
        aid = aid.upper()

        if aid in handled_approvals:
            prev = handled_approvals[aid]
            raw_answer_cb(call.id, "🔒 Already " + prev + ". Cannot change.", alert=True)
            try:
                edit_inline(call.message.chat.id, call.message.message_id,
                            call.message.text + "\n\n🔒 <b>Already " + prev + ".</b>", [])
            except:
                pass
            return
        if aid not in pending_approvals:
            raw_answer_cb(call.id, "Already handled or not found.", alert=True)
            try:
                edit_inline(call.message.chat.id, call.message.message_id,
                            call.message.text + "\n\n🔒 <b>Already Handled</b>", [])
            except:
                pass
            return

        ap = pending_approvals[aid]
        file_uid = ap['user_id']
        fn = ap['file_name']
        ft = ap['file_type']

        if action == "approve":
            del pending_approvals[aid]
            handled_approvals[aid] = "approved"
            threading.Thread(target=process_approved_file, args=(ap,)).start()
            raw_answer_cb(call.id, "Approved! Processing...", alert=False)
            try:
                edit_inline(call.message.chat.id, call.message.message_id,
                            "✅ <b>APPROVED — PROCESSING</b>\n\n"
                            "👤 User: <code>" + str(file_uid) + "</code>\n"
                            "📁 File: <code>" + safe_html(fn) + "</code>\n\n"
                            "⚙️ Extracting + installing deps + starting...\n"
                            "🔒 <b>Approval locked.</b>",
                            [])
            except:
                pass
        elif action == "reject":
            del pending_approvals[aid]
            handled_approvals[aid] = "rejected"
            set_file_status(file_uid, fn, 'rejected')
            try:
                if os.path.exists(ap['src_path']):
                    os.remove(ap['src_path'])
            except Exception as e:
                logger.error(f"reject del: {e}")
            notify_user_rejected(file_uid, fn, reason="Admin rejected the file.")
            raw_answer_cb(call.id, "Rejected & Deleted!", alert=False)
            try:
                edit_inline(call.message.chat.id, call.message.message_id,
                            "❌ <b>REJECTED & DELETED</b>\n\n"
                            "👤 User: <code>" + str(file_uid) + "</code>\n"
                            "📁 File: <code>" + safe_html(fn) + "</code>\n\n"
                            "🔒 <b>Approval locked.</b> Cannot be changed.",
                            [])
            except:
                pass
        return

    # -------- DELETE ALL CODE --------
    if data == "confirm_delete_all":
        if uid != OWNER_ID and uid not in admin_ids:
            raw_answer_cb(call.id, "Owner/Admin only.", alert=True)
            return
        raw_answer_cb(call.id, "Deleting...", alert=False)
        try:
            edit_inline(call.message.chat.id, call.message.message_id,
                        "⏳ <b>Stopping all scripts and deleting all files...</b>", [])
        except:
            pass
        # 1) Stop all running scripts FIRST
        for k in list(bot_scripts.keys()):
            try:
                kill_process_tree(bot_scripts[k])
            except:
                pass
        bot_scripts.clear()
        # 2) Delete all files on disk (both folders)
        for root_dir in (UPLOAD_BOTS_DIR, PENDING_DIR):
            try:
                if os.path.isdir(root_dir):
                    for d in os.listdir(root_dir):
                        dp = os.path.join(root_dir, d)
                        if os.path.isdir(dp):
                            shutil.rmtree(dp, ignore_errors=True)
                        else:
                            try:
                                os.remove(dp)
                            except:
                                pass
            except Exception as e:
                logger.error(f"delete_all dir err ({root_dir}): {e}")
        # 3) Clear DB
        clear_all_user_files_db()
        pending_approvals.clear()
        handled_approvals.clear()
        try:
            edit_inline(call.message.chat.id, call.message.message_id,
                        "✅ <b>ALL CODE DELETED</b>\n\n"
                        "🚫 All running scripts stopped.\n"
                        "🗑️ All uploaded files deleted.\n"
                        "📂 Server is now clean.",
                        [])
        except:
            pass
        return

    if data == "cancel_delete_all":
        raw_answer_cb(call.id, "Cancelled.", alert=False)
        try:
            edit_inline(call.message.chat.id, call.message.message_id,
                        "❌ <b>Delete Cancelled</b>", [])
        except:
            pass
        return


# ============================================================
# BUTTON TEXT HANDLER
# ============================================================

@bot.message_handler(func=lambda m: m.text in [
    "📢 Updates Channel", "📤 Upload File", "📂 Check Files",
    "⚡ Bot Speed", "📞 Contact Owner", "📊 Statistics",
    "💳 Subscriptions", "📢 Broadcast", "🔒 Lock Bot",
    "🟢 Running All Code", "👑 Admin Panel", "➡️ Join", "🔙 Back",
    "🗑️ Delete All Code", "👥 Total Users", "📂 Total Files"
])
def handle_btn(m):
    global bot_locked
    t = m.text
    uid = m.from_user.id

    if uid in banned_users and uid not in admin_ids:
        raw_send(m.chat.id, "🚫 You are banned from this bot.")
        return

    if t == "➡️ Join":
        inline_rows = [[
            ibtn("➡️ Join Channel", None, "primary", url=FORCE_CHANNEL_LINK)
        ]]
        send_inline(m.chat.id,
                    "➡️ <b>Click below to join our channel:</b>",
                    inline_rows)
        return

    if not is_user_joined(uid):
        force_join_prompt(m.chat.id)
        return

    if t == "📢 Updates Channel":
        inline_rows = [[
            ibtn("📢 Open Channel", None, "primary", url=UPDATE_CHANNEL)
        ]]
        send_inline(m.chat.id, "📢 <b>Updates Channel</b>", inline_rows)
    elif t == "📤 Upload File":
        limit = get_user_file_limit(uid)
        curr = get_user_file_count(uid)
        if curr >= limit:
            lim_str = str(limit) if limit != float('inf') else "Unlimited"
            raw_send(m.chat.id,
                     "⚠️ Limit reached (" + str(curr) + "/" + lim_str + ").",
                     reply_keyboard=get_main_kb(uid))
            return
        raw_send(m.chat.id,
                 "📁 Send .py, .js or .zip file.\n"
                 "⚠️ It will be sent to <b>Admin Approval</b> first.\n"
                 "🔒 Nothing runs until approved.",
                 reply_keyboard=get_main_kb(uid))
    elif t == "📂 Check Files":
        files = user_files.get(uid, [])
        pending_here = [aid for aid, ap in pending_approvals.items() if ap['user_id'] == uid]
        if not files and not pending_here:
            raw_send(m.chat.id, "📁 No files.", reply_keyboard=get_main_kb(uid))
            return
        msg = "📁 <b>Your files:</b>\n\n"
        if pending_here:
            msg += "⏳ <b>Pending approval:</b>\n"
            for aid in pending_here:
                ap = pending_approvals[aid]
                msg += "  • <code>" + safe_html(ap['file_name']) + "</code> (ID " + aid + ")\n"
            msg += "\n"
        if files:
            msg += "✅ <b>Approved:</b>\n"
        kb = []
        for fn, ft in sorted(files):
            r = is_bot_running(uid, fn)
            icon = "🟢" if r else "🟡"
            msg += icon + " <code>" + safe_html(fn) + "</code> (" + ft + ")\n"
            kb.append([
                rbtn(("Stop " if r else "Start ") + fn,
                     "danger" if r else "success"),
                rbtn("Delete " + fn, "danger")
            ])
        kb.append([rbtn("🔙 Back", "primary")])
        raw_send(m.chat.id, msg, reply_keyboard=kb)
    elif t == "⚡ Bot Speed":
        t0 = time.time()
        wm = bot.reply_to(m, "⏳ Testing...", parse_mode='HTML')
        try:
            bot.send_chat_action(m.chat.id, 'typing')
            rt = round((time.time() - t0) * 1000, 2)
            st = "✅ Unlocked" if not bot_locked else "⚠️ Locked"
            if uid == OWNER_ID:
                lvl = "👑 Owner"
            elif uid in admin_ids:
                lvl = "📊 Admin"
            elif uid in user_subscriptions and user_subscriptions[uid].get('expiry', datetime.min) > datetime.now():
                lvl = "✅ Premium"
            else:
                lvl = "👤 Free"
            bot.edit_message_text(
                "⚡ Speed: " + str(rt) + " ms\n🚦 " + st + "\n👤 " + lvl,
                m.chat.id, wm.message_id, parse_mode='HTML')
        except:
            pass
    elif t == "📞 Contact Owner":
        contact_url = f"https://t.me/{YOUR_USERNAME.replace('@', '')}"
        inline_rows = [[
            ibtn("📞 Contact Owner", None, "primary", url=contact_url)
        ]]
        send_inline(m.chat.id,
                    "📞 <b>Contact Owner</b>\n\n"
                    "💬 Tap the button below to contact the owner.",
                    inline_rows)
        raw_send(m.chat.id, "✅ Main Menu", reply_keyboard=get_main_kb(uid))
    elif t == "📊 Statistics":
        total_users = len(active_users)
        total_files = sum(len(f) for f in user_files.values())
        running = 0
        mine = 0
        for k, v in list(bot_scripts.items()):
            owner, _ = k.split('_', 1)
            if is_bot_running(int(owner), v['file_name']):
                running += 1
                if int(owner) == uid:
                    mine += 1
        msg = (
            "📊 <b>Statistics:</b>\n\n"
            "👤 Users: " + str(total_users) + "\n"
            "📁 Approved Files: " + str(total_files) + "\n"
            "🟢 Running: " + str(running) + "\n"
            "⏳ Pending: " + str(len(pending_approvals)) + "\n"
            "🚫 Banned: " + str(len(banned_users)) + "\n"
        )
        if uid in admin_ids:
            msg += "🔒 Bot: " + ("Locked" if bot_locked else "Unlocked") + "\n🤖 Your Bots: " + str(mine)
        else:
            msg += "🤖 Your Bots: " + str(mine)
        raw_send(m.chat.id, msg, reply_keyboard=get_main_kb(uid))
    elif t == "💳 Subscriptions":
        if uid not in admin_ids:
            raw_send(m.chat.id, "❌ Admin only.", reply_keyboard=get_main_kb(uid))
            return
        raw_send(m.chat.id,
                 "💸 <b>Subscription Management</b>\n\n"
                 "Send: <code>addsub ID DAYS</code>\n"
                 "Send: <code>removesub ID</code>\n"
                 "Send: <code>checksub ID</code>",
                 reply_keyboard=get_main_kb(uid))
    elif t == "📢 Broadcast":
        if uid not in admin_ids:
            raw_send(m.chat.id, "❌ Admin only.", reply_keyboard=get_main_kb(uid))
            return
        msg = bot.reply_to(m, "📢 Send broadcast message.\n/cancel to abort.", parse_mode='HTML')
        bot.register_next_step_handler(msg, process_broadcast)
    elif t == "🔒 Lock Bot":
        if uid not in admin_ids:
            raw_send(m.chat.id, "❌ Admin only.", reply_keyboard=get_main_kb(uid))
            return
        bot_locked = not bot_locked
        raw_send(m.chat.id,
                 "🔒 Bot locked." if bot_locked else "🔓 Bot unlocked.",
                 reply_keyboard=get_main_kb(uid))
    elif t == "🟢 Running All Code":
        if uid not in admin_ids:
            raw_send(m.chat.id, "❌ Admin only.", reply_keyboard=get_main_kb(uid))
            return
        run_all_scripts(m)
    elif t == "👑 Admin Panel":
        if uid not in admin_ids:
            raw_send(m.chat.id, "❌ Admin only.", reply_keyboard=get_main_kb(uid))
            return
        raw_send(m.chat.id,
                 "👑 <b>Admin Panel</b>\n\n"
                 "Send: <code>addadmin ID</code>\n"
                 "Send: <code>removeadmin ID</code>\n"
                 "Send: <code>listadmins</code>\n"
                 "Send: <code>/ban ID</code>\n"
                 "Send: <code>/unban ID</code>\n\n"
                 "⚠️ <b>Delete All Code</b> — stops all scripts and deletes all files.",
                 reply_keyboard=get_admin_kb())
    elif t == "🗑️ Delete All Code":
        if uid != OWNER_ID and uid not in admin_ids:
            raw_send(m.chat.id, "❌ Owner/Admin only.", reply_keyboard=get_main_kb(uid))
            return
        inline_rows = [[
            ibtn("✅ Yes, Delete Everything", "confirm_delete_all", "danger"),
            ibtn("❌ Cancel", "cancel_delete_all", "primary"),
        ]]
        send_inline(m.chat.id,
                    "⚠️ <b>Delete All Code — Confirm?</b>\n\n"
                    "This will:\n"
                    "• Stop all running scripts\n"
                    "• Delete all uploaded files (approved + pending)\n"
                    "• Clear server storage\n\n"
                    "❓ Are you sure?",
                    inline_rows)
    elif t == "👥 Total Users":
        if uid not in admin_ids:
            raw_send(m.chat.id, "❌ Admin only.", reply_keyboard=get_main_kb(uid))
            return
        raw_send(m.chat.id,
                 "👥 <b>Total Users:</b> " + str(len(active_users)),
                 reply_keyboard=get_admin_kb())
    elif t == "📂 Total Files":
        if uid not in admin_ids:
            raw_send(m.chat.id, "❌ Admin only.", reply_keyboard=get_main_kb(uid))
            return
        raw_send(m.chat.id,
                 "📂 <b>Total Approved Files:</b> " + str(sum(len(f) for f in user_files.values())),
                 reply_keyboard=get_admin_kb())
    elif t == "🔙 Back":
        raw_send(m.chat.id, "✅ Main Menu", reply_keyboard=get_main_kb(uid))


# ============================================================
# FILE COMMANDS (Start / Stop / Delete)
# ============================================================

@bot.message_handler(func=lambda m: m.text and (
    m.text.startswith("Start ") or m.text.startswith("Stop ") or
    m.text.startswith("Delete ")
))
def handle_file_cmd(m):
    uid = m.from_user.id
    parts = m.text.split(maxsplit=1)
    if len(parts) != 2:
        return
    action = parts[0].lower()
    fn = parts[1].strip()

    owner = None
    for o, files in user_files.items():
        if any(f[0] == fn for f in files):
            owner = o
            break
    if owner is None:
        raw_send(m.chat.id, "❌ File not found (or not approved yet).", reply_keyboard=get_main_kb(uid))
        return
    if not (uid == owner or uid in admin_ids):
        raw_send(m.chat.id, "❌ Not yours.", reply_keyboard=get_main_kb(uid))
        return

    folder = get_user_folder(owner)
    fp = os.path.join(folder, fn)
    ft = next((f[1] for f in user_files.get(owner, []) if f[0] == fn), 'py')
    key = f"{owner}_{fn}"

    if action == "start":
        if is_bot_running(owner, fn):
            raw_send(m.chat.id,
                     "⚠️ <b>Already running!</b>\n\n"
                     "File <code>" + safe_html(fn) + "</code> is already running.\n"
                     "Stop it first or use a different filename.",
                     reply_keyboard=get_main_kb(uid))
            return
        if not is_file_approved(owner, fn):
            raw_send(m.chat.id,
                     "🔒 <b>Not approved yet!</b>\n\n"
                     "This file is still awaiting admin approval. You cannot run it now.",
                     reply_keyboard=get_main_kb(uid))
            return
        if not os.path.exists(fp):
            raw_send(m.chat.id, "❌ Missing file.", reply_keyboard=get_main_kb(uid))
            remove_user_file_db(owner, fn)
            return
        if ft == 'py':
            threading.Thread(target=run_script, args=(fp, owner, folder, fn, m, 1, True)).start()
        elif ft == 'js':
            threading.Thread(target=run_js_script, args=(fp, owner, folder, fn, m, 1, True)).start()
    elif action == "stop":
        if not is_bot_running(owner, fn):
            raw_send(m.chat.id, "⚠️ Already stopped.", reply_keyboard=get_main_kb(uid))
            return
        pi = bot_scripts.get(key)
        if pi:
            kill_process_tree(pi)
        bot_scripts.pop(key, None)
        raw_send(m.chat.id, "✅ Stopped " + safe_html(fn) + ".", reply_keyboard=get_main_kb(uid))
    elif action == "delete":
        if is_bot_running(owner, fn):
            pi = bot_scripts.get(key)
            if pi:
                kill_process_tree(pi)
            bot_scripts.pop(key, None)
        if os.path.exists(fp):
            os.remove(fp)
        lp = os.path.join(folder, f"{os.path.splitext(fn)[0]}.log")
        if os.path.exists(lp):
            os.remove(lp)
        remove_user_file_db(owner, fn)
        raw_send(m.chat.id, "✅ Deleted " + safe_html(fn) + ".", reply_keyboard=get_main_kb(uid))


# ============================================================
# ADMIN COMMANDS
# ============================================================

@bot.message_handler(func=lambda m: m.text and (
    m.text.startswith("addadmin ") or m.text.startswith("removeadmin ") or
    m.text.lower() == "listadmins" or
    m.text.startswith("addsub ") or m.text.startswith("removesub ") or
    m.text.startswith("checksub ")
))
def handle_admin_cmd(m):
    uid = m.from_user.id
    if uid not in admin_ids:
        raw_send(m.chat.id, "❌ Admin only.", reply_keyboard=get_main_kb(uid))
        return
    txt = m.text.strip()
    low = txt.lower()

    if low == "listadmins":
        lst = "\n".join(
            "- <code>" + str(a) + "</code> " + ("(Owner)" if a == OWNER_ID else "") for a in sorted(admin_ids))
        if not lst:
            lst = "(None)"
        raw_send(m.chat.id, "👑 <b>Admins:</b>\n\n" + lst, reply_keyboard=get_main_kb(uid))
        return

    if low.startswith("addadmin "):
        if uid != OWNER_ID:
            raw_send(m.chat.id, "❌ Owner only.", reply_keyboard=get_main_kb(uid))
            return
        try:
            nid = int(txt.split()[1])
            if nid == OWNER_ID or nid in admin_ids:
                raw_send(m.chat.id, "⚠️ Already admin.", reply_keyboard=get_main_kb(uid))
                return
            add_admin_db(nid)
            raw_send(m.chat.id, "✅ Added admin <code>" + str(nid) + "</code>.",
                     reply_keyboard=get_main_kb(uid))
            try:
                bot.send_message(nid, "👑 You are now Admin!", parse_mode='HTML')
            except:
                pass
        except:
            raw_send(m.chat.id, "❌ Usage: addadmin ID", reply_keyboard=get_main_kb(uid))
        return

    if low.startswith("removeadmin "):
        if uid != OWNER_ID:
            raw_send(m.chat.id, "❌ Owner only.", reply_keyboard=get_main_kb(uid))
            return
        try:
            rid = int(txt.split()[1])
            if remove_admin_db(rid):
                raw_send(m.chat.id, "✅ Removed admin <code>" + str(rid) + "</code>.",
                         reply_keyboard=get_main_kb(uid))
            else:
                raw_send(m.chat.id, "⚠️ Not admin.", reply_keyboard=get_main_kb(uid))
        except:
            raw_send(m.chat.id, "❌ Usage: removeadmin ID", reply_keyboard=get_main_kb(uid))
        return

    if low.startswith("addsub "):
        try:
            parts = txt.split()
            sid = int(parts[1])
            days = int(parts[2])
            if sid <= 0 or days <= 0:
                raise ValueError()
            ce = user_subscriptions.get(sid, {}).get('expiry')
            start = datetime.now()
            if ce and ce > start:
                start = ce
            ne = start + timedelta(days=days)
            save_subscription(sid, ne)
            raw_send(m.chat.id,
                     "✅ Sub added. Expiry: " + str(ne.strftime("%Y-%m-%d")),
                     reply_keyboard=get_main_kb(uid))
            try:
                bot.send_message(sid,
                                 "🎁 Sub active! Expires: " + str(ne.strftime("%Y-%m-%d")) + ".",
                                 parse_mode='HTML')
            except:
                pass
        except:
            raw_send(m.chat.id, "❌ Usage: addsub ID DAYS", reply_keyboard=get_main_kb(uid))
        return

    if low.startswith("removesub "):
        try:
            sid = int(txt.split()[1])
            remove_subscription_db(sid)
            raw_send(m.chat.id, "✅ Sub removed.", reply_keyboard=get_main_kb(uid))
            try:
                bot.send_message(sid, "❌ Subscription removed.", parse_mode='HTML')
            except:
                pass
        except:
            raw_send(m.chat.id, "❌ Usage: removesub ID", reply_keyboard=get_main_kb(uid))
        return

    if low.startswith("checksub "):
        try:
            sid = int(txt.split()[1])
            if sid in user_subscriptions:
                e = user_subscriptions[sid].get('expiry')
                if e:
                    if e > datetime.now():
                        dl = (e - datetime.now()).days
                        raw_send(m.chat.id,
                                 "✅ <code>" + str(sid) + "</code> active. Expires: " + str(e.strftime("%Y-%m-%d")) + " (" + str(dl) + " days)",
                                 reply_keyboard=get_main_kb(uid))
                    else:
                        raw_send(m.chat.id,
                                 "⚠️ <code>" + str(sid) + "</code> expired.",
                                 reply_keyboard=get_main_kb(uid))
                        remove_subscription_db(sid)
                else:
                    raw_send(m.chat.id, "⚠️ Expiry missing.", reply_keyboard=get_main_kb(uid))
            else:
                raw_send(m.chat.id, "👤 No sub.", reply_keyboard=get_main_kb(uid))
        except:
            raw_send(m.chat.id, "❌ Usage: checksub ID", reply_keyboard=get_main_kb(uid))
        return


# ============================================================
# DOCUMENT HANDLER (all files → pending → approval)
# ============================================================

@bot.message_handler(content_types=['document'])
def handle_doc(m):
    handle_doc_new(m)


# ============================================================
# BROADCAST
# ============================================================

def process_broadcast(m):
    uid = m.from_user.id
    if uid not in admin_ids:
        raw_send(m.chat.id, "❌ Admin only.", reply_keyboard=get_main_kb(uid))
        return
    if m.text and m.text.lower() == '/cancel':
        raw_send(m.chat.id, "Cancelled.", reply_keyboard=get_main_kb(uid))
        return
    content = m.text
    if not content:
        raw_send(m.chat.id, "❌ Empty.", reply_keyboard=get_main_kb(uid))
        return
    raw_send(m.chat.id,
             "⏳ Broadcasting to " + str(len(active_users)) + " users...",
             reply_keyboard=get_main_kb(uid))
    threading.Thread(target=do_broadcast, args=(content, m.chat.id)).start()


def do_broadcast(text, chat_id):
    sent = 0
    failed = 0
    blocked = 0
    users = list(active_users)
    total = len(users)
    for i, u in enumerate(users):
        try:
            bot.send_message(u, "🔔 " + text, parse_mode='HTML')
            sent += 1
        except telebot.apihelper.ApiTelegramException as e:
            ed = str(e).lower()
            if any(s in ed for s in
                   ["blocked", "deactivated", "chat not found", "kicked", "restricted"]):
                blocked += 1
            else:
                failed += 1
        except:
            failed += 1
        if (i + 1) % 25 == 0:
            time.sleep(1.5)
        elif i % 5 == 0:
            time.sleep(0.2)
    raw_send(chat_id,
             "✅ <b>Broadcast Complete!</b>\n\n"
             "✅ Sent: " + str(sent) + "\n❌ Failed: " + str(failed) + "\n🚫 Blocked: " + str(blocked) + "\n👥 Total: " + str(total))


# ============================================================
# RUN ALL SCRIPTS (only approved files)
# ============================================================

def run_all_scripts(m):
    uid = m.from_user.id
    chat_id = m.chat.id
    if uid not in admin_ids:
        return
    raw_send(chat_id, "⏳ Starting all approved scripts...", reply_keyboard=get_main_kb(uid))
    threading.Thread(target=run_all_scripts_bg, args=(chat_id,)).start()


def run_all_scripts_bg(chat_id):
    started = 0
    users_count = 0
    skipped = 0
    snapshot = dict(user_files)
    for tuid, files in snapshot.items():
        if not files:
            continue
        users_count += 1
        uf = get_user_folder(tuid)
        for fn, ft in files:
            if not is_file_approved(tuid, fn):
                skipped += 1
                continue
            if not is_bot_running(tuid, fn):
                fp = os.path.join(uf, fn)
                if os.path.exists(fp):
                    try:
                        if ft == 'py':
                            threading.Thread(target=run_script,
                                             args=(fp, tuid, uf, fn, _FakeMsg(chat_id), 1, True)).start()
                            started += 1
                        elif ft == 'js':
                            threading.Thread(target=run_js_script,
                                             args=(fp, tuid, uf, fn, _FakeMsg(chat_id), 1, True)).start()
                            started += 1
                        time.sleep(0.7)
                    except:
                        skipped += 1
                else:
                    skipped += 1
    msg = "✅ Started: " + str(started) + "\n👥 Users: " + str(users_count) + "\n"
    if skipped:
        msg += "⚠️ Skipped: " + str(skipped) + "\n"
    raw_send(chat_id, msg)


class _FakeMsg:
    def __init__(self, chat_id):
        self.chat = type('C', (), {'id': chat_id})()


# ============================================================
# CLEANUP
# ============================================================

def cleanup():
    logger.warning("Shutting down...")
    for k in list(bot_scripts.keys()):
        if k in bot_scripts:
            kill_process_tree(bot_scripts[k])


atexit.register(cleanup)


# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    logger.info("=" * 40 +
                "\n🚀 EMPEROR HOSTING Starting...\n"
                "🔑 Owner: " + str(OWNER_ID) + "\n"
                "🛡️ Admins: " + str(admin_ids) + "\n" +
                "=" * 40)
    keep_alive()
    logger.info("🚀 Polling started...")
    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except requests.exceptions.ReadTimeout:
            logger.warning("ReadTimeout. 5s...")
            time.sleep(5)
        except requests.exceptions.ConnectionError as ce:
            logger.error(f"ConnError: {ce}. 15s...")
            time.sleep(15)
        except Exception as e:
            logger.critical(f"💥 Polling error: {e}", exc_info=True)
            time.sleep(30)
        finally:
            time.sleep(1)