"""
Football Prediction Bot — DASI BET v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Single-file architecture — GitHub / Replit / Render / Railway ready
"""

# ═══════════════════════════════════════════════════════════════
#  IMPORTS
# ═══════════════════════════════════════════════════════════════
import logging, os, json, hashlib, threading, time, uuid, re
import requests
from datetime import datetime, timedelta, time as dtime
from typing import Optional
from flask import Flask
from threading import Thread
from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, CommandHandler,
                           MessageHandler, CallbackQueryHandler,
                           filters, ContextTypes)

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "8652626453:AAEkAjJRPRb6DMUde_hSz5iPGSeMu3cojvg")
GROQ_API_KEY     = os.environ.get("GROQ_API_KEY",     "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")
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "5d44806d63094fdab0090cc5faef770c")
ODDS_API_KEY     = os.environ.get("ODDS_API_KEY",     "143a7046e5ab7a7a088be59cf2025c89")
TAVILY_API_KEY   = os.environ.get("TAVILY_API_KEY",   "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")

CHANNEL          = "@dasi_bet"
CHANNEL_URL      = "https://t.me/dasi_bet"
ADMIN_ID         = int(os.environ.get("ADMIN_ID",         "7046072164"))
ADMIN_USERNAME   = "@dasi_supportt"
BOT_USERNAME     = os.environ.get("BOT_USERNAME", "dasiibet_bot")

FREE_LIMIT       = 3
REFERRAL_GOAL    = 5
VIP_DAYS         = 30
POINTS_PER_VIP   = 100
# الموسم يُحسب تلقائياً: قبل أغسطس = سنة ماضية، بعده = سنة حالية
def _current_season():
    from datetime import datetime as _dt
    n = _dt.now()
    return str(n.year - 1) if n.month < 8 else str(n.year)
SEASON = _current_season()
PORT             = int(os.environ.get("PORT", "8080"))

DB_FILE          = "data/users.json"
CACHE_FILE       = "data/cache.json"
WELCOME_ID_FILE  = "data/welcome_file_id.txt"

# TTL بالدقائق لكل نوع كاش
TTL_MATCHES  = 60    # المباريات
TTL_ODDS     = 30    # الأود
TTL_ANALYSIS = 360   # التحليل (6 ساعات)
TTL_NEWS     = 120   # الأخبار
TTL_SAFE_BET = 360   # أضمن رهان

LEAGUES = {
    "PL":  {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 الإنجليزي",  "id": 2021, "odds_key": "soccer_epl"},
    "PD":  {"name": "🇪🇸 الإسباني",    "id": 2014, "odds_key": "soccer_spain_la_liga"},
    "BL1": {"name": "🇩🇪 الألماني",    "id": 2002, "odds_key": "soccer_germany_bundesliga"},
    "SA":  {"name": "🇮🇹 الإيطالي",    "id": 2019, "odds_key": "soccer_italy_serie_a"},
    "FL1": {"name": "🇫🇷 الفرنسي",     "id": 2015, "odds_key": "soccer_france_ligue_one"},
    "CL":  {"name": "🌍 أبطال أوروبا","id": 2001, "odds_key": "soccer_uefa_champs_league"},
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)
try:
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except Exception as _ge:
    logger.warning(f"Groq init failed: {_ge}")
    groq_client = None
_db_lock    = threading.Lock()
_cache_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════════
#  CACHE — TTL ذكي بالدقائق
# ═══════════════════════════════════════════════════════════════
def _ensure_dirs():
    os.makedirs("data", exist_ok=True)

def _load_cache() -> dict:
    _ensure_dirs()
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache(c: dict):
    _ensure_dirs()
    with _cache_lock:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CACHE_FILE)

def cache_key(*parts) -> str:
    raw = "|".join(str(p).lower().strip() for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()[:16]

def cache_get(key: str, ttl_minutes: int = TTL_ANALYSIS):
    c = _load_cache()
    if key not in c:
        return None
    try:
        t = datetime.strptime(c[key]["time"], "%Y-%m-%d %H:%M")
        if datetime.now() - t > timedelta(minutes=ttl_minutes):
            return None
        return c[key]["data"]
    except Exception:
        return None

def cache_set(key: str, data):
    c = _load_cache()
    c[key] = {"data": data, "time": datetime.now().strftime("%Y-%m-%d %H:%M")}
    # حذف أقدم 100 مفتاح إذا تجاوز 500
    if len(c) > 500:
        oldest = sorted(c.items(), key=lambda x: x[1]["time"])[:100]
        for k, _ in oldest:
            del c[k]
    _save_cache(c)

def cache_clear():
    _save_cache({})

# ═══════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════
def db_load() -> dict:
    _ensure_dirs()
    if not os.path.exists(DB_FILE):
        return {"users": {}, "total_requests": 0}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "total_requests": 0}

def db_save(db: dict):
    _ensure_dirs()
    with _db_lock:
        tmp = DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DB_FILE)

def db_user(db: dict, uid: int, update=None) -> dict:
    k = str(uid)
    if k not in db["users"]:
        db["users"][k] = {
            "name":               getattr(getattr(update, "effective_user", None), "full_name", ""),
            "username":           getattr(getattr(update, "effective_user", None), "username", ""),
            "joined":             datetime.now().strftime("%Y-%m-%d %H:%M"),
            "requests_today":     0,
            "bonus_requests":     0,
            "last_request_date":  "",
            "total_requests":     0,
            "vip":                False,
            "vip_expiry":         "",
            "blocked":            False,
            "points":             0,
            "referrals":          [],
            "referred_by":        "",
            "history":            [],
            "first_visit":        True,
        }
        db_save(db)
    return db["users"][k]

def is_vip(db: dict, uid: int) -> bool:
    if uid == ADMIN_ID:
        return True
    u = db_user(db, uid)
    if not u["vip"]:
        return False
    if u["vip_expiry"] and datetime.now().strftime("%Y-%m-%d") > u["vip_expiry"]:
        u["vip"] = False
        db_save(db)
        return False
    return True

def get_limit(db: dict, uid: int) -> int:
    return 9999 if is_vip(db, uid) else FREE_LIMIT + db_user(db, uid).get("bonus_requests", 0)

def has_quota(db: dict, uid: int) -> bool:
    if is_vip(db, uid):
        return True
    u     = db_user(db, uid)
    today = datetime.now().strftime("%Y-%m-%d")
    if u["last_request_date"] != today:
        u["requests_today"]    = 0
        u["last_request_date"] = today
        db_save(db)
    return u["requests_today"] < get_limit(db, uid)

def remaining(db: dict, uid: int):
    if is_vip(db, uid):
        return "♾️"
    u     = db_user(db, uid)
    today = datetime.now().strftime("%Y-%m-%d")
    used  = u["requests_today"] if u["last_request_date"] == today else 0
    return max(0, get_limit(db, uid) - used)

def consume(db: dict, uid: int, match: str):
    u     = db_user(db, uid)
    today = datetime.now().strftime("%Y-%m-%d")
    if u["last_request_date"] != today:
        u["requests_today"]    = 0
        u["last_request_date"] = today
    u["requests_today"]  += 1
    u["total_requests"]  += 1
    db["total_requests"]  = db.get("total_requests", 0) + 1
    u["history"].append({"match": match, "date": datetime.now().strftime("%Y-%m-%d %H:%M")})
    u["history"] = u["history"][-20:]
    _add_points(db, uid, 5)

def _add_points(db: dict, uid: int, pts: int) -> bool:
    u          = db_user(db, uid)
    u["points"] = u.get("points", 0) + pts
    if u["points"] >= POINTS_PER_VIP:
        u["points"]      -= POINTS_PER_VIP
        u["vip"]          = True
        u["vip_expiry"]   = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        db_save(db)
        return True
    db_save(db)
    return False

def activate_vip(db: dict, uid: int) -> str:
    u              = db_user(db, uid)
    u["vip"]        = True
    expiry          = (datetime.now() + timedelta(days=VIP_DAYS)).strftime("%Y-%m-%d")
    u["vip_expiry"] = expiry
    db_save(db)
    return expiry

def handle_referral(db: dict, new_uid: int, ref_id: str):
    if str(new_uid) == ref_id or ref_id not in db.get("users", {}):
        return
    ref = db_user(db, int(ref_id))
    if str(new_uid) in ref.get("referrals", []):
        return
    ref.setdefault("referrals", []).append(str(new_uid))
    db_user(db, new_uid)["referred_by"] = ref_id
    if len(ref["referrals"]) % REFERRAL_GOAL == 0:
        ref["bonus_requests"] = ref.get("bonus_requests", 0) + 1
    _add_points(db, int(ref_id), 10)
    db_save(db)

# ═══════════════════════════════════════════════════════════════
#  FOOTBALL-DATA API
# ═══════════════════════════════════════════════════════════════
_FAPI_BASE    = "https://api.football-data.org/v4"
_FAPI_HEADERS = {"X-Auth-Token": FOOTBALL_API_KEY}

def _fapi(endpoint: str, params: dict = None, retries: int = 2) -> Optional[dict]:
    for attempt in range(retries + 1):
        try:
            r = requests.get(
                f"{_FAPI_BASE}/{endpoint}",
                headers=_FAPI_HEADERS,
                params=params,
                timeout=12
            )
            if r.status_code == 200:
                return r.json()
            logger.warning(f"Football API {r.status_code}: {endpoint}")
        except requests.Timeout:
            logger.warning(f"Football API timeout ({attempt+1})")
        except Exception as e:
            logger.error(f"Football API error: {e}")
        if attempt < retries:
            time.sleep(1.5)
    return None

def get_matches(league_code: str, date: str) -> list:
    ck = cache_key("matches", league_code, date)
    cached = cache_get(ck, TTL_MATCHES)
    if cached:
        return cached
    lid = LEAGUES[league_code]["id"]
    # جرب مع season أولاً، ثم بدونها كـ fallback
    data = _fapi(f"competitions/{lid}/matches",
                 {"dateFrom": date, "dateTo": date, "season": SEASON})
    if not data or not data.get("matches"):
        logger.info(f"Retrying {league_code} without season param")
        data = _fapi(f"competitions/{lid}/matches",
                     {"dateFrom": date, "dateTo": date})
    if not data:
        return []
    result = []
    for m in data.get("matches", []):
        result.append({
            "home":    m["homeTeam"]["name"],
            "away":    m["awayTeam"]["name"],
            "time":    m["utcDate"][11:16],
            "league":  LEAGUES[league_code]["name"],
            "code":    league_code,
            "home_id": m["homeTeam"].get("id", 0),
            "away_id": m["awayTeam"].get("id", 0),
        })
    # لا تخزّن نتيجة فارغة في الكاش
    if result:
        cache_set(ck, result)
    return result

def get_all_matches(date: str) -> list:
    ck = cache_key("all_matches", date)
    cached = cache_get(ck, TTL_MATCHES)
    if cached:
        return cached
    all_m = []
    for code in LEAGUES:
        all_m.extend(get_matches(code, date))
    if all_m:
        cache_set(ck, all_m)
    return all_m

def get_team_form(team_id: int) -> dict:
    """جلب آخر 5 مباريات للفريق"""
    if not team_id:
        return {}
    ck = cache_key("form", team_id)
    cached = cache_get(ck, TTL_MATCHES)
    if cached:
        return cached
    data = _fapi(f"teams/{team_id}/matches",
                 {"status": "FINISHED", "limit": 5})
    if not data:
        return {}
    matches = data.get("matches", [])
    wins = draws = losses = goals_for = goals_against = 0
    results_str = []
    for m in matches:
        ht = m["score"]["fullTime"].get("home", 0) or 0
        at = m["score"]["fullTime"].get("away", 0) or 0
        is_home = m["homeTeam"].get("id") == team_id
        gf = ht if is_home else at
        ga = at if is_home else ht
        goals_for      += gf
        goals_against  += ga
        if gf > ga:
            wins += 1
            results_str.append("✅")
        elif gf == ga:
            draws += 1
            results_str.append("🟡")
        else:
            losses += 1
            results_str.append("❌")
    form = {
        "wins": wins, "draws": draws, "losses": losses,
        "goals_for": goals_for, "goals_against": goals_against,
        "played": len(matches),
        "results": " ".join(results_str[-5:]),
        "form_score": round((wins * 3 + draws) / max(len(matches) * 3, 1) * 100, 1)
    }
    cache_set(ck, form)
    return form

def get_standings(league_id: int) -> dict:
    """جلب ترتيب الدوري"""
    ck = cache_key("standings", league_id)
    cached = cache_get(ck, TTL_MATCHES)
    if cached:
        return cached
    data = _fapi(f"competitions/{league_id}/standings", {"season": SEASON})
    if not data:
        return {}
    standings = {}
    for table in data.get("standings", []):
        if table.get("type") == "TOTAL":
            for row in table.get("table", []):
                tid = row["team"].get("id")
                if tid:
                    standings[tid] = {
                        "position": row["position"],
                        "points":   row["points"],
                        "played":   row["playedGames"],
                        "won":      row["won"],
                        "draw":     row["draw"],
                        "lost":     row["lost"],
                        "gf":       row["goalsFor"],
                        "ga":       row["goalsAgainst"],
                    }
    cache_set(ck, standings)
    return standings

# ═══════════════════════════════════════════════════════════════
#  ODDS API (The Odds API — the-odds-api.com)
# ═══════════════════════════════════════════════════════════════
_ODDS_BASE = "https://api.the-odds-api.com/v4"

def get_real_odds(home: str, away: str, sport_key: str) -> dict:
    """جلب أود حقيقي — يستخدم كاش الدوري الكامل لتقليل API calls"""
    if not ODDS_API_KEY or not sport_key:
        return {}
    ck = cache_key("odds", home, away)
    cached = cache_get(ck, TTL_ODDS)
    if cached:
        return cached
    events = get_league_odds(sport_key)
    if not events:
        return {}
    result = _parse_odds(events, home, away)
    if not result:
        logger.info(f"No odds match for: {home} vs {away} in {sport_key}")
    else:
        cache_set(ck, result)
    return result or {}

# خريطة تبديل أسماء شائعة مختلفة بين Football-Data و Odds API
_NAME_MAP = {
    "manchester united fc":  ["manchester utd", "man united", "manchester united"],
    "manchester city fc":    ["manchester city", "man city"],
    "nottingham forest fc":  ["nottingham forest"],
    "tottenham hotspur fc":  ["tottenham", "spurs", "tottenham hotspur"],
    "newcastle united fc":   ["newcastle", "newcastle united"],
    "wolverhampton wanderers fc": ["wolverhampton", "wolves"],
    "brighton & hove albion fc":  ["brighton", "brighton & hove albion"],
    "west ham united fc":    ["west ham", "west ham united"],
    "aston villa fc":        ["aston villa"],
    "chelsea fc":            ["chelsea"],
    "arsenal fc":            ["arsenal"],
    "liverpool fc":          ["liverpool"],
    "everton fc":            ["everton"],
    "leicester city fc":     ["leicester"],
    "crystal palace fc":     ["crystal palace"],
    "brentford fc":          ["brentford"],
    "fulham fc":             ["fulham"],
    "bournemouth":           ["afc bournemouth"],
    "ipswich town fc":       ["ipswich"],
    "southampton fc":        ["southampton"],
}

def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()

def _name_tokens(name: str) -> set:
    """استخرج كلمات مفتاحية من اسم الفريق بعد تنظيفه"""
    clean = _normalize(name)
    # أزل كلمات شائعة غير مميزة
    for w in [" fc", " cf", " sc", " ac", " united", " city", " utd"]:
        clean = clean.replace(w, "")
    tokens = set(clean.split())
    tokens.discard("")
    return tokens

def _names_match(n1: str, n2: str) -> bool:
    """مطابقة مرنة بين اسمين لفريق"""
    n1l = n1.lower()
    n2l = n2.lower()
    # تطابق مباشر
    if n1l == n2l:
        return True
    # تطابق بعد تنظيف
    n1c = _normalize(n1)
    n2c = _normalize(n2)
    if n1c == n2c or n1c in n2c or n2c in n1c:
        return True
    # مطابقة من الخريطة
    for key, aliases in _NAME_MAP.items():
        names_set = {key} | set(aliases)
        if n1l in names_set and n2l in names_set:
            return True
        if any(a in n1l for a in aliases) and any(a in n2l for a in aliases):
            return True
    # مطابقة بالكلمات المفتاحية
    t1 = _name_tokens(n1)
    t2 = _name_tokens(n2)
    if t1 and t2 and len(t1 & t2) >= min(2, min(len(t1), len(t2))):
        return True
    return False

# كاش أود الدوري الكامل لتقليل استدعاءات API
_league_odds_cache: dict = {}

def get_league_odds(sport_key: str) -> list:
    """جلب أود دوري كامل مرة واحدة وتخزينه"""
    ck = cache_key("league_odds", sport_key)
    cached = cache_get(ck, TTL_ODDS)
    if cached:
        return cached
    if not ODDS_API_KEY or not sport_key:
        return []
    try:
        r = requests.get(
            f"{_ODDS_BASE}/sports/{sport_key}/odds",
            params={
                "apiKey":     ODDS_API_KEY,
                "regions":    "eu",
                "markets":    "h2h,totals,btts",
                "oddsFormat": "decimal",
            },
            timeout=12
        )
        if r.status_code == 200:
            data = r.json()
            cache_set(ck, data)
            return data
        logger.warning(f"Odds API {r.status_code} for {sport_key}: {r.text[:100]}")
    except Exception as e:
        logger.error(f"Odds API error: {e}")
    return []

def _parse_odds(events: list, home: str, away: str) -> dict:
    """استخرج الأود المناسب للمباراة بمطابقة مرنة"""
    for ev in events:
        eh = ev.get("home_team", "")
        ea = ev.get("away_team", "")
        if not _names_match(home, eh):
            continue
        if not _names_match(away, ea):
            continue
        odds = {"home_win": None, "draw": None, "away_win": None,
                "over_2_5": None, "under_2_5": None, "btts_yes": None, "btts_no": None}
        for bk in ev.get("bookmakers", [])[:3]:
            for mkt in bk.get("markets", []):
                if mkt["key"] == "h2h":
                    for o in mkt.get("outcomes", []):
                        n = o["name"].lower()
                        if "draw" in n:
                            odds["draw"] = odds["draw"] or round(o["price"], 2)
                        elif _normalize(o["name"]) in eh:
                            odds["home_win"] = odds["home_win"] or round(o["price"], 2)
                        else:
                            odds["away_win"] = odds["away_win"] or round(o["price"], 2)
                elif mkt["key"] == "totals":
                    for o in mkt.get("outcomes", []):
                        if "over" in o["name"].lower() and abs(o.get("point", 0) - 2.5) < 0.1:
                            odds["over_2_5"] = odds["over_2_5"] or round(o["price"], 2)
                        if "under" in o["name"].lower() and abs(o.get("point", 0) - 2.5) < 0.1:
                            odds["under_2_5"] = odds["under_2_5"] or round(o["price"], 2)
                elif mkt["key"] == "btts":
                    for o in mkt.get("outcomes", []):
                        if "yes" in o["name"].lower():
                            odds["btts_yes"] = odds["btts_yes"] or round(o["price"], 2)
                        if "no" in o["name"].lower():
                            odds["btts_no"] = odds["btts_no"] or round(o["price"], 2)
        return odds
    return {}

# ═══════════════════════════════════════════════════════════════
#  PREDICTION ENGINE — منطق رياضي حقيقي (بدون AI)
# ═══════════════════════════════════════════════════════════════
def calc_confidence(odd: Optional[float]) -> int:
    """حساب الثقة رياضياً من الأود الحقيقي"""
    if not odd or odd <= 1.0:
        return 60
    raw = round((1 / odd) * 100)
    return max(50, min(95, raw))

def _team_strength(form: dict, standing: dict, is_home: bool) -> float:
    """حساب قوة الفريق من 0 إلى 100"""
    score = 50.0
    # الشكل الأخير
    score += form.get("form_score", 50) * 0.3
    # ترتيب الدوري
    pos = standing.get("position", 10)
    score += max(0, (20 - pos)) * 1.2
    # نسبة الأهداف
    played = max(form.get("played", 1), 1)
    gf_avg = form.get("goals_for",     0) / played
    ga_avg = form.get("goals_against", 0) / played
    score += gf_avg * 3
    score -= ga_avg * 2
    # أفضلية الملعب
    if is_home:
        score += 6
    return round(min(100, max(0, score)), 1)

def predict_match(home: str, away: str,
                  home_id: int = 0, away_id: int = 0,
                  league_id: int = 0, odds: dict = None) -> dict:
    """
    توقع نتيجة المباراة بمنطق حقيقي — لا يعتمد على AI.
    يُرجع JSON واضح يُمرَّر لاحقاً إلى Groq للشرح فقط.
    """
    # جلب البيانات
    home_form     = get_team_form(home_id)    if home_id    else {}
    away_form     = get_team_form(away_id)    if away_id    else {}
    standings     = get_standings(league_id)  if league_id  else {}
    home_standing = standings.get(home_id, {})
    away_standing = standings.get(away_id, {})

    hs = _team_strength(home_form, home_standing, is_home=True)
    as_ = _team_strength(away_form, away_standing, is_home=False)

    # تحديد الفائز
    diff = hs - as_
    if diff > 8:
        winner     = home
        winner_odd = (odds or {}).get("home_win")
        result_key = "home_win"
    elif diff < -8:
        winner     = away
        winner_odd = (odds or {}).get("away_win")
        result_key = "away_win"
    else:
        winner     = "تعادل"
        winner_odd = (odds or {}).get("draw")
        result_key = "draw"

    # توقع النتيجة
    hgf = round(home_form.get("goals_for",  0) / max(home_form.get("played", 1), 1), 1)
    agf = round(away_form.get("goals_for",  0) / max(away_form.get("played", 1), 1), 1)
    home_goals = max(0, round(hgf * 0.85 + (0.3 if diff > 0 else 0)))
    away_goals = max(0, round(agf * 0.85 + (0.3 if diff < 0 else 0)))
    score_pred  = f"{home_goals}-{away_goals}"

    # confidence من الأود
    confidence = calc_confidence(winner_odd)

    # أفضل رهان
    best_bet  = winner if winner != "تعادل" else "تعادل"
    best_odd  = winner_odd

    # توقع أهداف
    avg_goals = hgf + agf
    over_line = 2.5 if avg_goals >= 2.5 else 1.5
    over_pred = "أوفر" if avg_goals >= over_line else "أندر"
    over_odd  = (odds or {}).get("over_2_5") if over_line == 2.5 else None

    # BTTS
    btts = home_form.get("goals_for", 0) > 0 and away_form.get("goals_for", 0) > 0
    btts_label = "نعم" if btts else "لا"
    btts_odd   = (odds or {}).get("btts_yes" if btts else "btts_no")

    # بيانات الشكل للنص
    home_results = home_form.get("results", "غير متاح")
    away_results = away_form.get("results", "غير متاح")
    home_pos     = home_standing.get("position", "—")
    away_pos     = away_standing.get("position", "—")

    return {
        "home":          home,
        "away":          away,
        "winner":        winner,
        "result_key":    result_key,
        "score":         score_pred,
        "best_bet":      best_bet,
        "best_odd":      best_odd,
        "confidence":    confidence,
        "home_strength": hs,
        "away_strength": as_,
        "over_line":     over_line,
        "over_pred":     over_pred,
        "over_odd":      over_odd,
        "btts":          btts_label,
        "btts_odd":      btts_odd,
        "home_results":  home_results,
        "away_results":  away_results,
        "home_position": home_pos,
        "away_position": away_pos,
        "home_form_score": home_form.get("form_score", 0),
        "away_form_score": away_form.get("form_score", 0),
        "home_gf_avg":   hgf,
        "away_gf_avg":   agf,
        "home_win_odd":  (odds or {}).get("home_win"),
        "draw_odd":      (odds or {}).get("draw"),
        "away_win_odd":  (odds or {}).get("away_win"),
    }

# ═══════════════════════════════════════════════════════════════
#  AI SERVICE — Groq للشرح فقط، ليس للقرار
# ═══════════════════════════════════════════════════════════════
SYSTEM_EXPLAINER = """أنت محلل كرة قدم متخصص. مهمتك الوحيدة هي شرح وتبرير التوقع المحدد مسبقاً.

قواعد صارمة لا تُخالَف:
1. اللغة العربية فقط في كل ردودك.
2. التوقع والأود والثقة يأتيانك جاهزَين — لا تغيّرهم أبداً.
3. الأسباب يجب أن تدعم الفريق الفائز المحدد فقط — ممنوع ذكر مزايا الفريق الخاسر.
4. ممنوع اختراع إصابات أو أخبار غير مذكورة في البيانات.
5. ممنوع اختراع أي أرقام أو إحصائيات غير موجودة في البيانات.
6. لا تذكر تحفظات أو تشكيك في التوقع.
7. ممنوع خلط اللغات — العربية فقط."""

def _groq_call(system: str, user: str, tokens: int = 600) -> str:
    if not groq_client:
        return "❌ خدمة الذكاء الاصطناعي غير مكوّنة."
    for attempt in range(3):
        try:
            r = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                max_tokens=tokens,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user}
                ]
            )
            return r.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Groq attempt {attempt+1}: {e}")
            time.sleep(2)
    return "❌ تعذّر الاتصال بالذكاء الاصطناعي، حاول لاحقاً."

def _news_search(t1: str, t2: str) -> str:
    if not TAVILY_API_KEY:
        return ""
    ck = cache_key("news", t1, t2)
    cached = cache_get(ck, TTL_NEWS)
    if cached:
        return cached
    try:
        from tavily import TavilyClient
        tc  = TavilyClient(api_key=TAVILY_API_KEY)
        res = tc.search(
            query=f"{t1} vs {t2} 2025 match preview injuries form",
            max_results=3, search_depth="basic"
        )
        text = " | ".join(r.get("content", "")[:150] for r in res.get("results", []))
        cache_set(ck, text)
        return text
    except Exception as e:
        logger.warning(f"Tavily: {e}")
        return ""

def build_analysis_message(pred: dict) -> str:
    """Python يبني الرسالة من JSON — لا يعتمد على AI"""
    def fmt_odd(o) -> str:
        return f"{o:.2f}" if o else "—"

    w    = pred["winner"]
    hwin = pred["home_win_odd"]
    draw = pred["draw_odd"]
    awin = pred["away_win_odd"]

    lines = [
        f"━━━━━━━━━━━━━━━━━━",
        f"⚽ *{pred['home']} vs {pred['away']}*",
        f"━━━━━━━━━━━━━━━━━━",
        f"🏆 *التوقع:* {w}" + (f" | الأود: {fmt_odd(pred['best_odd'])}" if pred['best_odd'] else ""),
        f"📊 *النتيجة المحتملة:* {pred['score']}",
        f"",
        f"💰 *الأود الكامل:*",
        f"  • فوز {pred['home']}: {fmt_odd(hwin)}",
        f"  • تعادل: {fmt_odd(draw)}",
        f"  • فوز {pred['away']}: {fmt_odd(awin)}",
        f"",
        f"⚽ *أهداف:* {pred['over_pred']} {pred['over_line']}.5" + (f" | الأود: {fmt_odd(pred['over_odd'])}" if pred['over_odd'] else ""),
        f"👥 *كلا الفريقين يسجلان:* {pred['btts']}" + (f" | الأود: {fmt_odd(pred['btts_odd'])}" if pred['btts_odd'] else ""),
        f"",
        f"💡 *أفضل رهان:* {pred['best_bet']}" + (f" | الأود: {fmt_odd(pred['best_odd'])}" if pred['best_odd'] else ""),
        f"📈 *الثقة:* {pred['confidence']}%",
        f"━━━━━━━━━━━━━━━━━━",
        f"⚠️ _للترفيه فقط_",
    ]
    return "\n".join(lines)

def generate_reasons(pred: dict) -> str:
    """Groq يشرح فقط — القرار من pred"""
    ck = cache_key("reasons", pred["home"], pred["away"])
    cached = cache_get(ck, TTL_ANALYSIS)
    if cached:
        return cached

    user_msg = f"""
التوقع الجاهز (لا تغيّره):
- المباراة: {pred['home']} vs {pred['away']}
- الفائز المتوقع: {pred['winner']}
- النتيجة: {pred['score']}
- ثقة: {pred['confidence']}%

بيانات حقيقية:
- شكل {pred['home']} (آخر 5): {pred['home_results']} | قوة: {pred['home_strength']}/100 | ترتيب: {pred['home_position']}
- شكل {pred['away']} (آخر 5): {pred['away_results']} | قوة: {pred['away_strength']}/100 | ترتيب: {pred['away_position']}
- متوسط أهداف {pred['home']}: {pred['home_gf_avg']} | متوسط أهداف {pred['away']}: {pred['away_gf_avg']}

اكتب تحليلاً مفصلاً (بالعربية فقط) بهذا الشكل الثابت:

🔍 *سبب التوقع — {pred['home']} vs {pred['away']}*
━━━━━━━━━━━━━━━━━━

📋 *آخر 5 مباريات:*
• {pred['home']}: {pred['home_results']}
• {pred['away']}: {pred['away_results']}

⚡ *أسباب التوقع بفوز {pred['winner']}:*
• [سبب 1 مبني على البيانات أعلاه]
• [سبب 2]
• [سبب 3]

📍 *عوامل إضافية:*
• الملعب: [ذكر أفضلية الملعب]
• الضغط النفسي: [من يحتاج النقاط أكثر]
━━━━━━━━━━━━━━━━━━
⚠️ للترفيه فقط
"""
    result = _groq_call(SYSTEM_EXPLAINER, user_msg, tokens=700)
    cache_set(ck, result)
    return result

def ai_safe_bet(matches: list) -> str:
    """أضمن رهان — يختار بالمنطق ثم يشرح بـ AI"""
    ck = cache_key("safe_bet", datetime.now().strftime("%Y-%m-%d"))
    cached = cache_get(ck, TTL_SAFE_BET)
    if cached:
        return cached

    # اختر المباراة ذات أعلى confidence
    best    = None
    best_c  = 0
    best_p  = None
    for m in matches[:12]:
        try:
            p = predict_match(m["home"], m["away"],
                              m.get("home_id", 0), m.get("away_id", 0))
            if p["confidence"] > best_c and p["best_odd"]:
                best_c = p["confidence"]
                best   = m
                best_p = p
        except Exception:
            pass

    if not best or not best_p:
        result = "😔 لا توجد مباريات كافية لتحديد أضمن رهان."
        cache_set(ck, result)
        return result

    def fmt(o): return f"{o:.2f}" if o else "—"

    result = (
        f"🔒 *أضمن رهان اليوم*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚽ *{best_p['home']} vs {best_p['away']}*\n"
        f"✅ *{best_p['best_bet']}* | الأود: {fmt(best_p['best_odd'])} | الثقة: {best_p['confidence']}%\n"
        f"📊 الأشكال: {best_p['home_results']} / {best_p['away_results']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ _للترفيه فقط_"
    )
    cache_set(ck, result)
    return result

def _pick_safe_bet(p: dict) -> tuple:
    """
    اختر أفضل رهان آمن للمباراة:
    - إذا كان هناك فائز واضح (أود <= 1.80) → فوز مباشر
    - إذا كان التعادل محتملاً → فرصة مزدوجة (فوز أو تعادل) بأود أقل
    - تجنب التعادل المنفرد دائماً
    يُرجع (bet_label, bet_odd, confidence)
    """
    hw = p.get("home_win_odd")
    aw = p.get("away_win_odd")
    dw = p.get("draw_odd")

    candidates = []

    # فوز مباشر للفريق الأقوى
    if hw and hw <= 1.85:
        conf = round((1/hw)*100)
        candidates.append((f"فوز {p['home']}", hw, conf))
    if aw and aw <= 1.85:
        conf = round((1/aw)*100)
        candidates.append((f"فوز {p['away']}", aw, conf))

    # فرصة مزدوجة (1X أو X2) إذا لم يكن هناك فائز واضح
    if hw and dw:
        dc_odd = round(hw * dw / (hw + dw - 1), 2) if (hw + dw - 1) > 0 else None
        # تقريب حقيقي للـ double chance: 1/(1/hw + 1/dw)
        dc_odd = round(1 / (1/hw + 1/dw), 2) if hw and dw else None
        if dc_odd and dc_odd <= 1.50:
            conf = round((1/dc_odd)*100)
            candidates.append((f"{p['home']} أو تعادل (1X)", dc_odd, conf))
    if aw and dw:
        dc_odd = round(1 / (1/aw + 1/dw), 2) if aw and dw else None
        if dc_odd and dc_odd <= 1.50:
            conf = round((1/dc_odd)*100)
            candidates.append((f"{p['away']} أو تعادل (X2)", dc_odd, conf))

    if not candidates:
        return None, None, 0

    # اختر الأعلى ثقة
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[0]

def ai_coupon(target_odd: float, matches_today: list, matches_tomorrow: list = None) -> str:
    """
    قسيمة ذهبية:
    - تجمع مباريات اليوم + الغد
    - تختار فقط الرهانات الآمنة (أود حقيقي <= 1.85)
    - لا تعادل منفرد — فرصة مزدوجة بدلاً منه
    - تصل للأود المطلوب بدقة
    """
    ck = cache_key("coupon", str(target_odd), datetime.now().strftime("%Y-%m-%d"))
    cached = cache_get(ck, TTL_SAFE_BET)
    if cached:
        return cached

    all_matches = list(matches_today or [])
    if matches_tomorrow:
        all_matches.extend(matches_tomorrow)

    # احسب التوقع لكل مباراة واختر الرهان الآمن
    # جلب أود كل دوري مرة واحدة فقط
    league_events_cache: dict = {}
    candidates = []
    for m in all_matches[:30]:
        try:
            league_id, odds_key = _get_league_info(m.get("code", "PL"))
            odds = {}
            if ODDS_API_KEY and odds_key:
                if odds_key not in league_events_cache:
                    league_events_cache[odds_key] = get_league_odds(odds_key)
                events = league_events_cache[odds_key]
                odds = _parse_odds(events, m["home"], m["away"]) if events else {}
            p = predict_match(m["home"], m["away"],
                              m.get("home_id", 0), m.get("away_id", 0),
                              league_id, odds)
            bet_label, bet_odd, conf = _pick_safe_bet(p)
            day_label_str = "📅 اليوم" if m in matches_today else "📆 الغد"
            if bet_label and bet_odd and conf >= 60:
                candidates.append({
                    "home": m["home"], "away": m["away"],
                    "bet":  bet_label, "odd":  bet_odd,
                    "conf": conf,      "day":  day_label_str,
                    "league": m.get("league", ""),
                })
        except Exception as e:
            logger.warning(f"Coupon predict error: {e}")

    if not candidates:
        return "😔 لا توجد مباريات آمنة كافية لبناء القسيمة حالياً."

    # رتب من الأعلى ثقة
    candidates.sort(key=lambda x: x["conf"], reverse=True)

    # اختر رهانات تصل للأود المطلوب
    selected    = []
    current_odd = 1.0
    for c in candidates:
        if len(selected) >= 10:
            break
        new_odd = round(current_odd * c["odd"], 2)
        if new_odd <= target_odd * 1.30:
            selected.append(c)
            current_odd = new_odd
        if current_odd >= target_odd * 0.85:
            break

    if not selected:
        # أضف أفضل 4 على الأقل حتى لو لم تبلغ الهدف
        selected    = candidates[:4]
        current_odd = 1.0
        for c in selected:
            current_odd = round(current_odd * c["odd"], 2)

    actual_odd = round(current_odd, 2)
    lines_out = []
    for i, c in enumerate(selected, 1):
        row = (
            str(i) + ". " + c["day"] + " | *" + c["home"] + " vs " + c["away"] + "*\n"
            "   \u2705 " + c["bet"] + " | \U0001f4b0 " + str(round(c["odd"], 2)) +
            " | \U0001f4c8 " + str(c["conf"]) + "%"
        )
        lines_out.append(row)

    sep = "\n"
    result = (
        "\U0001f3ab *\u0627\u0644\u0642\u0633\u064a\u0645\u0629 \u0627\u0644\u0630\u0647\u0628\u064a\u0629*\n"
        + "\U0001f3af \u0627\u0644\u0623\u0648\u062f \u0627\u0644\u0645\u0637\u0644\u0648\u0628: *" + str(target_odd) + "*\n"
        + "\u2501" * 18 + "\n"
        + sep.join(lines_out)
        + "\n" + "\u2501" * 18 + "\n"
        + "\U0001f4b0 \u0627\u0644\u0623\u0648\u062f \u0627\u0644\u0641\u0639\u0644\u064a: *" + str(actual_odd) + "x*\n"
        + "\U0001f4ca \u0627\u062d\u062a\u0645\u0627\u0644 \u0627\u0644\u0646\u062c\u0627\u062d: *" + str(min(95, round(100/actual_odd))) + "%*\n"
        + "\u26a0\ufe0f _\u0644\u0644\u062a\u0631\u0641\u064a\u0647 \u0641\u0642\u0637_"
    )
    cache_set(ck, result)
    return result
# ═══════════════════════════════════════════════════════════════
#  MATCH STORE — حل مشكلة callback_data المقطوعة
# ═══════════════════════════════════════════════════════════════
def store_match(context: ContextTypes.DEFAULT_TYPE, match: dict) -> str:
    """خزّن المباراة كاملة في user_data وأرجع UUID قصير"""
    mid = uuid.uuid4().hex[:8]
    context.user_data.setdefault("matches", {})[mid] = match
    return mid

def retrieve_match(context: ContextTypes.DEFAULT_TYPE, mid: str) -> Optional[dict]:
    return context.user_data.get("matches", {}).get(mid)

# ═══════════════════════════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════════════════════════
def kb_main(vip: bool):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 مباريات اليوم",  callback_data="leagues_today"),
         InlineKeyboardButton("📆 مباريات الغد",   callback_data="leagues_tomorrow")],
        [InlineKeyboardButton("🔒 أضمن رهان",      callback_data="safe_bet"),
         InlineKeyboardButton("⚽ توقع مباراة",    callback_data="predict")],
        [InlineKeyboardButton("🎫 قسيمة ذهبية",   callback_data="coupon"),
         InlineKeyboardButton("👥 أحل صديقاً",     callback_data="referral")],
        [InlineKeyboardButton("📊 إحصائياتي",      callback_data="my_stats"),
         InlineKeyboardButton("💎 VIP نشط ✅" if vip else "💎 VIP $5/شهر",
                              callback_data="my_stats" if vip else "vip_info")],
    ])

def kb_leagues(day: str):
    rows  = []
    items = list(LEAGUES.items())
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(items[i][1]["name"],
                                    callback_data=f"league_{items[i][0]}_{day}")]
        if i + 1 < len(items):
            row.append(InlineKeyboardButton(items[i+1][1]["name"],
                                            callback_data=f"league_{items[i+1][0]}_{day}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def kb_matches(match_list: list, context: ContextTypes.DEFAULT_TYPE, code: str, day: str):
    """أزرار المباريات — UUID بدل اسم مقطوع"""
    rows = []
    for m in match_list[:10]:
        mid = store_match(context, m)
        rows.append([InlineKeyboardButton(
            f"⚽ {m['home']} vs {m['away']}  🕐{m['time']}",
            callback_data=f"match_{mid}"
        )])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"leagues_{day}")])
    return InlineKeyboardMarkup(rows)

def kb_after_analysis(mid: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 سبب التوقع",   callback_data=f"reason_{mid}")],
        [InlineKeyboardButton("✍️ تقييم التوقع", callback_data="write_review")],
        [InlineKeyboardButton("🔙 الرئيسية",     callback_data="back_main")],
    ])

def kb_vip():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 اشترك $5/شهر", callback_data="pay_vip")],
        [InlineKeyboardButton("🔙 رجوع",          callback_data="back_main")],
    ])

def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]])

# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════
def ref_link(uid: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"

def day_date(day: str) -> str:
    return ((datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            if day == "tomorrow" else datetime.now().strftime("%Y-%m-%d"))

def day_label(day: str) -> str:
    return "الغد 📆" if day == "tomorrow" else "اليوم 📅"

def escape_md(text: str) -> str:
    """تنظيف نص Markdown لتجنب أخطاء Telegram"""
    for ch in r"_[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text

async def safe_send(msg, text: str, **kw):
    try:
        await msg.reply_text(text, parse_mode="Markdown", **kw)
    except Exception:
        try:
            await msg.reply_text(text[:4000], **kw)
        except Exception as e:
            logger.error(f"safe_send: {e}")

async def safe_edit(query, text: str, **kw):
    try:
        await query.edit_message_text(text[:4000], parse_mode="Markdown", **kw)
    except Exception:
        try:
            await query.edit_message_text(text[:4000], **kw)
        except Exception as e:
            logger.error(f"safe_edit: {e}")

async def _send_welcome_photo(msg):
    caption = (
        f"👑 *أهلاً بك في DASI BET!*\n\n"
        f"🏆 بوت التوقعات الرياضية الاحترافي\n"
        f"تحليلات حقيقية • أود واقعي • توقعات دقيقة 🚀\n\n"
        f"📢 *اشترك في قناتنا للحصول على أفضل التوقعات يومياً:*\n"
        f"{CHANNEL_URL}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 اشترك في القناة", url=CHANNEL_URL)]])
    try:
        if os.path.exists(WELCOME_ID_FILE):
            with open(WELCOME_ID_FILE, "r") as f:
                fid = f.read().strip()
            if fid:
                await msg.reply_photo(photo=fid, caption=caption,
                                      parse_mode="Markdown", reply_markup=kb)
                return
        if os.path.exists("welcome.png"):
            with open("welcome.png", "rb") as img:
                sent = await msg.reply_photo(photo=img, caption=caption,
                                             parse_mode="Markdown", reply_markup=kb)
            _ensure_dirs()
            with open(WELCOME_ID_FILE, "w") as f:
                f.write(sent.photo[-1].file_id)
        else:
            await safe_send(msg, caption, reply_markup=kb)
    except Exception as e:
        logger.warning(f"Welcome photo: {e}")
        await safe_send(msg, caption, reply_markup=kb)

async def _send_home_menu(msg, uid: int, db: dict):
    u      = db_user(db, uid)
    badge  = "💎 VIP" if is_vip(db, uid) else "🆓 مجاني"
    rem    = remaining(db, uid)
    points = u.get("points", 0)
    name   = getattr(getattr(msg, "chat", None), "first_name", "")
    await safe_send(msg,
        f"👑 *DASI BET — {name}*\n\n"
        f"🏷️ {badge} | 🎯 متبقي: *{rem}* | ⭐ {points}/100\n\n"
        f"اختر من القائمة 👇",
        reply_markup=kb_main(is_vip(db, uid))
    )

def _get_league_info(code: str) -> tuple:
    """أرجع (league_id, odds_key) لدوري معيّن"""
    lg = LEAGUES.get(code, {})
    return lg.get("id", 0), lg.get("odds_key", "")

async def _run_prediction(home: str, away: str,
                           home_id: int, away_id: int,
                           league_code: str) -> tuple:
    """تشغيل التوقع الكامل في thread منفصل حتى لا يتجمد البوت"""
    import asyncio
    loop = asyncio.get_event_loop()

    def _blocking():
        league_id, odds_key = _get_league_info(league_code)
        odds = {}
        if ODDS_API_KEY and odds_key:
            events = get_league_odds(odds_key)
            odds   = _parse_odds(events, home, away) if events else {}
            if not odds:
                logger.info(f"No odds found for {home} vs {away}")
        pred = predict_match(home, away, home_id, away_id, league_id, odds)
        text = build_analysis_message(pred)
        return pred, text

    pred, text = await loop.run_in_executor(None, _blocking)
    return pred, text

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — USER
# ═══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db  = db_load()
    uid = update.effective_user.id
    u   = db_user(db, uid, update)
    if context.args and context.args[0].startswith("ref_"):
        handle_referral(db, uid, context.args[0][4:])
    if u.get("first_visit", True):
        u["first_visit"] = False
        db_save(db)
        await _send_welcome_photo(update.message)
    await _send_home_menu(update.message, uid, db)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db   = db_load()
    uid  = update.effective_user.id
    text = update.message.text.strip()

    u = db_user(db, uid, update)
    if u.get("blocked"):
        return

    mode = context.user_data.pop("mode", None) or "predict"

    # وضع التقييم
    if mode == "review":
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"⭐ *تقييم جديد*\n\n👤 {u.get('name','?')} | ID: `{uid}`\n\n💬 {text}",
                parse_mode="Markdown"
            )
        except Exception:
            pass
        await safe_send(update.message, "✅ شكراً! تم إرسال تقييمك للإدارة 🙏")
        return

    # وضع القسيمة
    if mode == "coupon":
        try:
            target = float(text.replace(",", "."))
            if target < 1.5 or target > 100:
                raise ValueError
        except ValueError:
            await safe_send(update.message,
                            "❌ أرسل رقماً بين 1.5 و 100، مثال: `5.00`")
            context.user_data["mode"] = "coupon"
            return
        wait = await update.message.reply_text("🎫 جاري بناء القسيمة من مباريات اليوم والغد...")
        try:
            today    = datetime.now().strftime("%Y-%m-%d")
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            matches_today    = get_all_matches(today)
            matches_tomorrow = get_all_matches(tomorrow)
            if not matches_today and not matches_tomorrow:
                await wait.edit_text("😔 لا توجد مباريات كافية اليوم أو الغد.")
                return
            import asyncio
            result = await asyncio.get_event_loop().run_in_executor(
                None, ai_coupon, target, matches_today, matches_tomorrow)
            await wait.delete()
            await safe_send(update.message, result)
        except Exception as e:
            logger.error(f"Coupon error: {e}")
            try:
                await wait.edit_text("❌ حدث خطأ في بناء القسيمة، حاول مرة أخرى.")
            except Exception:
                pass
        return

    # وضع التوقع
    if not has_quota(db, uid):
        link = ref_link(uid)
        await safe_send(update.message,
            f"⛔ *انتهت توقعاتك اليوم!*\n\n"
            f"🆓 شارك رابطك — كل {REFERRAL_GOAL} أصدقاء = توقع مجاني\n`{link}`\n\n"
            f"💎 أو اشترك VIP بـ $5/شهر",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 اشترك VIP",    callback_data="vip_info")],
                [InlineKeyboardButton("👥 رابط الإحالة", callback_data="referral")],
            ])
        )
        return

    # التحقق من صيغة الإدخال
    has_vs  = " vs "  in text.lower()
    has_dad = " ضد "  in text
    if len(text) < 3 or (not has_vs and not has_dad):
        await safe_send(update.message,
                        "⚽ أرسل المباراة بصيغة:\n`ريال مدريد vs برشلونة`\nأو: `ريال مدريد ضد برشلونة`")
        return

    wait = await update.message.reply_text("🔍 جاري التحليل...")
    try:
        # تقسيم اسم المباراة
        if has_vs:
            idx   = text.lower().index(" vs ")
            home  = text[:idx].strip()
            away  = text[idx+4:].strip()
        else:
            parts = text.split(" ضد ", 1)
            home  = parts[0].strip()
            away  = parts[1].strip() if len(parts) > 1 else text

        pred, analysis = await _run_prediction(home, away, 0, 0, "PL")
        consume(db, uid, text)
        db_save(db)

        # خزّن التوقع كاملاً
        mid = store_match(context, {
            "home": home, "away": away,
            "home_id": 0, "away_id": 0,
            "code": "PL", "pred": pred
        })
        context.user_data[f"pred_{mid}"] = pred

        await wait.delete()
        await safe_send(update.message, analysis)
        rem = remaining(db, uid)
        await update.message.reply_text(
            f"🎯 متبقي: *{rem}*",
            parse_mode="Markdown",
            reply_markup=kb_after_analysis(mid)
        )
    except Exception as e:
        logger.error(f"handle_message predict: {e}")
        await wait.edit_text("❌ حدث خطأ، حاول مرة أخرى.")

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — CALLBACKS
# ═══════════════════════════════════════════════════════════════
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer()
    db  = db_load()
    uid = q.from_user.id
    d   = q.data

    # ── سبب التوقع ──────────────────────────────────────────────
    if d.startswith("reason_"):
        mid  = d[7:]
        match_data = retrieve_match(context, mid)
        pred       = context.user_data.get(f"pred_{mid}")
        if not pred and match_data:
            pred = match_data.get("pred")
        if not pred:
            await q.edit_message_text("❌ انتهت صلاحية البيانات، أعد التحليل.")
            return
        await q.edit_message_text("🔍 جاري إعداد التحليل المفصل...")
        try:
            import asyncio
            result = await asyncio.get_event_loop().run_in_executor(
                None, generate_reasons, pred)
            await safe_edit(q, result, reply_markup=kb_back())
        except Exception as e:
            logger.error(e)
            await q.edit_message_text("❌ حدث خطأ، حاول مرة أخرى.")
        return

    # ── قائمة الدوريات ───────────────────────────────────────────
    if d in ("leagues_today", "leagues_tomorrow"):
        day = "today" if d == "leagues_today" else "tomorrow"
        await safe_edit(q, f"🏆 *اختر الدوري — {day_label(day)}:*",
                        reply_markup=kb_leagues(day))

    elif d.startswith("league_"):
        parts = d.split("_")
        code  = parts[1]
        day   = parts[2] if len(parts) > 2 else "today"
        if code not in LEAGUES:
            await q.edit_message_text("❌ دوري غير معروف.")
            return
        name = LEAGUES[code]["name"]
        await q.edit_message_text(f"⏳ جاري جلب مباريات {name}...")
        date    = day_date(day)
        matches = get_matches(code, date)
        if not matches:
            await safe_edit(q, f"😔 لا توجد مباريات في {name} {day_label(day)}.",
                            reply_markup=kb_back())
        else:
            kb = kb_matches(matches, context, code, day)
            await safe_edit(q,
                f"📅 *{name} — {day_label(day)}*\n\nاضغط مباراة للتحليل 👇",
                reply_markup=kb)

    # ── مباراة محددة ─────────────────────────────────────────────
    elif d.startswith("match_"):
        mid        = d[6:]
        match_data = retrieve_match(context, mid)
        if not match_data:
            await q.edit_message_text("❌ انتهت صلاحية البيانات، ارجع واضغط الدوري مجدداً.")
            return
        if not has_quota(db, uid):
            await safe_edit(q, "⛔ *انتهت توقعاتك اليوم!*\n\n💎 اشترك VIP.",
                            reply_markup=kb_vip())
            return

        home     = match_data["home"]
        away     = match_data["away"]
        home_id  = match_data.get("home_id", 0)
        away_id  = match_data.get("away_id", 0)
        code     = match_data.get("code", "PL")

        await q.edit_message_text(f"🔍 جاري تحليل {home} vs {away}...")
        try:
            # جلب league code من المباراة
            league_code = match_data.get("code", "PL")
            pred, analysis = await _run_prediction(home, away, home_id, away_id, league_code)
            consume(db, uid, f"{home} vs {away}")
            db_save(db)

            # خزّن التوقع
            context.user_data[f"pred_{mid}"] = pred
            match_data["pred"] = pred

            await safe_edit(q, analysis)
            rem = remaining(db, uid)
            await context.bot.send_message(
                q.message.chat_id,
                f"🎯 متبقي: *{rem}*",
                parse_mode="Markdown",
                reply_markup=kb_after_analysis(mid)
            )
        except Exception as e:
            logger.error(f"match callback: {e}")
            await q.edit_message_text("❌ حدث خطأ، حاول مرة أخرى.")

    # ── أضمن رهان ────────────────────────────────────────────────
    elif d == "safe_bet":
        await q.edit_message_text("🔍 جاري البحث عن أضمن رهان اليوم...")
        matches = get_all_matches(datetime.now().strftime("%Y-%m-%d"))
        if not matches:
            await safe_edit(q, "😔 لا توجد مباريات كافية اليوم.", reply_markup=kb_back())
            return
        try:
            import asyncio
            result = await asyncio.get_event_loop().run_in_executor(
                None, ai_safe_bet, matches)
            await safe_edit(q, result, reply_markup=kb_back())
        except Exception as e:
            logger.error(e)
            await q.edit_message_text("❌ حدث خطأ، حاول مرة أخرى.")

    # ── توقع يدوي ─────────────────────────────────────────────────
    elif d == "predict":
        context.user_data["mode"] = "predict"
        await safe_edit(q,
            "⚽ *أرسل اسم المباراة:*\n\n"
            "مثال: `ريال مدريد vs برشلونة`\n"
            "أو: `Manchester City vs Arsenal`")

    # ── قسيمة ذهبية ──────────────────────────────────────────────
    elif d == "coupon":
        if not is_vip(db, uid):
            await safe_edit(q,
                "🔒 *القسيمة الذهبية للـ VIP فقط!*\n\n"
                "💎 اشترك بـ $5/شهر للحصول على قسيمة بالأود الذي تريده!",
                reply_markup=kb_vip())
            return
        context.user_data["mode"] = "coupon"
        await safe_edit(q,
            "🎫 *القسيمة الذهبية*\n\n"
            "أرسل الأود الإجمالي الذي تريده:\n\n"
            "مثال: `5.00` أو `10.00` أو `20.00`\n\n"
            "سأختار مباريات مختلفة للوصول لهذا الأود 🎯")

    # ── تقييم ─────────────────────────────────────────────────────
    elif d == "write_review":
        context.user_data["mode"] = "review"
        await safe_edit(q,
            "✍️ *أرسل تقييمك الآن:*\n\n"
            "اكتب رأيك أو أي خطأ لاحظته — سيصل مباشرة للإدارة 📩")

    # ── إحالة ─────────────────────────────────────────────────────
    elif d == "referral":
        u      = db_user(db, uid, q)
        refs   = len(u.get("referrals", []))
        next_b = REFERRAL_GOAL - (refs % REFERRAL_GOAL)
        link   = ref_link(uid)
        await safe_edit(q,
            f"👥 *نظام الإحالة*\n\n"
            f"🔗 رابطك:\n`{link}`\n\n"
            f"📊 إحالاتك: *{refs}* | تحتاج *{next_b}* للتوقع التالي\n"
            f"⭐ كل إحالة = 10 نقاط | كل {REFERRAL_GOAL} إحالات = توقع مجاني",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 شارك الرابط",
                    url=f"https://t.me/share/url?url={link}&text=🏆+أفضل+بوت+توقعات!")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")],
            ])
        )

    # ── إحصائياتي ─────────────────────────────────────────────────
    elif d == "my_stats":
        u     = db_user(db, uid, q)
        badge = "💎 VIP" if is_vip(db, uid) else "🆓 مجاني"
        await safe_edit(q,
            f"📊 *إحصائياتك:*\n\n"
            f"🏷️ {badge}\n"
            f"🎯 متبقي اليوم: {remaining(db, uid)}\n"
            f"📈 إجمالي طلباتك: {u['total_requests']}\n"
            f"👥 إحالاتك: {len(u.get('referrals',[]))}\n"
            f"⭐ نقاطك: {u.get('points',0)}/100\n"
            f"🎁 توقعات مكسوبة: {u.get('bonus_requests',0)}\n"
            f"📅 انضمت: {u['joined']}",
            reply_markup=kb_back())

    # ── VIP معلومات ────────────────────────────────────────────────
    elif d == "vip_info":
        await safe_edit(q,
            f"💎 *VIP — $5/شهر*\n\n"
            "✅ توقعات غير محدودة\n"
            "✅ القسيمة الذهبية بأود مخصص\n"
            "✅ أضمن رهان يومي\n"
            "✅ مباريات اليوم والغد\n"
            "✅ زر سبب التوقع المفصل\n\n"
            f"للاشتراك تواصل مع: {ADMIN_USERNAME}",
            reply_markup=kb_vip())

    elif d == "pay_vip":
        await safe_edit(q,
            f"💳 *للاشتراك VIP:*\n\n"
            f"👤 {ADMIN_USERNAME}\n"
            "💰 $5/شهر | ⚡ تفعيل فوري\n\n"
            "طرق الدفع:\n• USDT (TRC20)\n• PayPal\n• تحويل بنكي")

    # ── رجوع ──────────────────────────────────────────────────────
    elif d == "back_main":
        u      = db_user(db, uid)
        badge  = "💎 VIP" if is_vip(db, uid) else "🆓 مجاني"
        rem    = remaining(db, uid)
        points = u.get("points", 0)
        name   = q.from_user.first_name
        await safe_edit(q,
            f"👑 *DASI BET — {name}*\n\n"
            f"🏷️ {badge} | 🎯 متبقي: *{rem}* | ⭐ {points}/100\n\n"
            f"اختر من القائمة 👇",
            reply_markup=kb_main(is_vip(db, uid)))

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — ADMIN
# ═══════════════════════════════════════════════════════════════
def admin_only(fn):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            return
        await fn(update, context)
    return wrapper

@admin_only
async def cmd_admin(update, context):
    db    = db_load()
    today = datetime.now().strftime("%Y-%m-%d")
    total  = len(db["users"])
    vip_c  = sum(1 for u in db["users"].values() if u.get("vip"))
    active = sum(1 for u in db["users"].values() if u.get("last_request_date") == today)
    await update.message.reply_text(
        f"👑 *لوحة التحكم*\n\n"
        f"👥 {total} | 💎 {vip_c} VIP | 🟢 {active} اليوم\n\n"
        f"`/vip [ID]` — تفعيل VIP\n"
        f"`/unvip [ID]` — إلغاء VIP\n"
        f"`/ban [ID]` — حظر\n"
        f"`/unban [ID]` — فك حظر\n"
        f"`/broadcast [رسالة]` — رسالة جماعية\n"
        f"`/users` — آخر 20 مستخدم\n"
        f"`/stats` — إحصائيات\n"
        f"`/clearcache` — مسح الكاش\n"
        f"`/resetwelcome` — إعادة الصورة الترحيبية",
        parse_mode="Markdown")

@admin_only
async def cmd_vip(update, context):
    if not context.args:
        await update.message.reply_text("استخدام: /vip [ID]")
        return
    db  = db_load()
    uid = context.args[0]
    if uid not in db["users"]:
        await update.message.reply_text("❌ المستخدم غير موجود")
        return
    expiry = activate_vip(db, int(uid))
    await update.message.reply_text(
        f"✅ VIP مفعّل لـ `{uid}` حتى {expiry}", parse_mode="Markdown")
    try:
        await context.bot.send_message(
            int(uid), "🎉 *تم تفعيل VIP!*\n\nاضغط /start 🚀", parse_mode="Markdown")
    except Exception:
        pass

@admin_only
async def cmd_unvip(update, context):
    if not context.args:
        return
    db  = db_load()
    uid = context.args[0]
    if uid in db["users"]:
        db["users"][uid]["vip"] = False
        db_save(db)
        await update.message.reply_text(f"✅ إلغاء VIP لـ `{uid}`", parse_mode="Markdown")

@admin_only
async def cmd_ban(update, context):
    if not context.args:
        return
    db  = db_load()
    uid = context.args[0]
    if uid in db["users"]:
        db["users"][uid]["blocked"] = True
        db_save(db)
        await update.message.reply_text(f"⛔ حظر `{uid}`", parse_mode="Markdown")

@admin_only
async def cmd_unban(update, context):
    if not context.args:
        return
    db  = db_load()
    uid = context.args[0]
    if uid in db["users"]:
        db["users"][uid]["blocked"] = False
        db_save(db)
        await update.message.reply_text(f"✅ فك حظر `{uid}`", parse_mode="Markdown")

@admin_only
async def cmd_broadcast(update, context):
    if not context.args:
        await update.message.reply_text("استخدام: /broadcast [الرسالة]")
        return
    db   = db_load()
    msg  = " ".join(context.args)
    sent = failed = 0
    for uid_str in db["users"]:
        try:
            await context.bot.send_message(
                int(uid_str), f"📢 *من الإدارة:*\n\n{msg}", parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ أُرسلت: {sent} | ❌ فشل: {failed}")

@admin_only
async def cmd_users(update, context):
    db    = db_load()
    lines = []
    for uid, u in list(db["users"].items())[-20:]:
        b = "💎" if u.get("vip") else "🆓"
        x = "⛔" if u.get("blocked") else ""
        lines.append(f"{b}{x} `{uid}` {u.get('name','?')} | {u.get('total_requests',0)}")
    await update.message.reply_text(
        "👥 *آخر 20:*\n\n" + "\n".join(lines), parse_mode="Markdown")

@admin_only
async def cmd_stats(update, context):
    db    = db_load()
    today = datetime.now().strftime("%Y-%m-%d")
    active = sum(1 for u in db["users"].values() if u.get("last_request_date") == today)
    vip_c  = sum(1 for u in db["users"].values() if u.get("vip"))
    refs   = sum(len(u.get("referrals", [])) for u in db["users"].values())
    await update.message.reply_text(
        f"📊 *إحصائيات:*\n\n"
        f"👥 {len(db['users'])} مستخدم\n"
        f"💎 {vip_c} VIP\n"
        f"🟢 {active} نشط اليوم\n"
        f"📈 {db.get('total_requests',0)} طلب إجمالي\n"
        f"👥 {refs} إحالة إجمالي",
        parse_mode="Markdown")

@admin_only
async def cmd_clearcache(update, context):
    cache_clear()
    await update.message.reply_text("✅ تم مسح الكاش!")

@admin_only
async def cmd_resetwelcome(update, context):
    if os.path.exists(WELCOME_ID_FILE):
        os.remove(WELCOME_ID_FILE)
    await update.message.reply_text("✅ سيتم إعادة رفع الصورة في المرة القادمة!")

async def daily_report(context: ContextTypes.DEFAULT_TYPE):
    db    = db_load()
    today = datetime.now().strftime("%Y-%m-%d")
    active = sum(1 for u in db["users"].values() if u.get("last_request_date") == today)
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"📊 *تقرير يومي — {today}*\n\n"
            f"👥 {len(db['users'])} مستخدم\n"
            f"🟢 {active} نشط اليوم\n"
            f"📈 {db.get('total_requests',0)} طلب إجمالي",
            parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Daily report: {e}")

# ═══════════════════════════════════════════════════════════════
#  FLASK + MAIN
# ═══════════════════════════════════════════════════════════════
_flask = Flask(__name__)

@_flask.route("/")
def health():
    return "✅ DASI BET OK", 200

def main():
    os.makedirs("data", exist_ok=True)
    Thread(target=lambda: _flask.run(
        host="0.0.0.0", port=PORT, use_reloader=False), daemon=True).start()
    logger.info(f"✅ Flask on port {PORT}")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("admin",        cmd_admin))
    app.add_handler(CommandHandler("vip",          cmd_vip))
    app.add_handler(CommandHandler("unvip",        cmd_unvip))
    app.add_handler(CommandHandler("ban",          cmd_ban))
    app.add_handler(CommandHandler("unban",        cmd_unban))
    app.add_handler(CommandHandler("broadcast",    cmd_broadcast))
    app.add_handler(CommandHandler("users",        cmd_users))
    app.add_handler(CommandHandler("stats",        cmd_stats))
    app.add_handler(CommandHandler("clearcache",   cmd_clearcache))
    app.add_handler(CommandHandler("resetwelcome", cmd_resetwelcome))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.job_queue.run_daily(daily_report, time=dtime(8, 0))

    cache_clear()  # امسح الكاش القديم عند كل إعادة تشغيل
    logger.info("✅ DASI BET Bot v2.0 started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
