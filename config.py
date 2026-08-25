import os
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")

# On Railway: set DB_PATH env var to /data/alpha_consensus.db (persistent volume)
# Locally: defaults to the project folder
_default_db = os.path.join(os.path.dirname(__file__), "alpha_consensus.db")
DB_PATH = os.getenv("DB_PATH", _default_db)
UPDATE_INTERVAL_HOURS = 3

TRADERS = [
    # ── Options & flow ──────────────────────────────────────────────────────
    {"handle": "unusual_whales",  "name": "Unusual Whales",         "description": "Options flow & dark pool activity — tracks unusual institutional bets"},
    {"handle": "OptionsHawk",     "name": "OptionsHawk",            "description": "Real-time options activity — flags large smart-money positions"},

    # ── Breaking news ───────────────────────────────────────────────────────
    {"handle": "DeItaone",        "name": "Walter Bloomberg",       "description": "Breaking market headlines — fastest news feed on X"},

    # ── Famous TV / media investors ─────────────────────────────────────────
    {"handle": "jimcramer",       "name": "Jim Cramer",             "description": "CNBC Mad Money host — one of the most-watched market voices on TV"},
    {"handle": "kevinolearytv",   "name": "Kevin O'Leary",          "description": "Shark Tank investor & O'Leary Ventures — vocal on growth stocks & crypto"},

    # ── Legendary fund managers ─────────────────────────────────────────────
    {"handle": "BillAckman",      "name": "Bill Ackman",            "description": "Pershing Square — concentrated long-term activist investor (files 13F)"},
    {"handle": "CathieDWood",     "name": "Cathie Wood",            "description": "ARK Invest — disruptive innovation & AI growth (files 13F + daily trades)"},
    {"handle": "chamath",         "name": "Chamath Palihapitiya",   "description": "Social Capital — tech/AI/healthcare venture & growth investor"},
    {"handle": "DanielSLoeb",     "name": "Dan Loeb",               "description": "Third Point — hedge fund activist investor (files 13F)"},
    {"handle": "BurryArchive",    "name": "Burry Archive",          "description": "Tracks Michael Burry SEC filings — the Big Short investor"},

    # ── Macro economists ────────────────────────────────────────────────────
    {"handle": "RaoulGMI",        "name": "Raoul Pal",              "description": "Real Vision founder — global macro investor, crypto & liquidity cycles"},
    {"handle": "elerianm",        "name": "Mohamed El-Erian",       "description": "Allianz chief economic advisor — ex-PIMCO CEO, mainstream macro voice"},

    # ── Contrarian / bear ───────────────────────────────────────────────────
    {"handle": "PeterSchiff",     "name": "Peter Schiff",           "description": "Euro Pacific Capital — gold bull, Fed critic, contrarian macro investor"},
]
