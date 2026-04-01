# -*- coding: utf-8 -*-
import os
import re
import sqlite3
import json
import threading
import time
import random
from datetime import datetime

import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telebot.apihelper import ApiTelegramException

import phonenumbers
# from phonenumbers import geocoder as ph_geocoder # Heavy memory usage!

# ================================================================
# Monkey-patch buttons to support Telegram's new 'style' field
# ================================================================
_orig_inline_init = InlineKeyboardButton.__init__
def _new_inline_init(self, *args, **kwargs):
    self._style = kwargs.pop('style', None)
    self._api_kwargs = kwargs.pop('api_kwargs', {})
    _orig_inline_init(self, *args, **kwargs)
InlineKeyboardButton.__init__ = _new_inline_init

_orig_inline_to_dict = InlineKeyboardButton.to_dict
def _new_inline_to_dict(self):
    d = _orig_inline_to_dict(self)
    if hasattr(self, '_style') and self._style:
        d['style'] = self._style
    if hasattr(self, '_api_kwargs') and self._api_kwargs:
        d.update(self._api_kwargs)
    return d
InlineKeyboardButton.to_dict = _new_inline_to_dict

_orig_kb_init = KeyboardButton.__init__
def _new_kb_init(self, *args, **kwargs):
    self._style = kwargs.pop('style', None)
    self._api_kwargs = kwargs.pop('api_kwargs', {})
    _orig_kb_init(self, *args, **kwargs)
KeyboardButton.__init__ = _new_kb_init

_orig_kb_to_dict = KeyboardButton.to_dict
def _new_kb_to_dict(self):
    d = _orig_kb_to_dict(self)
    if hasattr(self, '_style') and self._style:
        d['style'] = self._style
    if hasattr(self, '_api_kwargs') and self._api_kwargs:
        d.update(self._api_kwargs)
    return d
KeyboardButton.to_dict = _new_kb_to_dict

def KBtn(text, style=None):
    return KeyboardButton(text, style=style)



# ================================================================
# ===================== CONFIG (DYNAMIC) ========================
# ================================================================
BOT_TOKEN = "8747923164:AAG2MOPOhpfVTO9JKUPdgESlvjwMBQIYD5o"  # REQUIRED

# Hardcoded defaults for initial seed
DEFAULT_CONFIGS = {
    "owner_username": "Tanvir_Rolex_MT",
    "channel_1_id": "0",
    "channel_2_id": "0",
    "ref_amount": "0.005",
    "min_withdraw": "0.5",
    "otp_group_link": "",
}

# These will be updated from DB by refresh_configs()
OWNER_USERNAME = DEFAULT_CONFIGS["owner_username"]
CHANNEL_1_ID = int(DEFAULT_CONFIGS["channel_1_id"])
CHANNEL_2_ID = int(DEFAULT_CONFIGS["channel_2_id"])
REF_AMOUNT = float(DEFAULT_CONFIGS["ref_amount"])
MIN_WITHDRAW = float(DEFAULT_CONFIGS["min_withdraw"])
OTP_GROUP_LINK = DEFAULT_CONFIGS["otp_group_link"]
FIXED_OTP_GROUP_ID = -1003780995114  # Set to requested group ID


def get_config(key: str) -> str:
    with db_lock:
        cur.execute("SELECT val FROM configs WHERE key = ?", (key,))
        row = cur.fetchone()
        if row:
            return row[0]
        return DEFAULT_CONFIGS.get(key, "")

def set_config(key: str, val: str):
    with db_lock:
        cur.execute("INSERT OR REPLACE INTO configs (key, val) VALUES (?, ?)", (key, str(val)))
        conn.commit()
    refresh_configs()

def refresh_configs():
    global OWNER_USERNAME, CHANNEL_1_ID, CHANNEL_2_ID, REF_AMOUNT, MIN_WITHDRAW, OTP_GROUP_LINK
    OWNER_USERNAME = get_config("owner_username")
    CHANNEL_1_ID = int(get_config("channel_1_id"))
    CHANNEL_2_ID = int(get_config("channel_2_id"))
    REF_AMOUNT = float(get_config("ref_amount"))
    MIN_WITHDRAW = float(get_config("min_withdraw"))
    OTP_GROUP_LINK = get_config("otp_group_link")

# Owners/admin seeds
FIXED_OWNER_ID = 8042824468
SECOND_OWNER_ID = 7524675360
EXTRA_OWNER_IDS = [SECOND_OWNER_ID]

EXTRA_OTP_GROUP_IDS = []

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "number_bot.db")
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
os.makedirs(USER_DATA_DIR, exist_ok=True)
# ================================================================


def normalize_bot_token(t: str) -> str:
    if not t:
        return ""
    t = t.strip()

    if "api.telegram.org" in t and "/bot" in t:
        try:
            t = t.split("/bot", 1)[1]
            t = t.split("/", 1)[0].strip()
        except Exception:
            pass

    if t.lower().startswith("bot") and ":" in t[3:]:
        t = t[3:].strip()

    if ":" not in t:
        raise RuntimeError(
            "BOT_TOKEN invalid format. Paste ONLY like: 123456789:AAHxxxxxx "
            "(no URL, no 'bot' prefix)."
        )
    return t


def mask_token(t: str) -> str:
    t = t or ""
    if len(t) <= 12:
        return t
    return t[:6] + "..." + t[-6:]


BOT_TOKEN = normalize_bot_token(BOT_TOKEN)

if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN in CONFIG section.")
if CHANNEL_1_ID is None or CHANNEL_2_ID is None:
    raise RuntimeError("Set CHANNEL_1_ID and CHANNEL_2_ID in CONFIG section.")

OWNER_IDS = {int(FIXED_OWNER_ID)} | set(int(x) for x in EXTRA_OWNER_IDS if x)
OTP_INPUT_GROUP_IDS = {int(FIXED_OTP_GROUP_ID)} | set(
    int(x) for x in EXTRA_OTP_GROUP_IDS if x
)

# ----------------- Bot init -----------------
# Turbo-optimized threading (20 threads)
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", num_threads=20)
BOT_USERNAME = None

# ----------------- DB init -----------------
conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=20)
cur = conn.cursor()

db_lock = threading.RLock()
membership_cache: dict[int, tuple[float, bool]] = {}


def init_db():
    with db_lock:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=OFF") # Maximum speed
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.execute("PRAGMA cache_size=-10000")  # Balancing RAM and speed (10MB)

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                balance REAL DEFAULT 0,
                referred_by INTEGER,
                joined_at TEXT,
                is_verified INTEGER DEFAULT 0
            )
        """
        )
        
        # Ensure column exists if table was already created
        try:
            cur.execute("ALTER TABLE users ADD COLUMN is_verified INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                telegram_id INTEGER PRIMARY KEY,
                role TEXT
            )
        """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                emoji TEXT
            )
        """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                country TEXT,
                flag TEXT,
                type_desc TEXT,
                short_name TEXT,
                per_user INTEGER,
                rate REAL DEFAULT 0,
                created_at TEXT,
                is_deleted INTEGER DEFAULT 0,
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER,
                value TEXT,
                is_used INTEGER DEFAULT 0,
                used_by INTEGER,
                used_at TEXT,
                is_current INTEGER DEFAULT 0,
                FOREIGN KEY (batch_id) REFERENCES batches (id)
            )
        """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS withdraw_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                details TEXT,
                created_at TEXT,
                status TEXT
            )
        """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS otps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT,
                user_tg_id INTEGER,
                content TEXT,
                created_at TEXT
            )
        """
        )

        # ensure columns
        cur.execute("PRAGMA table_info(batches)")
        cols = [row[1] for row in cur.fetchall()]
        if "category_id" not in cols:
            cur.execute("ALTER TABLE batches ADD COLUMN category_id INTEGER")
            conn.commit()

        if "rate" not in cols:
            cur.execute("ALTER TABLE batches ADD COLUMN rate REAL DEFAULT 0")
            conn.commit()

        cur.execute("PRAGMA table_info(withdraw_requests)")
        cols = [row[1] for row in cur.fetchall()]
        if "amount" not in cols:
            cur.execute("ALTER TABLE withdraw_requests ADD COLUMN amount REAL")
            conn.commit()

        cur.execute("PRAGMA table_info(numbers)")
        cols = [row[1] for row in cur.fetchall()]
        if "is_current" not in cols:
            cur.execute(
                "ALTER TABLE numbers ADD COLUMN is_current INTEGER DEFAULT 0"
            )
            conn.commit()

        # indexes
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_numbers_batch_used ON numbers(batch_id, is_used)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_batches_is_deleted ON batches(is_deleted)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_otps_user ON otps(user_tg_id)"
        )

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_numbers_used_by ON numbers(used_by)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_numbers_batch_current ON numbers(batch_id, is_current)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_numbers_batch_used ON numbers(batch_id, is_used)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS configs (
                key TEXT PRIMARY KEY,
                val TEXT
            )
        """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS required_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                button_text TEXT,
                invite_link TEXT
            )
        """
        )

        conn.commit()

        # No seeds by default as per user request


        for oid in OWNER_IDS:
            cur.execute(
                "INSERT OR IGNORE INTO admins (telegram_id, role) VALUES (?, 'owner')",
                (oid,),
            )
        conn.commit()
        refresh_configs()


init_db()


# ----------------- State management -----------------
user_states: dict[int, dict] = {}


def set_state(user_id: int, state_name: str, data=None):
    user_states[user_id] = {"state": state_name, "data": data or {}}


def get_state(user_id: int):
    return user_states.get(user_id, {"state": None, "data": {}})


def clear_state(user_id: int):
    user_states.pop(user_id, None)


def cleanup_caches():
    """Clear memory-heavy caches if they exceed size limits."""
    global membership_cache, user_states
    
    # Aggressive cleanup for low memory footprint
    if len(membership_cache) > 1000:
        membership_cache.clear()
        
    if len(user_states) > 500:
        user_states.clear()
        
    # Also clean up snapshot cache if accessible via attribute
    last_snap = getattr(snapshot_user_to_file, "_last_snap", {})
    if len(last_snap) > 1000:
        last_snap.clear()
        # Re-assign empty dict or just clear content? Clearing content is safer.
        # But here we cleared via .clear(), so it's fine.
        
    import gc
    gc.collect()


# ----------------- User snapshot to file -----------------
def snapshot_user_to_file(tg_id: int):
    # Limit snapshots to once every 5 minutes per user to save I/O and CPU
    now_ts = time.time()
    
    # Randomly clean caches (1% chance) during activity
    if random.random() < 0.01:
        cleanup_caches()

    last_snap = getattr(snapshot_user_to_file, "_last_snap", {})
    
    # Periodic cleanup check (1% chance) to keep memory low
    if len(last_snap) > 1000:
        last_snap.clear()

    if tg_id in last_snap and (now_ts - last_snap[tg_id]) < 300:
        return
    
    with db_lock:
        cur.execute(
            "SELECT id, telegram_id, balance, referred_by, joined_at FROM users WHERE telegram_id = ?",
            (tg_id,),
        )
        row = cur.fetchone()
        if not row:
            return
        user_id, telegram_id, balance, referred_by, joined_at = row

        cur.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by = ?", (telegram_id,)
        )
        rc = cur.fetchone()
        ref_count = int(rc[0]) if rc and rc[0] is not None else 0
        ref_earned = float(ref_count) * float(REF_AMOUNT)

    data = {
        "user_id": user_id,
        "telegram_id": telegram_id,
        "balance": float(balance or 0.0),
        "referred_by": referred_by,
        "joined_at": joined_at,
        "referral_count": ref_count,
        "referral_earned": ref_earned,
    }
    path = os.path.join(USER_DATA_DIR, f"{telegram_id}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        last_snap[tg_id] = now_ts
        snapshot_user_to_file._last_snap = last_snap
    except Exception:
        pass


# ----------------- Helpers -----------------
def is_admin(user_id: int) -> bool:
    with db_lock:
        cur.execute("SELECT 1 FROM admins WHERE telegram_id = ?", (user_id,))
        return cur.fetchone() is not None


def is_owner(user_id: int) -> bool:
    with db_lock:
        cur.execute(
            "SELECT 1 FROM admins WHERE telegram_id = ? AND role = 'owner'",
            (user_id,),
        )
        return cur.fetchone() is not None


def get_or_create_user(tg_id: int, ref_id: int | None):
    with db_lock:
        cur.execute("SELECT id FROM users WHERE telegram_id = ?", (tg_id,))
        row = cur.fetchone()
        if row:
            user_id = row[0]
            snapshot_user_to_file(tg_id)
            return user_id

        now = datetime.utcnow().isoformat()
        cur.execute(
            "INSERT INTO users (telegram_id, referred_by, joined_at) VALUES (?,?,?)",
            (tg_id, ref_id if ref_id and ref_id != tg_id else None, now),
        )
        conn.commit()
        user_id = cur.lastrowid

        snapshot_user_to_file(tg_id)

        if ref_id and ref_id != tg_id:
            cur.execute(
                "UPDATE users SET balance = balance + ? WHERE telegram_id = ?",
                (REF_AMOUNT, ref_id),
            )
            conn.commit()
            snapshot_user_to_file(ref_id)

        return user_id


def get_user_balance(tg_id: int) -> float:
    with db_lock:
        cur.execute(
            "SELECT balance FROM users WHERE telegram_id = ?", (tg_id,)
        )
        row = cur.fetchone()
        return float(row[0]) if row else 0.0


def get_ref_stats(user_tg_id: int) -> tuple[int, float]:
    with db_lock:
        cur.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by = ?",
            (user_tg_id,),
        )
        row = cur.fetchone()
    ref_count = int(row[0]) if row and row[0] is not None else 0
    ref_earned = float(ref_count) * float(REF_AMOUNT)
    return ref_count, ref_earned


def normalize_number(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    for ch in [" ", "-", "(", ")", "\t"]:
        text = text.replace(ch, "")
    if not text:
        return None
    if not text.startswith("+"):
        text = "+" + text
    if not re.fullmatch(r"\+\d{7,15}", text):
        return None
    return text


def extract_otp_from_text(text: str) -> str | None:
    m = re.search(
        r"(OTP|code|password|passcode|verification)[^\d]{0,15}([\d\-\s]{3,16})",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        raw = m.group(2)
        digits = re.sub(r"\D", "", raw)
        if 3 <= len(digits) <= 8:
            return digits

    candidates = re.findall(r"(?<!\+)(?<!\d)\d{4,8}(?!\d)", text)
    if candidates:
        return candidates[-1]

    return None


def region_to_flag(region_code: str) -> str:
    if not region_code or len(region_code) != 2:
        return ""
    region_code = region_code.upper()
    return "".join(chr(ord("🇦") + ord(c) - ord("A")) for c in region_code)



COUNTRY_NAMES = {
    "AF": "Afghanistan", "AL": "Albania", "DZ": "Algeria", "AS": "American Samoa", "AD": "Andorra",
    "AO": "Angola", "AI": "Anguilla", "AQ": "Antarctica", "AG": "Antigua and Barbuda", "AR": "Argentina",
    "AM": "Armenia", "AW": "Aruba", "AU": "Australia", "AT": "Austria", "AZ": "Azerbaijan",
    "BS": "Bahamas", "BH": "Bahrain", "BD": "Bangladesh", "BB": "Barbados", "BY": "Belarus",
    "BE": "Belgium", "BZ": "Belize", "BJ": "Benin", "BM": "Bermuda", "BT": "Bhutan",
    "BO": "Bolivia", "BA": "Bosnia / Herzegovina", "BW": "Botswana", "BV": "Bouvet Island", "BR": "Brazil",
    "IO": "British Indian Ocean Territory", "BN": "Brunei Darussalam", "BG": "Bulgaria", "BF": "Burkina Faso", "BI": "Burundi",
    "KH": "Cambodia", "CM": "Cameroon", "CA": "Canada", "CV": "Cape Verde", "KY": "Cayman Island",
    "CF": "Central African Republic", "TD": "Chad", "CL": "Chile", "CN": "China", "CX": "Christmas Island",
    "CC": "Cocos (Keeling) Islands", "CO": "Colombia", "KM": "Comoros", "CG": "Congo", "CD": "Congo (Democratic Republic)",
    "CK": "Cook Islands", "CR": "Costa Rica", "CI": "Cote D'Ivoire", "HR": "Croatia", "CU": "Cuba",
    "CY": "Cyprus", "CZ": "Czech Republic", "DK": "Denmark", "DJ": "Djibouti", "DM": "Dominica",
    "DO": "Dominican Republic", "EC": "Ecuador", "EG": "Egypt", "SV": "El Salvador", "GQ": "Equatorial Guinea",
    "ER": "Eritrea", "EE": "Estonia", "ET": "Ethiopia", "FK": "Falkland Islands", "FO": "Faroe Islands",
    "FJ": "Fiji", "FI": "Finland", "FR": "France", "GF": "French Guiana", "PF": "French Polynesia",
    "TF": "French Southern Territories", "GA": "Gabon", "GM": "Gambia", "GE": "Georgia", "DE": "Germany",
    "GH": "Ghana", "GI": "Gibraltar", "GR": "Greece", "GL": "Greenland", "GD": "Grenada",
    "GP": "Guadeloupe", "GU": "Guam", "GT": "Guatemala", "GN": "Guinea", "GW": "Guinea-Bissau",
    "GY": "Guyana", "HT": "Haiti", "HM": "Heard/McDonald Islands", "VA": "Holy See (Vatican City)", "HN": "Honduras",
    "HK": "Hong Kong", "HU": "Hungary", "IS": "Iceland", "IN": "India", "ID": "Indonesia",
    "IR": "Iran", "IQ": "Iraq", "IE": "Ireland", "IL": "Israel", "IT": "Italy",
    "JM": "Jamaica", "JP": "Japan", "JO": "Jordan", "KZ": "Kazakhstan", "KE": "Kenya",
    "KI": "Kiribati", "KP": "Korea (North)", "KR": "Korea (South)", "KW": "Kuwait", "KG": "Kyrgyzstan",
    "LA": "Laos", "LV": "Latvia", "LB": "Lebanon", "LS": "Lesotho", "LR": "Liberia",
    "LY": "Libya", "LI": "Liechtenstein", "LT": "Lithuania", "LU": "Luxembourg", "MO": "Macau",
    "MK": "Macedonia", "MG": "Madagascar", "MW": "Malawi", "MY": "Malaysia", "MV": "Maldives",
    "ML": "Mali", "MT": "Malta", "MH": "Marshall Islands", "MQ": "Martinique", "MR": "Mauritania",
    "MU": "Mauritius", "YT": "Mayotte", "MX": "Mexico", "FM": "Micronesia", "MD": "Moldova",
    "MC": "Monaco", "MN": "Mongolia", "MS": "Montserrat", "MA": "Morocco", "MZ": "Mozambique",
    "MM": "Myanmar", "NA": "Namibia", "NR": "Nauru", "NP": "Nepal", "NL": "Netherlands",
    "AN": "Netherlands Antilles", "NC": "New Caledonia", "NZ": "New Zealand", "NI": "Nicaragua", "NE": "Niger",
    "NG": "Nigeria", "NU": "Niue", "NF": "Norfolk Island", "MP": "Northern Mariana Islands", "NO": "Norway",
    "OM": "Oman", "PK": "Pakistan", "PW": "Palau", "PS": "Palestinian Territory", "PA": "Panama",
    "PG": "Papua New Guinea", "PY": "Paraguay", "PE": "Peru", "PH": "Philippines", "PN": "Pitcairn",
    "PL": "Poland", "PT": "Portugal", "PR": "Puerto Rico", "QA": "Qatar", "RE": "Reunion",
    "RO": "Romania", "RU": "Russian Federation", "RW": "Rwanda", "SH": "Saint Helena", "KN": "Saint Kitts and Nevis",
    "LC": "Saint Lucia", "PM": "Saint Pierre and Miquelon", "VC": "Saint Vincent and Grenadines", "WS": "Samoa",
    "SM": "San Marino", "ST": "Sao Tome and Principe", "SA": "Saudi Arabia", "SN": "Senegal", "CS": "Serbia and Montenegro",
    "SC": "Seychelles", "SL": "Sierra Leone", "SG": "Singapore", "SK": "Slovakia", "SI": "Slovenia",
    "SB": "Solomon Islands", "SO": "Somalia", "ZA": "South Africa", "GS": "South Georgia/Sandwich Islands", "ES": "Spain",
    "LK": "Sri Lanka", "SD": "Sudan", "SR": "Suriname", "SJ": "Svalbard and Jan Mayen", "SZ": "Swaziland",
    "SE": "Sweden", "CH": "Switzerland", "SY": "Syrian Arab Republic", "TW": "Taiwan", "TJ": "Tajikistan",
    "TZ": "Tanzania", "TH": "Thailand", "TL": "Timor-Leste", "TG": "Togo", "TK": "Tokelau",
    "TO": "Tonga", "TT": "Trinidad and Tobago", "TN": "Tunisia", "TR": "Turkey", "TM": "Turkmenistan",
    "TC": "Turks and Caicos Islands", "TV": "Tuvalu", "UG": "Uganda", "UA": "Ukraine", "AE": "United Arab Emirates",
    "GB": "United Kingdom", "US": "United States", "UM": "United States Minor Outlying Islands", "UY": "Uruguay",
    "UZ": "Uzbekistan", "VU": "Vanuatu", "VE": "Venezuela", "VN": "Vietnam", "VG": "Virgin Islands, British",
    "VI": "Virgin Islands, U.S.", "WF": "Wallis and Futuna", "EH": "Western Sahara", "YE": "Yemen",
    "ZM": "Zambia", "ZW": "Zimbabwe",
}

def detect_country_from_number(num: str):
    try:
        parsed = phonenumbers.parse(num, None)
        region = phonenumbers.region_code_for_number(parsed)

        country_name = COUNTRY_NAMES.get(region)
        
        if not country_name and region:
            country_name = region  # Fallback to region code if name missing

        if not country_name:
            country_name = f"Country +{parsed.country_code}"

        flag = region_to_flag(region) if region else ""
        return {
            "country_name": country_name,
            "flag": flag,
            "region": region,
            "country_code": parsed.country_code,
        }
    except Exception:
        return None


def is_member_all_channels(user_id: int) -> bool:
    with db_lock:
        cur.execute("SELECT is_verified FROM users WHERE telegram_id = ?", (user_id,))
        row = cur.fetchone()
        if row and row[0] == 1:
            return True

        cur.execute("SELECT channel_id FROM required_channels")
        rows = cur.fetchall()
    
    if not rows:
        return True

    now_ts = time.time()
    cached = membership_cache.get(user_id)
    if cached:
        ts, ok = cached
        if ok and (now_ts - ts) < 600: # Cache for 10 minutes
            return True

    for (ch,) in rows:
        try:
            member = bot.get_chat_member(ch, user_id)
            if member.status in ("left", "kicked"):
                membership_cache[user_id] = (now_ts, False)
                return False
        except Exception:
            membership_cache[user_id] = (now_ts, False)
            return False

    membership_cache[user_id] = (now_ts, True)
    return True


def format_amount(val: float) -> str:
    try:
        s = f"{float(val):.8f}"
    except Exception:
        return str(val)
    s = s.rstrip("0").rstrip(".")
    return s or "0"


# ----------------- UI texts -----------------
START_FEATURES_TEXT = (
    "⚡ Fast delivery\n"
    "🔒 Secure numbers\n"
    "♻️ Change anytime"
)
SELECT_COUNTRY_TEXT = "🌍 <b>Select Country:</b>"


# ----------------- Keyboards -----------------
def build_join_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    
    with db_lock:
        cur.execute("SELECT button_text, invite_link FROM required_channels")
        rows = cur.fetchall()
        
    for text, link in rows:
        kb.row(InlineKeyboardButton(text, url=link, api_kwargs={"style": "primary"}))
        
    kb.row(InlineKeyboardButton("✅ Verify", callback_data="verify_join", api_kwargs={"style": "success"}))
    return kb


def build_main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KBtn("📞 GET NUMBER", style="success"), KBtn("💰 BALANCE", style="primary"))
    kb.row(KBtn("👥 REFER AND EARN", style="primary"), KBtn("💬 SUPPORT", style="primary"))
    kb.row(KBtn("📊 STATUS", style="primary"))
    if is_admin(user_id):
        kb.row(KBtn("🛠 ADMIN PANEL", style="danger"))
    return kb


def build_balance_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("Bkash", callback_data="wdm:bkash", api_kwargs={"style": "primary"}),
        InlineKeyboardButton("Nagad", callback_data="wdm:nagad", api_kwargs={"style": "primary"}),
    )
    kb.row(InlineKeyboardButton("Binance", callback_data="wdm:binance", api_kwargs={"style": "primary"}))
    kb.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu", api_kwargs={"style": "danger"}))
    return kb


def build_confirm_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Confirm", callback_data="wd_confirm", api_kwargs={"style": "success"}),
        InlineKeyboardButton("❌ Cancel", callback_data="wd_cancel", api_kwargs={"style": "danger"}),
    )
    return kb


def build_status_text() -> str:
    """
    WhatsApp | 🇧🇩 Bangladesh | 100
    Telegram | 🇺🇸 United States | 200
    """
    with db_lock:
        cur.execute(
            """
            SELECT b.short_name, b.country, b.flag,
                   COUNT(n.id) as available
            FROM batches b
            JOIN numbers n ON n.batch_id = b.id AND n.is_used = 0
            WHERE b.is_deleted = 0
            GROUP BY b.id, b.short_name, b.country, b.flag
            ORDER BY b.short_name ASC, b.country ASC
            """
        )
        rows = cur.fetchall()
    if not rows:
        return "📊 <b>Status</b>\n\nNo numbers available at the moment."
    lines = []
    for short_name, country, flag, avail in rows:
        lines.append(
            f"{short_name} | {flag or ''} {country} | <code>{avail}</code>"
        )
    return "📊 <b>Status</b>\n\n" + "\n".join(lines)


def build_refer_text(user_tg_id: int) -> str:
    global BOT_USERNAME
    balance = get_user_balance(user_tg_id)
    ref_count, ref_earned = get_ref_stats(user_tg_id)
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_tg_id}"
    return (
        "👥 <b>Refer & Earn</b>\n\n"
        f"🔗 <b>Your referral link:</b>\n<code>{ref_link}</code>\n\n"
        f"📈 <b>Total referrals:</b> <code>{ref_count}</code>\n"
        f"💵 <b>Referral earnings:</b> <code>{format_amount(ref_earned)}$</code>\n"
        f"➕ <b>Per referral:</b> <code>{format_amount(REF_AMOUNT)}$</code>\n\n"
        f"💳 <b>Your current balance:</b> <code>{format_amount(balance)}$</code>"
    )


def build_balance_text(user_tg_id: int) -> str:
    balance = get_user_balance(user_tg_id)
    return (
        "💰 <b>Balance</b>\n\n"
        f"💳 Current balance: <code>{format_amount(balance)}$</code>\n"
        f"📉 Minimum withdraw: <code>{format_amount(MIN_WITHDRAW)}$</code>\n\n"
        "Choose a withdrawal method below:"
    )


def broadcast_new_stock(batch_id: int):
    with db_lock:
        cur.execute(
            "SELECT country, flag, short_name, rate FROM batches WHERE id = ?",
            (batch_id,),
        )
        row = cur.fetchone()
        if not row:
            return
        country, flag, short_name, rate = row

    title_flag = f"{country} {flag or ''}".strip()
    text = (
        "🆕 <b>New Stock Added</b>\n\n"
        f"🌍 {title_flag} | <code>{short_name}</code>\n"
        f"💵 Rate per OTP: <code>{format_amount(rate)}$</code>"
    )
    with db_lock:
        try:
            cur.execute("SELECT telegram_id FROM users")
            rows = cur.fetchall()
        except Exception:
            return
    
    sent = 0
    for (uid,) in rows:
        try:
            bot.send_message(uid, text)
            sent += 1
            if sent % 20 == 0:
                time.sleep(1) # throttle to avoid rate limits
        except Exception:
            continue
    cleanup_caches()


# ----------------- /start -----------------
@bot.message_handler(commands=["start"])
def cmd_start(message):
    global BOT_USERNAME
    if BOT_USERNAME is None:
        BOT_USERNAME = bot.get_me().username

    args = message.text.split()
    ref_id = None
    if len(args) > 1:
        try:
            ref_id = int(args[1])
        except Exception:
            ref_id = None

    get_or_create_user(message.from_user.id, ref_id)
    clear_state(message.from_user.id)

    if not is_member_all_channels(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "Please join all required channels first, then tap <b>Verify</b>.",
            reply_markup=build_join_keyboard(),
        )
        return

    bot.send_message(
        message.chat.id,
        START_FEATURES_TEXT,
        reply_markup=build_main_menu_keyboard(message.from_user.id),
    )


@bot.callback_query_handler(func=lambda call: True)
def callback_router(call):
    data = call.data
    
    # Fast response for common non-alert actions
    if not any(x in data for x in ["verify", "wdc_", "wdr_", "delcat_", "delbatch_", "confirmdel_"]):
        try:
            bot.answer_callback_query(call.id, cache_time=5)
        except Exception:
            pass

    if data == "verify_join":
        user_id = call.from_user.id
        if is_member_all_channels(user_id):
            with db_lock:
                cur.execute("UPDATE users SET is_verified = 1 WHERE telegram_id = ?", (user_id,))
                conn.commit()
            bot.answer_callback_query(call.id, "✅ Verified!")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            bot.send_message(
                call.message.chat.id,
                START_FEATURES_TEXT,
                reply_markup=build_main_menu_keyboard(user_id),
            )
        else:
            bot.answer_callback_query(
                call.id, "❌ Please join all channels first.", show_alert=True
            )
        return

    if data == "back_menu":
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.send_message(
            call.message.chat.id,
            START_FEATURES_TEXT,
            reply_markup=build_main_menu_keyboard(call.from_user.id),
        )
        return

    # withdraw method select
    if data.startswith("wdm:"):
        method_key = data.split(":", 1)[1].strip().lower()
        method_map = {"bkash": "Bkash", "nagad": "Nagad", "binance": "Binance"}
        if method_key not in method_map:
            return

        user_id = call.from_user.id
        bal = get_user_balance(user_id)
        if bal < MIN_WITHDRAW:
            bot.answer_callback_query(
                call.id, "Withdraw not available.", show_alert=True
            )
            bot.send_message(
                call.message.chat.id,
                "❌ Withdraw is not available.\n\n"
                f"Minimum: <code>{format_amount(MIN_WITHDRAW)}$</code>\n"
                f"Balance: <code>{format_amount(bal)}$</code>",
                reply_markup=build_main_menu_keyboard(user_id),
            )
            return

        set_state(user_id, "wd_account", {"method": method_map[method_key]})
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"Send your <b>{method_map[method_key]}</b> number / ID:",
        )
        return

    if data == "wd_confirm":
        user_id = call.from_user.id
        st = get_state(user_id)
        if st["state"] != "wd_confirm":
            bot.answer_callback_query(
                call.id, "Nothing to confirm.", show_alert=True
            )
            return

        method = st["data"].get("method", "")
        account = st["data"].get("account", "")
        amount = float(st["data"].get("amount") or 0)

        with db_lock:
            cur.execute(
                "SELECT id FROM users WHERE telegram_id = ?", (user_id,)
            )
            row = cur.fetchone()
            if not row:
                bot.answer_callback_query(
                    call.id, "User not found.", show_alert=True
                )
                clear_state(user_id)
                return
            db_user_id = row[0]

            details = f"Method: {method}\nAccount: {account}"
            now = datetime.utcnow().isoformat()
            cur.execute(
                """
                INSERT INTO withdraw_requests (user_id, amount, details, created_at, status)
                VALUES (?,?,?,?,?)
                """,
                (db_user_id, amount, details, now, "pending"),
            )
            conn.commit()

        snapshot_user_to_file(user_id)

        for oid in OWNER_IDS:
            try:
                bot.send_message(
                    oid,
                    "💸 <b>New Withdraw Request</b>\n\n"
                    f"👤 User ID: <code>{user_id}</code>\n"
                    f"Username: @{call.from_user.username or 'N/A'}\n\n"
                    f"💳 Method: <b>{method}</b>\n"
                    f"🆔 Account/ID: <code>{account}</code>\n"
                    f"💵 Amount: <code>{format_amount(amount)}$</code>",
                )
            except Exception:
                pass

        bot.answer_callback_query(call.id, "Submitted!")
        bot.send_message(
            call.message.chat.id,
            "✅ <b>Your withdraw request has been submitted.</b>\nAdmin will review it soon.",
            reply_markup=build_main_menu_keyboard(user_id),
        )
        clear_state(user_id)
        return

    if data == "wd_cancel":
        clear_state(call.from_user.id)
        bot.answer_callback_query(call.id, "Cancelled.")
        bot.send_message(
            call.message.chat.id,
            "✅ Withdraw cancelled.",
            reply_markup=build_main_menu_keyboard(call.from_user.id),
        )
        return

    # country select
    if data.startswith("batch_"):
        batch_id = int(data.split("_", 1)[1])
        handle_get_numbers_for_batch(call, batch_id)
        return

    if data.startswith("chgnum_"):
        batch_id = int(data.split("_", 1)[1])
        handle_get_numbers_for_batch(call, batch_id)
        return

    if data == "changecountry":
        bot.answer_callback_query(call.id)
        show_get_numbers_menu(
            call.message.chat.id,
            call.from_user.id,
            message_to_edit=call.message,
        )
        return

    # admin: withdraw requests
    if data == "withdraw_requests":
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        else:
            bot.answer_callback_query(call.id)
            handle_withdraw_requests(call)
        return

    if data.startswith("wdc_"):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
            return
        req_id = int(data.split("_", 1)[1])
        handle_withdraw_confirm(call, req_id)
        return

    if data.startswith("wdr_"):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
            return
        req_id = int(data.split("_", 1)[1])
        handle_withdraw_reject(call, req_id)
        return

    # admin: numbers / broadcast / admins / admin list
    if data == "addnums":
        handle_add_numbers_start(call)
        return

    if data == "manage_categories":
        handle_manage_categories(call)
        return

    if data == "add_category":
        handle_add_category_start(call)
        return

    if data.startswith("delcat_"):
        cat_id = int(data.split("_", 1)[1])
        delete_category(call, cat_id)
        return

    if data == "managenums":
        handle_manage_numbers(call)
        return

    if data.startswith("delbatch_"):
        batch_id = int(data.split("_", 1)[1])
        ask_confirm_delete_batch(call, batch_id)
        return

    if data.startswith("confirmdel_"):
        batch_id = int(data.split("_", 1)[1])
        confirm_delete_batch(call, batch_id)
        return

    if data.startswith("cancel_del_"):
        bot.answer_callback_query(call.id, "Cancelled.")
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None,
            )
        except Exception:
            pass
        return

    if data == "broadcast":
        handle_broadcast_start(call)
        return

    if data == "add_admin":
        handle_add_admin_start(call)
        return

    if data == "remove_admin":
        handle_remove_admin_start(call)
        return

    if data.startswith("selrm_"):
        admin_id = int(data.split("_", 1)[1])
        ask_confirm_remove_admin(call, admin_id)
        return

    if data.startswith("confirmrm_"):
        admin_id = int(data.split("_", 1)[1])
        confirm_remove_admin(call, admin_id)
        return

    if data.startswith("cancelrm_"):
        cancel_remove_admin(call)
        return

    if data == "admin_list":
        handle_admin_list(call)
        return

    if data == "brd_skip":
        bot.answer_callback_query(call.id, "Skipped.")
        bot.edit_message_text("✅ New category added without broadcast.", call.message.chat.id, call.message.message_id)
        return

    if data.startswith("brd_"):
        batch_id = int(data.split("_", 1)[1])
        bot.answer_callback_query(call.id, "Starting broadcast...")
        bot.edit_message_text("📣 Broadcast started...", call.message.chat.id, call.message.message_id)
        # Background broadcast to avoid blocking the main thread too long
        threading.Thread(target=broadcast_new_stock, args=(batch_id,), daemon=True).start()
        return

    if data == "admin_settings":
        handle_admin_settings(call)
        return

    if data.startswith("edit_set_"):
        key = data.split("_", 2)[2]
        handle_edit_setting(call, key)
        return

    if data == "manage_channels":
        handle_manage_channels(call)
        return

    if data == "add_channel":
        handle_add_channel(call)
        return

    if data.startswith("del_ch_"):
        db_id = int(data.split("_", 2)[2])
        handle_del_channel(call, db_id)
        return

    if data == "admin_panel_back":
        bot.answer_callback_query(call.id)
        bot.delete_message(call.message.chat.id, call.message.message_id) # Delete sub-menu
        send_admin_panel(call.message.chat.id, call.from_user.id)
        return

    if data.startswith("selcat_"):
        handle_add_numbers_select_category(call)
        return

    if data.startswith("cat_"):
        handle_select_category(call)
        return

    bot.answer_callback_query(call.id, "Unknown action.")


# ----------------- Get Numbers menu -----------------
def show_get_numbers_menu(chat_id: int, user_tg_id: int, message_to_edit=None):
    if not is_member_all_channels(user_tg_id):
        text = "❌ Please join all channels first, then tap <b>Verify</b>."
        if message_to_edit:
            bot.edit_message_text(
                text,
                chat_id,
                message_to_edit.message_id,
                reply_markup=build_join_keyboard(),
            )
        else:
            bot.send_message(chat_id, text, reply_markup=build_join_keyboard())
        return

    # Show only Categories that have available numbers
    with db_lock:
        cur.execute(
            """
            SELECT c.id, c.name, c.emoji
            FROM categories c
            JOIN batches b ON b.category_id = c.id
            JOIN numbers n ON n.batch_id = b.id AND n.is_used = 0
            WHERE b.is_deleted = 0
            GROUP BY c.id, c.name, c.emoji
            ORDER BY c.name ASC
            """
        )
        rows = cur.fetchall()

    if not rows:
        # Fallback if no categories yet: show legacy batches with available numbers
        with db_lock:
            cur.execute(
                """
                SELECT b.id, b.country, b.flag, b.short_name
                FROM batches b
                JOIN numbers n ON n.batch_id = b.id AND n.is_used = 0
                WHERE b.is_deleted = 0
                GROUP BY b.id, b.country, b.flag, b.short_name
                ORDER BY b.country ASC, b.short_name ASC
                """
            )
            legacy_rows = cur.fetchall()
        
        if not legacy_rows:
            text = "😕 <b>No services available right now.</b>"
            if message_to_edit:
                bot.edit_message_text(text, chat_id, message_to_edit.message_id)
            else:
                bot.send_message(chat_id, text)
            return

        kb = InlineKeyboardMarkup()
        for b_id, country, flag, short in legacy_rows:
            label = f"{short} | {flag or ''} {country}"
            kb.row(InlineKeyboardButton(label.strip(), callback_data=f"batch_{b_id}", api_kwargs={"style": "primary"}))
    else:
        kb = InlineKeyboardMarkup()
        for cat_id, name, emoji in rows:
            label = f"{emoji or ''} {name}".strip()
            kb.row(InlineKeyboardButton(label, callback_data=f"cat_{cat_id}", api_kwargs={"style": "primary"}))

    text = "🚀 <b>Select a Service:</b>"
    if message_to_edit:
        bot.edit_message_text(text, chat_id, message_to_edit.message_id, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)


def handle_select_category(call):
    cat_id = int(call.data.split("_", 1)[1])
    user_tg_id = call.from_user.id
    chat_id = call.message.chat.id

    with db_lock:
        cur.execute("SELECT name, emoji FROM categories WHERE id = ?", (cat_id,))
        row = cur.fetchone()
        if not row:
            bot.answer_callback_query(call.id, "Category not found.")
            return
        cat_name, cat_emoji = row

        cur.execute(
            """
            SELECT b.id, b.country, b.flag, COUNT(n.id) as avail
            FROM batches b
            JOIN numbers n ON n.batch_id = b.id AND n.is_used = 0
            WHERE b.category_id = ? AND b.is_deleted = 0
            GROUP BY b.id, b.country, b.flag
            ORDER BY b.country ASC
            """,
            (cat_id,),
        )
        countries = cur.fetchall()

    kb = InlineKeyboardMarkup()
    # Group countries into rows of 2
    row = []
    for i, (b_id, country, flag, avail) in enumerate(countries):
        label = f"{flag or ''} {country} ({avail})".strip()
        row.append(InlineKeyboardButton(label, callback_data=f"batch_{b_id}", api_kwargs={"style": "primary"}))
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    
    kb.row(InlineKeyboardButton("⬅ Back To Services", callback_data="changecountry", api_kwargs={"style": "danger"}))

    text = f"{cat_emoji or ''} <b>Select country for {cat_name}:</b>"
    bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=kb)


class CopyText:
    def __init__(self, text):
        self.text = text
    def to_dict(self):
        return {"text": self.text}

def handle_get_numbers_for_batch(call, batch_id: int):
    user_tg_id = call.from_user.id
    chat_id = call.message.chat.id

    if not is_member_all_channels(user_tg_id):
        bot.answer_callback_query(
            call.id, "Please join all channels first.", show_alert=True
        )
        return

    with db_lock:
        # Release prefetched numbers of other batches
        cur.execute(
            """
            UPDATE numbers
            SET is_used = 0, used_by = NULL, used_at = NULL, is_current = 0
            WHERE used_by = ? AND is_current = 2 AND batch_id != ?
            """,
            (user_tg_id, batch_id),
        )

        cur.execute(
            "SELECT country, flag, short_name, per_user FROM batches WHERE id = ? AND is_deleted = 0",
            (batch_id,),
        )
        batch = cur.fetchone()
        if not batch:
            bot.answer_callback_query(
                call.id, "This category is not available."
            )
            return

        country, flag, short_name, per_user = batch

        # Clear previous current numbers for this user
        cur.execute(
            "UPDATE numbers SET is_current = 0 WHERE used_by = ? AND is_current = 1",
            (user_tg_id,),
        )

        # Try to use prefetched numbers (is_current=2)
        cur.execute(
            """
            SELECT id, value FROM numbers
            WHERE batch_id = ? AND used_by = ? AND is_current = 2
            ORDER BY id ASC
            LIMIT ?
            """,
            (batch_id, user_tg_id, per_user),
        )
        prefetched = cur.fetchall()

        now = datetime.utcnow().isoformat()

        if prefetched:
            current_nums = prefetched
            pref_ids = [nid for (nid, _val) in prefetched]
            cur.executemany(
                "UPDATE numbers SET is_current = 1 WHERE id = ?",
                [(nid,) for nid in pref_ids],
            )
        else:
            # No prefetched -> allocate fresh numbers
            cur.execute(
                """
                SELECT id, value FROM numbers
                WHERE batch_id = ? AND is_used = 0
                ORDER BY id ASC
                LIMIT ?
                """,
                (batch_id, per_user),
            )
            current_nums = cur.fetchall()
            if not current_nums:
                bot.answer_callback_query(
                    call.id, "No numbers left in this category."
                )
                return

            num_ids = [nid for (nid, _val) in current_nums]
            cur.executemany(
                """
                UPDATE numbers
                SET is_used = 1, used_by = ?, used_at = ?, is_current = 1
                WHERE id = ?
                """,
                [(user_tg_id, now, nid) for nid in num_ids],
            )

        # Prefetch next group
        cur.execute(
            """
            SELECT id, value FROM numbers
            WHERE batch_id = ? AND is_used = 0
            ORDER BY id ASC
            LIMIT ?
            """,
            (batch_id, per_user),
        )
        next_prefetched = cur.fetchall()
        if next_prefetched:
            next_ids = [nid for (nid, _v) in next_prefetched]
            now2 = datetime.utcnow().isoformat()
            cur.executemany(
                """
                UPDATE numbers
                SET is_used = 1, used_by = ?, used_at = ?, is_current = 2
                WHERE id = ?
                """,
                [(user_tg_id, now2, nid) for nid in next_ids],
            )

        conn.commit()

    text = (
        f"{flag or ''} <b>{country} Number Assigned:</b>\n"
        "╭───────────────╮\n"
        "│    ⏳ <b>Waiting for OTP...</b>\n"
        "╰───────────────╯"
    )

    kb = InlineKeyboardMarkup()
    for _nid, val in current_nums:
        # Wrap the copy_text value in an object with a to_dict() method
        kb.add(InlineKeyboardButton(
            text=f"{flag or ''} {val}",
            copy_text=CopyText(val),
        ))

    kb.row(
        InlineKeyboardButton("🔄 Change Number", callback_data=f"chgnum_{batch_id}", style="danger"),
    )
    kb.row(
        InlineKeyboardButton("🌍 Change Country", callback_data="changecountry", style="success"),
    )
    if OTP_GROUP_LINK:
        kb.row(InlineKeyboardButton("🔔 Otp Group", url=OTP_GROUP_LINK, style="primary"))

    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(
            text, chat_id, call.message.message_id, reply_markup=kb
        )
    except Exception:
        bot.send_message(chat_id, text, reply_markup=kb)




# ----------------- Admin panel helpers -----------------
def send_admin_panel(chat_id: int, user_id: int):
    if not is_admin(user_id):
        bot.send_message(
            chat_id, "❌ You are not allowed to access the admin panel."
        )
        return

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("➕ Add Numbers", callback_data="addnums", api_kwargs={"style": "success"}),
        InlineKeyboardButton("📂 Manage Numbers", callback_data="managenums", api_kwargs={"style": "primary"}),
    )
    kb.row(
        InlineKeyboardButton("📁 Manage Categories", callback_data="manage_categories", api_kwargs={"style": "primary"}),
        InlineKeyboardButton("💸 Withdraw Requests", callback_data="withdraw_requests", api_kwargs={"style": "primary"}),
    )
    kb.row(
        InlineKeyboardButton("📣 Broadcast", callback_data="broadcast", api_kwargs={"style": "primary"}),
        InlineKeyboardButton("👤 Admin List", callback_data="admin_list", api_kwargs={"style": "primary"}),
    )
    kb.row(
        InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings", api_kwargs={"style": "primary"}),
        InlineKeyboardButton("📢 Req. Channels", callback_data="manage_channels", api_kwargs={"style": "primary"}),
    )
    if is_owner(user_id):
        kb.row(
            InlineKeyboardButton("👮 Add Admin", callback_data="add_admin", api_kwargs={"style": "success"}),
            InlineKeyboardButton("🚫 Remove Admin", callback_data="remove_admin", api_kwargs={"style": "danger"}),
        )

    bot.send_message(chat_id, "🛠 <b>Admin Panel</b>", reply_markup=kb)


def handle_admin_list(call):
    """Show only non-owner admins (role = 'admin')."""
    admin_id = call.from_user.id
    if not is_admin(admin_id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return

    with db_lock:
        cur.execute(
            "SELECT telegram_id, role FROM admins WHERE role = 'admin' ORDER BY telegram_id ASC"
        )
        rows = cur.fetchall()

    if not rows:
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "There are no admins (only owners).")
        return

    lines = []
    for tg_id, role in rows:
        try:
            chat = bot.get_chat(tg_id)
            uname = (
                f"@{chat.username}"
                if chat.username
                else (chat.first_name or "User")
            )
        except Exception:
            uname = "Unknown"
        lines.append(f"<code>{tg_id}</code> | {uname} | <b>{role}</b>")

    text = "👤 <b>Admin List</b>\n\n" + "\n".join(lines)
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text)


# ----------------- Settings Management -----------------
def handle_admin_settings(call):
    admin_id = call.from_user.id
    if not is_admin(admin_id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return

    configs = {
        "owner_username": "Support Username",
        "ref_amount": "Referral Amount ($)",
        "min_withdraw": "Min Withdraw ($)",
        "otp_group_link": "OTP Group Link",
    }

    kb = InlineKeyboardMarkup()
    lines = ["⚙️ <b>Bot Settings</b>\n"]
    for key, label in configs.items():
        curr_val = get_config(key)
        lines.append(f"• {label}: <code>{curr_val}</code>")
        kb.row(InlineKeyboardButton(f"Edit {label}", callback_data=f"edit_set_{key}", api_kwargs={"style": "primary"}))

    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "\n".join(lines) + "\n\nTap a button to modify a setting:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
    )


def handle_edit_setting(call, key):
    admin_id = call.from_user.id
    if not is_admin(admin_id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return

    set_state(admin_id, "edit_setting", {"key": key})
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        f"📝 <b>Editing Setting:</b> <code>{key}</code>\n\n"
        f"Current value: <code>{get_config(key)}</code>\n\n"
        "Send the new value below:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=InlineKeyboardMarkup().row(
            InlineKeyboardButton("❌ Cancel", callback_data="admin_settings", api_kwargs={"style": "danger"})
        ),
    )


# ----------------- Channel Management -----------------
def handle_manage_channels(call):
    admin_id = call.from_user.id
    if not is_admin(admin_id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return

    with db_lock:
        cur.execute("SELECT id, channel_id, button_text FROM required_channels")
        rows = cur.fetchall()

    kb = InlineKeyboardMarkup()
    text = "📢 <b>Required Channels</b>\n\n"
    if not rows:
        text += "No required channels configured."
    else:
        for db_id, ch_id, btn_text in rows:
            text += f"• <code>{ch_id}</code> | {btn_text}\n"
            kb.row(
                InlineKeyboardButton(f"❌ Delete {btn_text}", callback_data=f"del_ch_{db_id}", api_kwargs={"style": "danger"})
            )

    kb.row(InlineKeyboardButton("➕ Add New Channel", callback_data="add_channel", api_kwargs={"style": "success"}))
    kb.row(InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_panel_back", api_kwargs={"style": "primary"}))

    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
    )


def handle_add_channel(call):
    admin_id = call.from_user.id
    if not is_admin(admin_id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return

    set_state(admin_id, "add_ch_id", {})
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "📢 <b>Adding New Channel</b>\n\n"
        "Step 1: Send the <b>Channel ID</b>.\n"
        "Example: <code>-1001234567890</code>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=InlineKeyboardMarkup().row(
            InlineKeyboardButton("❌ Cancel", callback_data="manage_channels", api_kwargs={"style": "danger"})
        ),
    )


def handle_del_channel(call, db_id):
    admin_id = call.from_user.id
    if not is_admin(admin_id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return

    with db_lock:
        cur.execute("DELETE FROM required_channels WHERE id = ?", (db_id,))
        conn.commit()

    bot.answer_callback_query(call.id, "Channel deleted!")
    handle_manage_channels(call)


# ----------------- Category Management -----------------
def handle_manage_categories(call):
    admin_id = call.from_user.id
    if not is_admin(admin_id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return

    with db_lock:
        cur.execute("SELECT id, name, emoji FROM categories ORDER BY name ASC")
        rows = cur.fetchall()

    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("➕ Add Category", callback_data="add_category", api_kwargs={"style": "success"}))

    if not rows:
        text = "📁 <b>Category Management</b>\n\nNo categories found."
    else:
        lines = []
        for cid, name, emoji in rows:
            lines.append(f"• {emoji or ''} <b>{name}</b> (ID: {cid})")
            kb.row(InlineKeyboardButton(f"🗑 Delete {name}", callback_data=f"delcat_{cid}", api_kwargs={"style": "danger"}))
        text = "📁 <b>Category Management</b>\n\n" + "\n".join(lines)

    bot.answer_callback_query(call.id)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)


def handle_add_category_start(call):
    admin_id = call.from_user.id
    if not is_admin(admin_id):
        return
    set_state(admin_id, "add_cat_name", {})
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "➕ <b>Add Category</b>\n\nSend category name (e.g., 🔵 Facebook):")


def delete_category(call, cat_id):
    if not is_admin(call.from_user.id):
        return
    with db_lock:
        cur.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        conn.commit()
    bot.answer_callback_query(call.id, "Category deleted!")
    handle_manage_categories(call)


def handle_add_numbers_start(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return

    set_state(user_id, "addnum_numbers", {})
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "➕ <b>Add Numbers</b>\n\n"
        "Step 1: Send all numbers.\n"
        "• Plain text (one per line), or\n"
        "• Upload a .txt / .csv file (one per line)\n\n"
        "Example:\n<code>+8801XXXXXXXXX\n8801YYYYYYYYY\n+8801ZZZZZZZZZ</code>",
    )


def process_add_numbers_input(user_id: int, chat_id: int, text_raw: str):
    lines = text_raw.splitlines()
    cleaned = []
    for line in lines:
        num = normalize_number(line)
        if num:
            cleaned.append(num)

    if not cleaned:
        bot.send_message(
            chat_id, "No valid numbers detected. Please send again."
        )
        return

    info = detect_country_from_number(cleaned[0])
    if not info:
        bot.send_message(
            chat_id,
            "Could not detect country.\nMake sure numbers are in international format like +880...",
        )
        return

    c_code = info["country_code"]
    for num in cleaned[1:]:
        ni = detect_country_from_number(num)
        if not ni or ni["country_code"] != c_code:
            bot.send_message(
                chat_id,
                "All numbers in one batch must belong to the same country.",
            )
            return

    data = {
        "numbers": cleaned,
        "country": info["country_name"],
        "flag": info["flag"],
    }
    
    with db_lock:
        cur.execute("SELECT id, name, emoji FROM categories ORDER BY name ASC")
        rows = cur.fetchall()
    
    if not rows:
        bot.send_message(chat_id, "❌ No categories found. Please create a category in Admin Panel first.")
        clear_state(user_id)
        return

    kb = InlineKeyboardMarkup()
    for cid, name, emoji in rows:
        kb.row(InlineKeyboardButton(f"{emoji or ''} {name}", callback_data=f"selcat_{cid}", api_kwargs={"style": "primary"}))
    
    set_state(user_id, "addnum_category", data)

    bot.send_message(
        chat_id,
        f"Detected country: {info['flag'] or ''} <b>{info['country_name']}</b> (+{c_code})\n\n"
        "Step 2: Select a category for these numbers:",
        reply_markup=kb
    )


def handle_add_numbers_select_category(call):
    user_id = call.from_user.id
    st = get_state(user_id)
    if st["state"] != "addnum_category":
        bot.answer_callback_query(call.id, "Session expired.")
        return
    
    cat_id = int(call.data.split("_", 1)[1])
    data = st["data"]
    
    with db_lock:
        cur.execute("SELECT name, emoji FROM categories WHERE id = ?", (cat_id,))
        cat = cur.fetchone()
    
    if not cat:
        bot.answer_callback_query(call.id, "Category not found.")
        return
    
    data["category_id"] = cat_id
    data["short_name"] = cat[0] # Use category name as short_name for legacy compatibility
    data["type_desc"] = f"{cat[1] or ''} {cat[0]}".strip()
    
    set_state(user_id, "addnum_per_user", data)
    bot.edit_message_text(
        f"Category: <b>{cat[1] or ''} {cat[0]}</b>\n\n"
        "Step 3: How many numbers per user? Send an integer. Example: <code>1</code>",
        call.message.chat.id,
        call.message.message_id
    )


def handle_manage_numbers(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return

    with db_lock:
        cur.execute(
            """
            SELECT b.id, b.country, b.flag, b.short_name, b.rate, b.created_at,
                   (SELECT COUNT(*) FROM numbers n WHERE n.batch_id = b.id) as total,
                   (SELECT COUNT(*) FROM numbers n WHERE n.batch_id = b.id AND n.is_used = 0) as available,
                   c.name as cat_name, c.emoji as cat_emoji
            FROM batches b
            LEFT JOIN categories c ON b.category_id = c.id
            WHERE b.is_deleted = 0
            ORDER BY b.created_at DESC
            LIMIT 10
            """
        )
        rows = cur.fetchall()

    if not rows:
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "📂 <b>Manage Numbers</b>\n\nNo active batches.",
            call.message.chat.id,
            call.message.message_id,
        )
        return

    lines = []
    kb = InlineKeyboardMarkup()
    for b_id, country, flag, short, rate, created, total, avail, cat_name, cat_emoji in rows:
        created_short = (created or "").split("T")[0]
        cat_info = f"{cat_emoji or ''} {cat_name or short}"
        lines.append(
            f"ID <code>{b_id}</code> | {flag or ''} <b>{country}</b>\n"
            f"📂 Category: <b>{cat_info}</b>\n"
            f"📅 {created_short} | Total: <code>{total}</code> | Available: <code>{avail}</code>\n"
            f"💵 Rate: <code>{format_amount(rate)}$</code>"
        )
        kb.row(
            InlineKeyboardButton(
                f"🗑 Delete ID {b_id}", callback_data=f"delbatch_{b_id}", api_kwargs={"style": "danger"}
            )
        )

    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "📂 <b>Manage Numbers</b>\n\n" + "\n\n".join(lines),
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
    )


def ask_confirm_delete_batch(call, batch_id: int):
    with db_lock:
        cur.execute(
            "SELECT country, flag, short_name FROM batches WHERE id = ? AND is_deleted = 0",
            (batch_id,),
        )
        row = cur.fetchone()
    if not row:
        bot.answer_callback_query(call.id, "Batch not found.", show_alert=True)
        return

    country, flag, short = row
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Confirm", callback_data=f"confirmdel_{batch_id}", api_kwargs={"style": "danger"}),
        InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_del_{batch_id}", api_kwargs={"style": "primary"}),
    )
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        f"⚠ <b>Confirm Delete</b>\n\n"
        f"ID: <code>{batch_id}</code>\n"
        f"{flag or ''} <b>{country}</b> (<code>{short}</code>)\n\n"
        "All numbers in this batch will be removed.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
    )


def confirm_delete_batch(call, batch_id: int):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return

    with db_lock:
        cur.execute("DELETE FROM numbers WHERE batch_id = ?", (batch_id,))
        cur.execute(
            "UPDATE batches SET is_deleted = 1 WHERE id = ?", (batch_id,)
        )
        conn.commit()
    bot.answer_callback_query(call.id, "Deleted.")
    bot.edit_message_text(
        "✅ Batch and its numbers deleted.",
        call.message.chat.id,
        call.message.message_id,
    )


def handle_broadcast_start(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return
    set_state(call.from_user.id, "broadcast", {})
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "📣 <b>Broadcast</b>\n\nSend the message (text, photo, video, audio, etc.) you want to broadcast to all users.\n\n"
        "To cancel, send /start.",
    )


def handle_add_admin_start(call):
    if not is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "Owner only.", show_alert=True)
        return
    set_state(call.from_user.id, "add_admin", {})
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "👮 <b>Add Admin</b>\n\nSend the Telegram numeric ID of the user.\n\nTo cancel, send /start.",
    )


def handle_remove_admin_start(call):
    if not is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "Owner only.", show_alert=True)
        return

    with db_lock:
        cur.execute("SELECT telegram_id, role FROM admins WHERE role != 'owner'")
        rows = cur.fetchall()
    if not rows:
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id, "There are no non-owner admins to remove."
        )
        return

    kb = InlineKeyboardMarkup()
    lines = []
    for admin_id, role in rows:
        try:
            chat = bot.get_chat(admin_id)
            name = chat.username or (chat.first_name or "User")
        except Exception:
            name = "Unknown"
        kb.row(
            InlineKeyboardButton(
                f"{name} ({admin_id})", callback_data=f"selrm_{admin_id}", api_kwargs={"style": "primary"}
            )
        )
        lines.append(f"• <code>{admin_id}</code> - {name}")

    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "🚫 <b>Remove Admin</b>\n\nSelect an admin:\n\n" + "\n".join(lines),
        reply_markup=kb,
    )


def ask_confirm_remove_admin(call, admin_id: int):
    if not is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "Owner only.", show_alert=True)
        return

    try:
        chat = bot.get_chat(admin_id)
        name = chat.username or (chat.first_name or "User")
    except Exception:
        name = "Unknown"

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Confirm", callback_data=f"confirmrm_{admin_id}", api_kwargs={"style": "danger"}),
        InlineKeyboardButton("❌ Cancel", callback_data=f"cancelrm_{admin_id}", api_kwargs={"style": "primary"}),
    )
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        f"⚠ <b>Confirm Remove Admin</b>\n\nID: <code>{admin_id}</code>\nName: {name}",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
    )


def confirm_remove_admin(call, admin_id: int):
    if not is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "Owner only.", show_alert=True)
        return

    with db_lock:
        cur.execute(
            "DELETE FROM admins WHERE telegram_id = ? AND role != 'owner'",
            (admin_id,),
        )
        conn.commit()
    bot.answer_callback_query(call.id, "Removed.")
    bot.edit_message_text(
        f"✅ Admin with ID <code>{admin_id}</code> removed (if existed).",
        call.message.chat.id,
        call.message.message_id,
    )


def cancel_remove_admin(call):
    bot.answer_callback_query(call.id, "Cancelled.")
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass


# ----------------- Withdraw requests (admin) -----------------
def _parse_withdraw_details(details: str) -> tuple[str, str]:
    method = ""
    account = ""
    if not details:
        return method, account
    for line in details.splitlines():
        line = line.strip()
        if line.lower().startswith("method:"):
            method = line.split(":", 1)[1].strip()
        elif line.lower().startswith("account:"):
            account = line.split(":", 1)[1].strip()
    return method, account


def handle_withdraw_requests(call):
    with db_lock:
        cur.execute(
            """
        SELECT wr.id, wr.amount, wr.details, wr.created_at, u.telegram_id
        FROM withdraw_requests wr
        JOIN users u ON wr.user_id = u.id
        WHERE wr.status = 'pending'
        ORDER BY wr.created_at ASC
        LIMIT 10
    """
        )
        rows = cur.fetchall()

    if not rows:
        bot.edit_message_text(
            "💸 <b>Withdraw Requests</b>\n\nNo pending requests.",
            call.message.chat.id,
            call.message.message_id,
        )
        return

    lines = []
    kb = InlineKeyboardMarkup()
    for rid, amount, details, created, tg_id in rows:
        method, account = _parse_withdraw_details(details or "")
        created_short = (created or "").split("T")[0] if created else ""
        lines.append(
            f"ID <code>{rid}</code> | User: <code>{tg_id}</code>\n"
            f"💳 Method: <b>{method or 'N/A'}</b>\n"
            f"🆔 Account: <code>{account or 'N/A'}</code>\n"
            f"💵 Amount: <code>{format_amount(amount)}$</code>\n"
            f"📅 {created_short}"
        )
        kb.row(
            InlineKeyboardButton(f"✅ Approve {rid}", callback_data=f"wdc_{rid}", api_kwargs={"style": "success"}),
            InlineKeyboardButton(f"❌ Reject {rid}", callback_data=f"wdr_{rid}", api_kwargs={"style": "danger"}),
        )

    bot.edit_message_text(
        "💸 <b>Pending Withdraw Requests</b>\n\n" + "\n\n".join(lines),
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
    )


def handle_withdraw_confirm(call, req_id: int):
    with db_lock:
        cur.execute(
            """
        SELECT wr.user_id, wr.amount, wr.status, u.telegram_id
        FROM withdraw_requests wr
        JOIN users u ON wr.user_id = u.id
        WHERE wr.id = ?
    """,
            (req_id,),
        )
        row = cur.fetchone()
        if not row:
            bot.answer_callback_query(
                call.id, "Request not found.", show_alert=True
            )
            return

        db_user_id, amount, status, user_tg_id = row
        amount = float(amount or 0.0)

        if status != "pending":
            bot.answer_callback_query(
                call.id, "Already processed.", show_alert=True
            )
            return

        cur.execute("SELECT balance FROM users WHERE id = ?", (db_user_id,))
        r2 = cur.fetchone()
        bal = float(r2[0]) if r2 and r2[0] is not None else 0.0
        if bal < amount:
            bot.answer_callback_query(
                call.id, "Insufficient balance.", show_alert=True
            )
            return

        cur.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ?",
            (amount, db_user_id),
        )
        cur.execute(
            "UPDATE withdraw_requests SET status = 'approved' WHERE id = ?",
            (req_id,),
        )
        conn.commit()

    snapshot_user_to_file(user_tg_id)

    bot.answer_callback_query(call.id, "Approved.")

    try:
        bot.send_message(
            user_tg_id,
            "✅ <b>Your withdraw request has been approved.</b>\n"
            f"💵 Amount: <code>{format_amount(amount)}$</code>",
        )
    except Exception:
        pass

    handle_withdraw_requests(call)


def handle_withdraw_reject(call, req_id: int):
    with db_lock:
        cur.execute(
            """
        SELECT wr.status, u.telegram_id
        FROM withdraw_requests wr
        JOIN users u ON wr.user_id = u.id
        WHERE wr.id = ?
    """,
            (req_id,),
        )
        row = cur.fetchone()
        if not row:
            bot.answer_callback_query(
                call.id, "Request not found.", show_alert=True
            )
            return

        status, user_tg_id = row
        if status != "pending":
            bot.answer_callback_query(
                call.id, "Already processed.", show_alert=True
            )
            return

        cur.execute(
            "UPDATE withdraw_requests SET status = 'rejected' WHERE id = ?",
            (req_id,),
        )
        conn.commit()

    bot.answer_callback_query(call.id, "Rejected.")

    try:
        bot.send_message(
            user_tg_id,
            "❌ <b>Your withdraw request was rejected.</b>\nYou can request again later.",
        )
    except Exception:
        pass

    handle_withdraw_requests(call)


# ----------------- OTP group handler -----------------
@bot.message_handler(
    func=lambda m: OTP_INPUT_GROUP_IDS and (m.chat.id in OTP_INPUT_GROUP_IDS),
    content_types=["text"],
)
def handle_otp_group_message(message):
    if not message.text:
        return

    text = message.text
    
    # Improved extraction for the specific format
    # Try to find number after "Number:" or "☎" or just standard regex
    num_match = re.search(r"(?:Number:|[☎☎️])\s*(\+?\d{7,15})", text)
    if num_match:
        numbers = [num_match.group(1)]
    else:
        numbers = re.findall(r"\+?\d{7,15}", text)

    processed = set()
    for raw in numbers:
        num = normalize_number(raw)
        if not num or num in processed:
            continue
        processed.add(num)

        with db_lock:
            # Find the latest user who used this number
            cur.execute(
                """
                SELECT used_by, batch_id
                FROM numbers
                WHERE value = ? AND used_by IS NOT NULL
                ORDER BY used_at DESC, id DESC
                LIMIT 1
                """,
                (num,),
            )
            row = cur.fetchone()
            if not row:
                continue

            user_tg_id, batch_id = row
            if not user_tg_id:
                continue

            cur.execute(
                "SELECT rate, flag FROM batches WHERE id = ?", (batch_id,)
            )
            row2 = cur.fetchone()
            rate = float(row2[0]) if row2 and row2[0] is not None else 0.0
            
            # Use flag from batch, or try to detect if missing
            batch_flag = row2[1] if row2 and row2[1] else ""
            if not batch_flag:
                info = detect_country_from_number(num)
                batch_flag = info["flag"] if info else "☎️"

            now = datetime.utcnow().isoformat()
            cur.execute(
                "INSERT INTO otps (number, user_tg_id, content, created_at) VALUES (?,?,?,?)",
                (num, user_tg_id, text, now),
            )

            # Reward is added for EVERY OTP
            cur.execute(
                "UPDATE users SET balance = balance + ? WHERE telegram_id = ?",
                (rate, user_tg_id),
            )
            conn.commit()

        snapshot_user_to_file(user_tg_id)

        # Extract bits for the specific requested format
        otp_code = extract_otp_from_text(text)
        
        # Parse full SMS content
        actual_sms = text
        if "💬 Full SMS" in text:
            try:
                parts = text.split("💬 Full SMS", 1)
                if len(parts) > 1:
                    actual_sms = parts[1].strip()
            except Exception:
                pass
        elif "Full SMS" in text:
             actual_sms = text.split("Full SMS", 1)[1].strip()

        # Build message exactly like the picture
        # 🇷🇺 Number: +79234603075
        # 🏆 Reward: 1$
        # 🔑 OTP: 852782
        # 💬 Full SMS
        # # 852782 is your Facebook code H29QFsn4Sr
        
        formatted_parts = [
            f"{batch_flag} Number: <code>{num}</code>",
            f"🏆 Reward: {format_amount(rate)}$"
        ]
        
        if otp_code:
            formatted_parts.append(f"🔑 OTP: <code>{otp_code}</code>")
        
        formatted_parts.append(f"💬 Full SMS\n{actual_sms}")
        formatted_text = "\n".join(formatted_parts)

        try:
            # Send to the user who used the number
            bot.send_message(user_tg_id, formatted_text)
            
            # Also send to the group and delete original
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except Exception:
                pass
            bot.send_message(message.chat.id, formatted_text)
            
            cleanup_caches()
        except Exception:
            pass


# ----------------- Document handler (add numbers from file) -----------------
@bot.message_handler(func=lambda m: m.chat.type == "private", content_types=["document"])
def handle_private_document(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    st = get_state(user_id)
    if st["state"] == "broadcast":
        return all_text_handler(message)

    if st["state"] != "addnum_numbers":
        bot.send_message(
            chat_id,
            "To import numbers from a file, open Admin Panel → Add Numbers first.",
        )
        return

    doc = message.document
    fname = (doc.file_name or "").lower()
    if not (fname.endswith(".txt") or fname.endswith(".csv")):
        bot.send_message(chat_id, "Upload a .txt or .csv file.")
        return

    try:
        file_info = bot.get_file(doc.file_id)
        downloaded = bot.download_file(file_info.file_path)
        try:
            content = downloaded.decode("utf-8")
        except UnicodeDecodeError:
            content = downloaded.decode("latin-1", errors="ignore")
    except Exception:
        bot.send_message(
            chat_id, "Failed to download/read the file. Try again."
        )
        return

    process_add_numbers_input(user_id, chat_id, content)


# ----------------- Private messages handler -----------------
@bot.message_handler(func=lambda m: m.chat.type == "private", content_types=["text", "photo", "audio", "video", "document", "voice", "animation", "sticker"])
def all_text_handler(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text_raw = message.text or message.caption or ""
    text = text_raw.strip()

    st = get_state(user_id)
    state = st["state"]
    data = st["data"]

    # withdraw: account/id
    if state == "wd_account":
        account = text
        if not account:
            bot.send_message(
                chat_id, "Account/ID cannot be empty. Send again."
            )
            return
        data["account"] = account
        set_state(user_id, "wd_amount", data)
        bal = get_user_balance(user_id)
        bot.send_message(
            chat_id,
            "Send the <b>amount</b> to withdraw.\n\n"
            f"Minimum: <code>{format_amount(MIN_WITHDRAW)}$</code>\n"
            f"Balance: <code>{format_amount(bal)}$</code>",
        )
        return

    # withdraw: amount
    if state == "wd_amount":
        try:
            amount = float(text)
        except Exception:
            bot.send_message(
                chat_id, "Send a valid amount. Example: <code>0.50</code>"
            )
            return

        bal = get_user_balance(user_id)
        if amount < MIN_WITHDRAW:
            bot.send_message(
                chat_id,
                f"Amount must be at least <code>{format_amount(MIN_WITHDRAW)}$</code>.",
            )
            return
        if amount > bal:
            bot.send_message(
                chat_id,
                f"You cannot withdraw more than your balance: <code>{format_amount(bal)}$</code>",
            )
            return

        data["amount"] = amount
        set_state(user_id, "wd_confirm", data)

        method = data.get("method", "N/A")
        account = data.get("account", "N/A")
        bot.send_message(
            chat_id,
            "Please confirm your withdraw request:\n\n"
            f"💳 Method: <b>{method}</b>\n"
            f"🆔 Account/ID: <code>{account}</code>\n"
            f"💵 Amount: <code>{format_amount(amount)}$</code>",
            reply_markup=build_confirm_keyboard(),
        )
        return

    # add numbers: list
    if state == "addnum_numbers":
        process_add_numbers_input(user_id, chat_id, text_raw)
        return

    # add numbers: short name
    if state == "addnum_short":
        short = text
        if not short:
            bot.send_message(chat_id, "Short name cannot be empty.")
            return
        if len(short) > 30:
            bot.send_message(
                chat_id, "Short name too long (max 30 characters)."
            )
            return
        data["short_name"] = short
        data["type_desc"] = short
        set_state(user_id, "addnum_per_user", data)
        bot.send_message(
            chat_id,
            "Step 3: How many numbers per user? Send an integer. Example: <code>1</code>",
        )
        return

    # add numbers: per_user
    if state == "addnum_per_user":
        try:
            per_user = int(text)
            if per_user <= 0:
                raise ValueError
        except Exception:
            bot.send_message(
                chat_id, "Send a valid positive integer."
            )
            return

        data["per_user"] = per_user
        set_state(user_id, "addnum_rate", data)
        bot.send_message(
            chat_id,
            "Step 4: Send rate per successful OTP (USD).\nExample: <code>0.0001</code>\n"
            "(NOTE: If you set 0, OTPs will not give any reward.)",
        )
        return

    # add numbers: rate
    if state == "addnum_rate":
        try:
            rate = float(text)
            if rate < 0:
                raise ValueError
        except Exception:
            bot.send_message(
                chat_id, "Send a valid non-negative number for rate."
            )
            return

        data["rate"] = rate
        now = datetime.utcnow().isoformat()

        with db_lock:
            cur.execute(
                """
                SELECT id FROM batches
                WHERE country = ? AND short_name = ? AND is_deleted = 0
                ORDER BY id ASC
                LIMIT 1
                """,
                (data["country"], data["short_name"]),
            )
            row = cur.fetchone()
            if row:
                batch_id = row[0]
                cur.execute(
                    """
                    UPDATE batches
                    SET category_id = ?, per_user = ?, flag = ?, type_desc = ?, rate = ?, created_at = ?
                    WHERE id = ?
                    """,
                    (
                        data.get("category_id"),
                        data["per_user"],
                        data["flag"],
                        data["type_desc"],
                        data["rate"],
                        now,
                        batch_id,
                    ),
                )
                existed = True
            else:
                cur.execute(
                    """
                    INSERT INTO batches (category_id, country, flag, type_desc, short_name, per_user, rate, created_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        data.get("category_id"),
                        data["country"],
                        data["flag"],
                        data["type_desc"],
                        data["short_name"],
                        data["per_user"],
                        data["rate"],
                        now,
                    ),
                )
                batch_id = cur.lastrowid
                existed = False

            cur.executemany(
                "INSERT INTO numbers (batch_id, value, is_used) VALUES (?,?,0)",
                [(batch_id, num) for num in data["numbers"]],
            )
            conn.commit()

        msg_prefix = (
            "✅ Updated existing category.\n\n"
            if existed
            else "✅ Added new category.\n\n"
        )
        
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("📢 Broadcast to Users", callback_data=f"brd_{batch_id}", api_kwargs={"style": "success"}),
            InlineKeyboardButton("❌ Skip", callback_data="brd_skip", api_kwargs={"style": "danger"})
        )

        bot.send_message(
            chat_id,
            msg_prefix
            + f"{data['flag'] or ''} <b>{data['country']}</b> (<code>{data['short_name']}</code>)\n"
            f"Per user: <code>{data['per_user']}</code>\n"
            f"Rate: <code>{format_amount(data['rate'])}$</code>\n"
            f"Total added now: <code>{len(data['numbers'])}</code>\n\n"
            "Do you want to broadcast this new stock to all users?",
            reply_markup=kb,
        )

        clear_state(user_id)
        return

    # broadcast
    if state == "broadcast":
        with db_lock:
            cur.execute("SELECT telegram_id FROM users")
            rows = cur.fetchall()
        sent = 0
        for (uid,) in rows:
            try:
                bot.copy_message(
                    chat_id=uid,
                    from_chat_id=chat_id,
                    message_id=message.message_id
                )
                sent += 1
                if sent % 25 == 0:
                    time.sleep(1)
            except Exception:
                continue
        bot.send_message(
            chat_id, f"✅ Broadcast finished. Sent to {sent} users."
        )
        clear_state(user_id)
        return

    # add channel flow
    if state == "add_ch_id":
        try:
            ch_id = int(text)
        except Exception:
            bot.send_message(chat_id, "Send a valid numeric Channel ID.")
            return
        data["channel_id"] = ch_id
        set_state(user_id, "add_ch_text", data)
        bot.send_message(chat_id, "Step 2: Send the <b>Button Text</b>.\nExample: <code>📢 Join Channel</code>")
        return

    if state == "add_ch_text":
        data["button_text"] = text
        set_state(user_id, "add_ch_link", data)
        bot.send_message(chat_id, "Step 3: Send the <b>Invite Link</b>.\nExample: <code>https://t.me/your_channel</code>")
        return

    if state == "add_ch_link":
        data["invite_link"] = text
        with db_lock:
            cur.execute(
                "INSERT INTO required_channels (channel_id, button_text, invite_link) VALUES (?, ?, ?)",
                (data["channel_id"], data["button_text"], data["invite_link"])
            )
            conn.commit()
        bot.send_message(chat_id, "✅ Channel added to required list!")
        clear_state(user_id)
        # return to admin panel
        send_admin_panel(chat_id, user_id)
        return

    # edit setting value
    if state == "edit_setting":
        key = data.get("key")
        if not key:
            bot.send_message(chat_id, "❌ Error: missing key.")
            clear_state(user_id)
            return
        
        set_config(key, text)
        bot.send_message(
            chat_id,
            f"✅ <b>Setting Updated!</b>\n\n"
            f"Key: <code>{key}</code>\n"
            f"New Value: <code>{text}</code>",
            reply_markup=build_main_menu_keyboard(user_id),
        )
        clear_state(user_id)
        # return to admin panel
        send_admin_panel(chat_id, user_id)
        return

    # add admin
    if state == "add_admin":
        try:
            new_admin_id = int(text)
        except Exception:
            bot.send_message(
                chat_id, "Send a valid numeric Telegram ID."
            )
            return
        if new_admin_id in OWNER_IDS:
            bot.send_message(chat_id, "This user is already an owner.")
            clear_state(user_id)
            return
        with db_lock:
            cur.execute(
                "INSERT OR REPLACE INTO admins (telegram_id, role) VALUES (?, 'admin')",
                (new_admin_id,),
            )
            conn.commit()
        bot.send_message(
            chat_id,
            f"✅ Added <code>{new_admin_id}</code> as admin.",
            reply_markup=build_main_menu_keyboard(user_id),
        )
        clear_state(user_id)
        return

    # add category name
    if state == "add_cat_name":
        if not text:
            bot.send_message(chat_id, "Category name cannot be empty.")
            return
        
        # Try to extract emoji if present at the start
        emoji = ""
        name = text
        m = re.match(r"(\W+)\s*(.*)", text)
        if m:
            # Simple check for emoji-like chars
            potential_emoji = m.group(1).strip()
            if potential_emoji:
                emoji = potential_emoji
                name = m.group(2).strip() or text
        
        with db_lock:
            try:
                cur.execute("INSERT INTO categories (name, emoji) VALUES (?, ?)", (name, emoji))
                conn.commit()
                bot.send_message(chat_id, f"✅ Category <b>{emoji} {name}</b> created!")
            except sqlite3.IntegrityError:
                bot.send_message(chat_id, "❌ This category already exists.")
        
        clear_state(user_id)
        return

    # main menu buttons
    if text == "📞 GET NUMBER":
        show_get_numbers_menu(chat_id, user_id, message_to_edit=None)
        return

    if text == "💰 BALANCE":
        bot.send_message(
            chat_id,
            build_balance_text(user_id),
            reply_markup=build_balance_keyboard(),
        )
        return

    if text == "👥 REFER AND EARN":
        bot.send_message(
            chat_id,
            build_refer_text(user_id),
            reply_markup=build_main_menu_keyboard(user_id),
        )
        return

    if text == "💬 SUPPORT":
        if OWNER_USERNAME:
            kb = InlineKeyboardMarkup()
            kb.row(InlineKeyboardButton("💬 Contact Support", url=f"https://t.me/{OWNER_USERNAME}", api_kwargs={"style": "primary"}))
            bot.send_message(
                chat_id,
                "💬 <b>Support</b>\n\nClick the button below to contact support.",
                reply_markup=kb,
            )
        else:
            bot.send_message(
                chat_id,
                "💬 <b>Support</b>\n\nSupport contact is not configured.",
                reply_markup=build_main_menu_keyboard(user_id),
            )
        return

    if text == "📊 STATUS":
        bot.send_message(
            chat_id,
            build_status_text(),
            reply_markup=build_main_menu_keyboard(user_id),
        )
        return

    if text == "🛠 ADMIN PANEL":
        send_admin_panel(chat_id, user_id)
        return

    # fallback
    bot.send_message(
        chat_id,
        "Use the menu buttons below.",
        reply_markup=build_main_menu_keyboard(user_id),
    )


# ----------------- Main -----------------
if __name__ == "__main__":
    print("Running file:", os.path.abspath(__file__))
    print("Using token:", mask_token(BOT_TOKEN), "len=", len(BOT_TOKEN))

    try:
        me = bot.get_me()
    except ApiTelegramException as e:
        raise SystemExit(
            "Telegram API returned 401 Unauthorized.\n"
            "Your BOT_TOKEN is invalid/revoked.\n\n"
            "Fix:\n"
            "1) Open @BotFather → /mybots → (your bot) → API Token\n"
            "2) Generate/Copy token again\n"
            "3) Paste ONLY raw token like: 123456789:AAHxxxxxx\n\n"
            f"Token used (masked): {mask_token(BOT_TOKEN)}\n"
            f"Original error: {e}"
        )

    BOT_USERNAME = me.username
    print(f"Bot started as @{BOT_USERNAME}")
    bot.infinity_polling(skip_pending=True, timeout=20)