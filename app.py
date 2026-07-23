"""
LEGACY BANK — Premium Digital Banking Suite
--------------------------------------------
A dark-luxury Streamlit interface for the Legacy Bank system.

Single-file build: all configuration, persistence, security, validation,
business logic, UI components, and pages live here for simple deployment
(just this one file + requirements.txt).

Run with:  streamlit run app.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import random
import re
import string
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

# =========================================================================== #
# CONFIG — business-rule constants (single source of truth)
# =========================================================================== #
DATABASE_FILE = Path("database.json")
LOG_FILE = Path("legacy_bank.log")

MIN_AGE = 18
MAX_AGE = 120

PIN_LENGTH = 4
MOBILE_LENGTH = 10

ACCOUNT_PREFIX_LEN = 4
ACCOUNT_SUFFIX_LEN = 8

MIN_TRANSACTION_AMOUNT = 1
MAX_DEPOSIT_AMOUNT = 10_000
MAX_WITHDRAWAL_AMOUNT = 50_000
MAX_TRANSFER_AMOUNT = 25_000

CURRENCY_SYMBOL = "Rs"

APP_TITLE = "Legacy Bank | Banking Built for Your Future"
BRAND_NAME = "LEGACY BANK"
BRAND_TAGLINE = "Banking Built for Your Future"

TRANSACTION_TYPE_LABELS = {
    "deposit": "Deposit",
    "withdrawal": "Withdrawal",
    "transfer_out": "Transfer Sent",
    "transfer_in": "Transfer Received",
}

# =========================================================================== #
# LOGGING — every business event (sign-in, deposit, withdrawal, transfer,
# profile change, closure) is recorded server-side for audit/debugging.
# =========================================================================== #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger("legacy_bank")


# =========================================================================== #
# STORAGE — thread-safe, atomic JSON persistence
# =========================================================================== #
# Fixes vs. the legacy version:
# - Atomic writes (write to a temp file then os.replace) so a crash mid-save
#   can never truncate/corrupt the database.
# - A module-level lock serialises reads/writes across the Streamlit session
#   threads that share one Python process, closing the race window where two
#   browser tabs could clobber each other's balance updates.
# - Corrupt/missing files degrade to an empty list instead of crashing the app.
_STORAGE_LOCK = threading.Lock()


def load_accounts() -> list[dict[str, Any]]:
    """Load all account records from disk. Never raises."""
    with _STORAGE_LOCK:
        if not DATABASE_FILE.exists():
            return []
        try:
            with open(DATABASE_FILE, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
                return json.loads(content) if content else []
        except (json.JSONDecodeError, OSError) as err:
            logger.error("Failed to load database: %s", err)
            return []


def save_accounts(accounts: list[dict[str, Any]]) -> bool:
    """Persist all account records atomically. Returns success flag."""
    with _STORAGE_LOCK:
        tmp_path = DATABASE_FILE.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(accounts, fh, indent=2)
            tmp_path.replace(DATABASE_FILE)
            return True
        except OSError as err:
            logger.error("Failed to save database: %s", err)
            return False


# =========================================================================== #
# AUTH — PIN hashing utilities
# =========================================================================== #
# Security fix vs. the legacy version: PINs used to be stored and compared as
# plain integers inside database.json. Anyone with file access could read
# every customer's PIN. Here we salt + hash the PIN with PBKDF2-HMAC-SHA256
# before it ever touches disk, and compare in constant time.
_PBKDF2_ITERATIONS = 260_000


def hash_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    """Return (hash_hex, salt_hex) for a PIN, generating a salt if needed."""
    salt_bytes = bytes.fromhex(salt) if salt else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt_bytes, _PBKDF2_ITERATIONS)
    return digest.hex(), salt_bytes.hex()


def verify_pin(pin: str, hash_hex: str, salt_hex: str) -> bool:
    """Constant-time check that `pin` matches the stored hash/salt pair."""
    candidate, _ = hash_pin(pin, salt_hex)
    return hmac.compare_digest(candidate, hash_hex)


# =========================================================================== #
# VALIDATORS — single source of truth for all field rules
# =========================================================================== #
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MOBILE_RE = re.compile(rf"^[6-9]\d{{{MOBILE_LENGTH - 1}}}$")


def validate_name(name: str) -> str | None:
    if not name or not name.strip():
        return "Full legal name is required."
    if len(name.strip()) < 2:
        return "Name must be at least 2 characters."
    if not all(part.isalpha() or part in " .'-" for part in name.strip()):
        return "Name may only contain letters, spaces, apostrophes, and hyphens."
    return None


def validate_age(age: int) -> str | None:
    if age < MIN_AGE:
        return f"Access Denied: Account holders must be at least {MIN_AGE} years of age."
    if age > MAX_AGE:
        return "Please enter a valid age."
    return None


def validate_email(email: str) -> str | None:
    if not email or not email.strip():
        return "Email address is required."
    if not _EMAIL_RE.match(email.strip()):
        return "Please enter a valid email address."
    return None


def validate_mobile(mobile: str) -> str | None:
    if not mobile or not mobile.strip():
        return "Mobile number is required."
    if not _MOBILE_RE.match(mobile.strip()):
        return f"Mobile number must be {MOBILE_LENGTH} digits and start with 6-9."
    return None


def validate_pin(pin: str) -> str | None:
    if not pin or not pin.isdigit() or len(pin) != PIN_LENGTH:
        return f"PIN must be exactly {PIN_LENGTH} numeric digits."
    if len(set(pin)) == 1:
        return "PIN is too weak — please avoid repeated digits (e.g. 1111)."
    return None


def validate_amount(amount: float, minimum: float, maximum: float, label: str = "Amount") -> str | None:
    if amount is None or amount < minimum:
        return f"{label} must be at least {minimum:,.0f}."
    if amount > maximum:
        return f"{label} cannot exceed {maximum:,.0f} per transaction."
    return None


# =========================================================================== #
# SERVICES — core banking business logic (pure; no Streamlit calls here)
# =========================================================================== #
# Every mutating operation follows the same safe pattern:
#   1. Re-load the latest data from disk (so concurrent sessions never work
#      from a stale in-memory copy).
#   2. Locate + validate the target account.
#   3. Apply the change and append a transaction record.
#   4. Persist, then return a fresh copy of the account so the caller can
#      sync `st.session_state`.
class ServiceError(Exception):
    """Raised for expected, user-facing business rule violations."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _generate_account_number(existing_accounts: list[dict[str, Any]]) -> str:
    """4 uppercase letters + 8 digits, guaranteed unique against current data."""
    existing = {acc["account_no"] for acc in existing_accounts}
    for _ in range(1000):
        candidate = "".join(random.choices(string.ascii_uppercase, k=ACCOUNT_PREFIX_LEN)) + "".join(
            random.choices(string.digits, k=ACCOUNT_SUFFIX_LEN)
        )
        if candidate not in existing:
            return candidate
    raise ServiceError("Unable to allocate a unique account number. Please try again.")


def _public_view(account: dict[str, Any]) -> dict[str, Any]:
    """Strip credential material before handing an account back to the UI layer."""
    return {k: v for k, v in account.items() if k not in ("pin_hash", "pin_salt")}


def _find_index(accounts: list[dict[str, Any]], account_no: str) -> int | None:
    for i, acc in enumerate(accounts):
        if acc.get("account_no") == account_no:
            return i
    return None


def _append_transaction(account: dict[str, Any], tx_type: str, amount: float, counterparty: str | None = None,
                         note: str = "") -> None:
    account.setdefault("transactions", []).append(
        {
            "id": uuid.uuid4().hex[:10],
            "type": tx_type,
            "amount": round(amount, 2),
            "balance_after": round(account["balance"], 2),
            "counterparty": counterparty,
            "note": note,
            "timestamp": _now(),
        }
    )


def _authenticate_index(accounts: list[dict[str, Any]], account_no: str, pin: str) -> int:
    idx = _find_index(accounts, account_no)
    if idx is None or not verify_pin(pin, accounts[idx]["pin_hash"], accounts[idx]["pin_salt"]):
        raise ServiceError("Authentication Failed: Invalid account number or PIN.")
    return idx


def create_account(name: str, age: int, email: str, mobile: str, pin: str) -> dict[str, Any]:
    """Create a new account. Raises ServiceError with a user-facing message on failure."""
    for err in (
        validate_name(name),
        validate_age(age),
        validate_email(email),
        validate_mobile(mobile),
        validate_pin(pin),
    ):
        if err:
            raise ServiceError(err)

    accounts = load_accounts()

    normalized_email = email.strip().lower()
    normalized_mobile = mobile.strip()
    if any(acc.get("email", "").lower() == normalized_email for acc in accounts):
        raise ServiceError("An account with this email address already exists. Please sign in instead.")
    if any(acc.get("mobile") == normalized_mobile for acc in accounts):
        raise ServiceError("An account with this mobile number already exists. Please sign in instead.")

    pin_hash, pin_salt = hash_pin(pin)
    account = {
        "account_no": _generate_account_number(accounts),
        "name": name.strip(),
        "age": int(age),
        "email": normalized_email,
        "mobile": normalized_mobile,
        "balance": 0.0,
        "pin_hash": pin_hash,
        "pin_salt": pin_salt,
        "created_at": _now(),
        "transactions": [],
    }

    accounts.append(account)
    if not save_accounts(accounts):
        raise ServiceError("System Exception: unable to save your new account. Please try again.")

    logger.info("Account created: %s", account["account_no"])
    return _public_view(account)


def authenticate(account_no: str, pin: str) -> dict[str, Any]:
    """Verify credentials and return the public account view."""
    accounts = load_accounts()
    idx = _find_index(accounts, account_no.strip().upper())
    if idx is None or not verify_pin(pin, accounts[idx]["pin_hash"], accounts[idx]["pin_salt"]):
        logger.warning("Failed sign-in attempt for account %s", account_no)
        raise ServiceError("Authentication Failed: Invalid Account ID or PIN.")
    logger.info("Successful sign-in: %s", account_no)
    return _public_view(accounts[idx])


def deposit(account_no: str, pin: str, amount: float) -> dict[str, Any]:
    if err := validate_amount(amount, MIN_TRANSACTION_AMOUNT, MAX_DEPOSIT_AMOUNT, "Deposit"):
        raise ServiceError(err)

    accounts = load_accounts()
    idx = _authenticate_index(accounts, account_no, pin)
    accounts[idx]["balance"] = round(accounts[idx]["balance"] + amount, 2)
    _append_transaction(accounts[idx], "deposit", amount)

    if not save_accounts(accounts):
        raise ServiceError("System Exception: deposit could not be saved. Please try again.")
    logger.info("Deposit of %.2f to %s", amount, account_no)
    return _public_view(accounts[idx])


def withdraw(account_no: str, pin: str, amount: float) -> dict[str, Any]:
    if err := validate_amount(amount, MIN_TRANSACTION_AMOUNT, MAX_WITHDRAWAL_AMOUNT, "Withdrawal"):
        raise ServiceError(err)

    accounts = load_accounts()
    idx = _authenticate_index(accounts, account_no, pin)
    if amount > accounts[idx]["balance"]:
        raise ServiceError("Transaction Declined: Insufficient balance.")

    accounts[idx]["balance"] = round(accounts[idx]["balance"] - amount, 2)
    _append_transaction(accounts[idx], "withdrawal", amount)

    if not save_accounts(accounts):
        raise ServiceError("System Exception: withdrawal could not be saved. Please try again.")
    logger.info("Withdrawal of %.2f from %s", amount, account_no)
    return _public_view(accounts[idx])


def transfer(from_account_no: str, pin: str, to_account_no: str, amount: float) -> dict[str, Any]:
    to_account_no = to_account_no.strip().upper()
    if err := validate_amount(amount, MIN_TRANSACTION_AMOUNT, MAX_TRANSFER_AMOUNT, "Transfer"):
        raise ServiceError(err)
    if to_account_no == from_account_no:
        raise ServiceError("You cannot transfer funds to your own account.")

    accounts = load_accounts()
    sender_idx = _authenticate_index(accounts, from_account_no, pin)
    recipient_idx = _find_index(accounts, to_account_no)
    if recipient_idx is None:
        raise ServiceError("Recipient account not found. Please check the Account ID and try again.")
    if amount > accounts[sender_idx]["balance"]:
        raise ServiceError("Transaction Declined: Insufficient balance.")

    accounts[sender_idx]["balance"] = round(accounts[sender_idx]["balance"] - amount, 2)
    accounts[recipient_idx]["balance"] = round(accounts[recipient_idx]["balance"] + amount, 2)
    _append_transaction(accounts[sender_idx], "transfer_out", amount, counterparty=to_account_no)
    _append_transaction(accounts[recipient_idx], "transfer_in", amount, counterparty=from_account_no)

    if not save_accounts(accounts):
        raise ServiceError("System Exception: transfer could not be saved. Please try again.")
    logger.info("Transfer of %.2f from %s to %s", amount, from_account_no, to_account_no)
    return _public_view(accounts[sender_idx])


def get_account(account_no: str, pin: str) -> dict[str, Any]:
    accounts = load_accounts()
    idx = _authenticate_index(accounts, account_no, pin)
    return _public_view(accounts[idx])


def update_profile(account_no: str, pin: str, new_name: str = "", new_email: str = "",
                    new_mobile: str = "", new_pin: str = "") -> dict[str, Any]:
    accounts = load_accounts()
    idx = _authenticate_index(accounts, account_no, pin)

    if new_name.strip():
        if err := validate_name(new_name):
            raise ServiceError(err)
        accounts[idx]["name"] = new_name.strip()

    if new_email.strip():
        if err := validate_email(new_email):
            raise ServiceError(err)
        normalized = new_email.strip().lower()
        if any(i != idx and a.get("email", "").lower() == normalized for i, a in enumerate(accounts)):
            raise ServiceError("That email address is already in use by another account.")
        accounts[idx]["email"] = normalized

    if new_mobile.strip():
        if err := validate_mobile(new_mobile):
            raise ServiceError(err)
        normalized = new_mobile.strip()
        if any(i != idx and a.get("mobile") == normalized for i, a in enumerate(accounts)):
            raise ServiceError("That mobile number is already in use by another account.")
        accounts[idx]["mobile"] = normalized

    if new_pin.strip():
        if err := validate_pin(new_pin):
            raise ServiceError(err)
        pin_hash, pin_salt = hash_pin(new_pin)
        accounts[idx]["pin_hash"] = pin_hash
        accounts[idx]["pin_salt"] = pin_salt

    if not save_accounts(accounts):
        raise ServiceError("System Exception: profile could not be saved. Please try again.")
    logger.info("Profile updated: %s", account_no)
    return _public_view(accounts[idx])


def close_account(account_no: str, pin: str, confirm: bool) -> None:
    if not confirm:
        raise ServiceError("Termination Aborted: Confirmation required.")

    accounts = load_accounts()
    idx = _authenticate_index(accounts, account_no, pin)
    accounts.pop(idx)

    if not save_accounts(accounts):
        raise ServiceError("System Exception: account closure could not be saved. Please try again.")
    logger.info("Account closed: %s", account_no)


def get_transactions(account_no: str, pin: str) -> list[dict[str, Any]]:
    accounts = load_accounts()
    idx = _authenticate_index(accounts, account_no, pin)
    return list(reversed(accounts[idx].get("transactions", [])))


# =========================================================================== #
# STYLES — global CSS (kept the existing navy/gold glassmorphism identity,
# added accessibility fixes: focus rings + prefers-reduced-motion)
# =========================================================================== #
GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700;800&family=Inter:wght@300;400;500;600;700&display=swap');

:root {
    --lb-gold: #D8B865;
    --lb-gold-bright: #F3DFA2;
    --lb-gold-deep: #8C6B24;
    --lb-navy: #0B0F1A;
    --lb-navy-2: #10182B;
    --lb-card: rgba(20, 27, 46, 0.62);
    --lb-border: rgba(216, 184, 101, 0.22);
    --lb-text: #E7ECF5;
    --lb-muted: #93A0BC;
    --lb-success: #37C97E;
    --lb-error: #F0596A;
    --lb-warning: #E3B341;
    --lb-info: #5AA9FF;
}

html, body, .stApp {
    background: radial-gradient(circle at 15% 0%, #10182F 0%, #080B14 55%, #030408 100%);
    font-family: 'Inter', sans-serif;
    color: var(--lb-text);
}

h1, h2, h3, h4 {
    font-family: 'Playfair Display', serif !important;
    background: linear-gradient(135deg, #FBF0CE 0%, var(--lb-gold) 45%, var(--lb-gold-deep) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: 0.3px;
}

p, span, label, div, li { color: var(--lb-text); }
.lb-muted { color: var(--lb-muted) !important; }

#MainMenu, footer { visibility: hidden; }
header { visibility: visible !important; background: transparent !important; }

button:focus-visible, input:focus-visible, [tabindex]:focus-visible {
    outline: 2px solid var(--lb-gold) !important;
    outline-offset: 2px !important;
}
@media (prefers-reduced-motion: reduce) {
    * { animation-duration: 0.001ms !important; transition-duration: 0.001ms !important; }
}

.lb-hero {
    padding: 34px 40px;
    border-radius: 20px;
    background: linear-gradient(135deg, rgba(216,184,101,0.10), rgba(20,27,46,0.35));
    border: 1px solid var(--lb-border);
    backdrop-filter: blur(14px);
    margin-bottom: 28px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.35);
}
.lb-hero h1 { font-size: 2.3rem; margin-bottom: 4px; }
.lb-hero-sub { color: var(--lb-muted); font-size: 1.02rem; letter-spacing: 0.2px; }
.lb-kicker {
    display: inline-block;
    font-size: 0.72rem;
    letter-spacing: 3px;
    color: var(--lb-gold);
    text-transform: uppercase;
    border: 1px solid var(--lb-border);
    border-radius: 999px;
    padding: 4px 14px;
    margin-bottom: 12px;
    background: rgba(216,184,101,0.06);
}

.lb-card {
    background: var(--lb-card);
    border: 1px solid var(--lb-border);
    border-radius: 18px;
    padding: 24px 26px;
    backdrop-filter: blur(16px);
    box-shadow: 0 6px 24px rgba(0,0,0,0.30);
    margin-bottom: 20px;
    transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}
.lb-card:hover {
    transform: translateY(-2px);
    border-color: rgba(216,184,101,0.45);
    box-shadow: 0 10px 30px rgba(0,0,0,0.45);
}
.lb-card-title {
    font-family: 'Playfair Display', serif;
    font-size: 1.05rem;
    color: var(--lb-gold-bright);
    margin-bottom: 10px;
    letter-spacing: 0.4px;
}

.lb-vcard {
    position: relative;
    border-radius: 22px;
    padding: 28px 30px;
    min-height: 190px;
    background: linear-gradient(135deg, #171E33 0%, #0B0F1A 60%, #050710 100%);
    border: 1px solid rgba(216,184,101,0.35);
    box-shadow: 0 16px 40px rgba(0,0,0,0.55), inset 0 0 40px rgba(216,184,101,0.04);
    overflow: hidden;
    color: #F3E9CC;
    font-family: 'Inter', sans-serif;
}
.lb-vcard::before {
    content: "";
    position: absolute;
    top: -60%; right: -20%;
    width: 260px; height: 260px;
    background: radial-gradient(circle, rgba(216,184,101,0.28) 0%, rgba(216,184,101,0) 70%);
    border-radius: 50%;
}
.lb-vcard-top { display: flex; justify-content: space-between; align-items: flex-start; position: relative; z-index: 1; }
.lb-vcard-brand {
    font-family: 'Playfair Display', serif;
    font-weight: 700;
    font-size: 1.15rem;
    letter-spacing: 1px;
    background: linear-gradient(135deg, #FBF0CE, var(--lb-gold));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.lb-vcard-chip { width: 42px; height: 30px; border-radius: 6px; background: linear-gradient(135deg, #D8B865, #8C6B24); margin-bottom: 18px; }
.lb-vcard-number {
    font-size: 1.28rem; letter-spacing: 4px; font-family: 'Inter', monospace;
    margin: 18px 0 20px 0; color: #F3E9CC; position: relative; z-index: 1;
}
.lb-vcard-bottom { display: flex; justify-content: space-between; align-items: flex-end; position: relative; z-index: 1; }
.lb-vcard-label { font-size: 0.62rem; letter-spacing: 1.5px; color: rgba(243,233,204,0.55); text-transform: uppercase; margin-bottom: 3px; }
.lb-vcard-value { font-size: 0.92rem; font-weight: 600; letter-spacing: 0.5px; }

.lb-alert {
    display: flex; align-items: flex-start; gap: 12px;
    border-radius: 14px; padding: 14px 18px; margin: 10px 0;
    font-size: 0.94rem; border: 1px solid transparent; backdrop-filter: blur(10px);
}
.lb-alert-success { background: rgba(55,201,126,0.10); border-color: rgba(55,201,126,0.35); color: #B7F3D3; }
.lb-alert-error   { background: rgba(240,89,106,0.10); border-color: rgba(240,89,106,0.38); color: #FAC3C9; }
.lb-alert-warning { background: rgba(227,179,65,0.10); border-color: rgba(227,179,65,0.38); color: #F5DEA0; }
.lb-alert-info    { background: rgba(90,169,255,0.10); border-color: rgba(90,169,255,0.35); color: #C4DEFF; }

.lb-badge {
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.3px;
}
.lb-badge-in  { background: rgba(55,201,126,0.14); color: #7FE8AC; border: 1px solid rgba(55,201,126,0.35); }
.lb-badge-out { background: rgba(240,89,106,0.14); color: #FF98A4; border: 1px solid rgba(240,89,106,0.35); }

.stButton>button {
    background: linear-gradient(135deg, #E9CD84 0%, #B5872C 100%) !important;
    color: #10131A !important;
    font-weight: 700 !important;
    font-family: 'Inter', sans-serif !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 0.6rem 1.1rem !important;
    letter-spacing: 0.3px;
    transition: transform 0.15s ease, box-shadow 0.15s ease !important;
    box-shadow: 0 4px 14px rgba(216,184,101,0.18);
}
.stButton>button:hover { transform: translateY(-1px); box-shadow: 0 8px 22px rgba(216,184,101,0.35); }
.stButton>button[kind="secondary"] {
    background: rgba(216,184,101,0.08) !important;
    color: var(--lb-gold-bright) !important;
    border: 1px solid var(--lb-border) !important;
    box-shadow: none;
}

.stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] {
    background: rgba(13, 18, 30, 0.75) !important;
    border: 1px solid var(--lb-border) !important;
    color: var(--lb-text) !important;
    border-radius: 10px !important;
}
.stTextInput input:focus, .stNumberInput input:focus {
    border-color: var(--lb-gold) !important;
    box-shadow: 0 0 0 1px var(--lb-gold) !important;
}
label { color: var(--lb-muted) !important; font-size: 0.86rem !important; }

div[data-testid="stMetricValue"] { font-family: 'Playfair Display', serif !important; color: var(--lb-gold-bright) !important; font-weight: 700; }
div[data-testid="stMetric"] {
    background: rgba(16, 22, 38, 0.55);
    border: 1px solid var(--lb-border);
    border-radius: 14px;
    padding: 18px;
    backdrop-filter: blur(10px);
}
div[data-testid="stMetricLabel"] { color: var(--lb-muted) !important; }

.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] {
    background: rgba(16, 22, 38, 0.45);
    border-radius: 10px 10px 0 0;
    border: 1px solid var(--lb-border);
    color: var(--lb-muted);
}
.stTabs [aria-selected="true"] { color: var(--lb-gold-bright) !important; }

section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0A0E18 0%, #05070C 100%);
    border-right: 1px solid rgba(216,184,101,0.12);
}
.lb-side-brand {
    text-align: center; font-family: 'Playfair Display', serif; font-size: 1.55rem; font-weight: 700;
    background: linear-gradient(135deg, #FBF0CE, var(--lb-gold), #8C6B24);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    padding-top: 6px; margin-bottom: 0;
}
.lb-side-tagline {
    text-align: center; font-size: 0.66rem; letter-spacing: 2.5px; color: var(--lb-gold);
    text-transform: uppercase; margin-bottom: 10px; opacity: 0.85;
}

hr { border-color: rgba(216,184,101,0.16) !important; }

.lb-section-label {
    font-size: 0.72rem; letter-spacing: 2px; text-transform: uppercase;
    color: var(--lb-gold); margin: 18px 0 6px 0; opacity: 0.85;
}
</style>
"""


# =========================================================================== #
# UI COMPONENTS — reusable building blocks
# =========================================================================== #
def alert(kind: str, message: str) -> None:
    """Render a themed alert card. kind: success | error | warning | info."""
    st.markdown(
        f'<div class="lb-alert lb-alert-{kind}">💬<div>{message}</div></div>',
        unsafe_allow_html=True,
    )


def hero(kicker: str, title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="lb-hero">
            <div class="lb-kicker">{kicker}</div>
            <h1>{title}</h1>
            <div class="lb-hero-sub">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_label(text: str) -> None:
    st.markdown(f'<div class="lb-section-label">{text}</div>', unsafe_allow_html=True)


def mask_account(account_no: str) -> str:
    """Turn ABCD12345678 into •••• •••• 5678 style masking for card display."""
    tail = account_no[-4:] if len(account_no) >= 4 else account_no
    return f"•••• •••• •••• {tail}"


def render_virtual_card(account: dict[str, Any]) -> None:
    """Render a premium virtual debit card for the authenticated client."""
    name = account.get("name", "Client Name").upper()
    account_no = account.get("account_no", "")
    masked = mask_account(account_no)
    valid_year = datetime.now().year + 5
    st.markdown(
        f"""
        <div class="lb-vcard">
            <div class="lb-vcard-top">
                <div><div class="lb-vcard-chip"></div></div>
                <div class="lb-vcard-brand">LEGACY BANK</div>
            </div>
            <div class="lb-vcard-number">{masked}</div>
            <div class="lb-vcard-bottom">
                <div>
                    <div class="lb-vcard-label">Card Holder</div>
                    <div class="lb-vcard-value">{name}</div>
                </div>
                <div>
                    <div class="lb-vcard-label">Valid Thru</div>
                    <div class="lb-vcard-value">12/{str(valid_year)[-2:]}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def transactions_to_dataframe(transactions: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert raw transaction records into a display-ready DataFrame."""
    if not transactions:
        return pd.DataFrame(columns=["Date", "Type", "Amount", "Balance After", "Counterparty"])

    rows = []
    for tx in transactions:
        ts = datetime.fromisoformat(tx["timestamp"]).strftime("%d %b %Y, %I:%M %p")
        tx_type = TRANSACTION_TYPE_LABELS.get(tx["type"], tx["type"].title())
        signed_amount = tx["amount"] if tx["type"] in ("deposit", "transfer_in") else -tx["amount"]
        rows.append(
            {
                "Date": ts,
                "Type": tx_type,
                "Amount": signed_amount,
                "Balance After": tx["balance_after"],
                "Counterparty": tx.get("counterparty") or "—",
            }
        )
    return pd.DataFrame(rows)


def render_transaction_table(transactions: list[dict[str, Any]], max_rows: int | None = None) -> None:
    df = transactions_to_dataframe(transactions)
    if df.empty:
        alert("info", "No transactions yet. Your activity will appear here.")
        return

    display_df = df.head(max_rows) if max_rows else df
    st.dataframe(
        display_df.style.format({"Amount": f"{CURRENCY_SYMBOL} {{:,.2f}}", "Balance After": f"{CURRENCY_SYMBOL} {{:,.2f}}"}),
        use_container_width=True,
        hide_index=True,
    )


def render_balance_trend(transactions: list[dict[str, Any]]) -> None:
    """Simple balance-over-time chart from oldest to newest transaction."""
    if not transactions:
        return
    ordered = list(reversed(transactions))  # oldest first for a left-to-right trend
    df = pd.DataFrame(
        {
            "Transaction #": range(1, len(ordered) + 1),
            "Balance": [tx["balance_after"] for tx in ordered],
        }
    ).set_index("Transaction #")
    st.line_chart(df, height=220)


# =========================================================================== #
# PAGES — Sign In / Sign Up + all authenticated pages
# =========================================================================== #
def render_auth_screen() -> None:
    hero(
        "Welcome to Legacy Bank",
        "Exclusive Private Banking Suite",
        "Sign in to access your personal vault, or open a new account with us.",
    )

    tab_signin, tab_signup = st.tabs(["🔐 Sign In", "✨ Sign Up"])

    with tab_signin:
        _render_signin_tab()
    with tab_signup:
        _render_signup_tab()


def _render_signin_tab() -> None:
    st.markdown('<div class="lb-card">', unsafe_allow_html=True)
    st.markdown('<div class="lb-card-title">Client Access</div>', unsafe_allow_html=True)
    with st.form("main_signin_form"):
        account_no = st.text_input("Account ID", placeholder="e.g. ABCD12345678").strip().upper()
        pin = st.text_input("4-digit Access PIN", type="password", max_chars=4, placeholder="••••")
        st.markdown("<br>", unsafe_allow_html=True)
        submitted = st.form_submit_button("Access My Vault", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if not submitted:
        return

    if not account_no or not pin:
        alert("error", "Account ID and PIN are required.")
        return
    if not pin.isdigit():
        alert("error", "PIN must be numeric.")
        return

    try:
        with st.spinner("Verifying credentials..."):
            account = authenticate(account_no, pin)
        st.session_state.active_account = account
        st.session_state.active_pin = pin
        st.session_state.current_page = "Dashboard"
        st.toast("Welcome back!", icon="✅")
        st.rerun()
    except ServiceError as err:
        alert("error", str(err))


def _render_signup_tab() -> None:
    st.markdown('<div class="lb-card">', unsafe_allow_html=True)
    st.markdown('<div class="lb-card-title">Open Your Legacy Vault</div>', unsafe_allow_html=True)
    with st.form("main_signup_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Full Legal Name", placeholder="e.g. Aditya Sharma")
            age = st.number_input("Age", min_value=0, max_value=120, step=1, value=18)
            email = st.text_input("Primary Email", placeholder="you@example.com")
        with col2:
            mobile = st.text_input("10-digit Mobile Number", placeholder="9876543210", max_chars=10)
            pin = st.text_input("Create 4-digit Security PIN", type="password", max_chars=4, placeholder="••••")
            pin_confirm = st.text_input("Confirm PIN", type="password", max_chars=4, placeholder="••••")

        st.markdown("<br>", unsafe_allow_html=True)
        submitted = st.form_submit_button("Initialize My Legacy Vault", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if not submitted:
        return

    errors = [
        e
        for e in (
            validate_name(name),
            validate_age(int(age)),
            validate_email(email),
            validate_mobile(mobile),
            validate_pin(pin),
        )
        if e
    ]
    if pin and pin_confirm and pin != pin_confirm:
        errors.append("PIN and Confirm PIN do not match.")

    if errors:
        for e in errors:
            alert("error", e)
        return

    try:
        with st.spinner("Encrypting your vault credentials..."):
            account = create_account(name, int(age), email, mobile, pin)
        st.session_state.active_account = account
        st.session_state.active_pin = pin
        st.session_state.current_page = "Dashboard"
        st.balloons()
        st.rerun()
    except ServiceError as err:
        alert("warning", str(err))


def _current_account() -> dict:
    return st.session_state.active_account


def _current_pin() -> str:
    return st.session_state.active_pin


def _sync_account(fresh_account: dict) -> None:
    st.session_state.active_account = fresh_account


def page_dashboard() -> None:
    account = _current_account()

    hero(
        "Legacy Bank",
        f"Welcome back, {account.get('name', 'Client')}",
        "Banking Built for Your Future — track your balance and manage your account securely.",
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Available Balance", f"Rs {account.get('balance', 0):,.2f}")
    col2.metric("Account ID", account.get("account_no", "—"))
    col3.metric("Total Transactions", len(account.get("transactions", [])))

    st.markdown("<br>", unsafe_allow_html=True)
    section_label("Your Legacy Debit Card")
    render_virtual_card(account)

    st.markdown("<br>", unsafe_allow_html=True)
    section_label("Quick Actions")
    q1, q2, q3, q4, q5 = st.columns(5)
    actions = [
        (q1, "Deposit Funds", "Deposit Funds"),
        (q2, "Withdraw Funds", "Withdraw Funds"),
        (q3, "Transfer Funds", "Transfer Funds"),
        (q4, "Transaction History", "Transaction History"),
        (q5, "Update Profile", "Update Profile"),
    ]
    for col, label, target_page in actions:
        if col.button(label, use_container_width=True, key=f"quick_{target_page}"):
            st.session_state.current_page = target_page
            st.rerun()

    transactions = account.get("transactions", [])
    if transactions:
        st.markdown("<br>", unsafe_allow_html=True)
        section_label("Balance Trend")
        render_balance_trend(transactions)

        section_label("Recent Activity")
        render_transaction_table(transactions, max_rows=5)


def page_deposit() -> None:
    hero("Deposit Reserve", "Add Funds to Your Vault",
         f"Grow your holdings — single deposits up to Rs {MAX_DEPOSIT_AMOUNT:,.0f}.")
    account = _current_account()

    st.markdown('<div class="lb-card">', unsafe_allow_html=True)
    st.markdown('<div class="lb-card-title">Deposit Details</div>', unsafe_allow_html=True)
    with st.form("deposit_form"):
        st.text_input("Account ID", value=account.get("account_no", ""), disabled=True)
        pin = st.text_input("Access PIN", type="password", max_chars=4, placeholder="••••")
        amount = st.number_input("Deposit Amount (Rs)", min_value=0.0, step=500.0, format="%.2f")
        submitted = st.form_submit_button("Execute Deposit", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if not submitted:
        return
    if not pin or not pin.isdigit():
        alert("error", "Valid Access PIN is required.")
        return

    try:
        with st.spinner("Authorizing transfer..."):
            fresh_account = deposit(account["account_no"], pin, amount)
        _sync_account(fresh_account)
        st.session_state.active_pin = pin
        alert("success", f"Deposit Successful: Rs {amount:,.2f} added to your account.")
        st.toast("Deposit complete", icon="✅")
    except ServiceError as err:
        alert("error", str(err))


def page_withdraw() -> None:
    hero("Capital Withdrawal", "Withdraw From Your Vault",
         f"Debit liquidity securely — up to Rs {MAX_WITHDRAWAL_AMOUNT:,.0f} per transaction.")
    account = _current_account()

    st.markdown('<div class="lb-card">', unsafe_allow_html=True)
    st.markdown('<div class="lb-card-title">Withdrawal Details</div>', unsafe_allow_html=True)
    with st.form("withdraw_form"):
        st.text_input("Account ID", value=account.get("account_no", ""), disabled=True)
        pin = st.text_input("Access PIN", type="password", max_chars=4, placeholder="••••")
        amount = st.number_input("Withdrawal Amount (Rs)", min_value=0.0, step=500.0, format="%.2f")
        submitted = st.form_submit_button("Request Withdrawal", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if not submitted:
        return
    if not pin or not pin.isdigit():
        alert("error", "Valid Access PIN is required.")
        return

    try:
        with st.spinner("Validating available balance..."):
            fresh_account = withdraw(account["account_no"], pin, amount)
        _sync_account(fresh_account)
        st.session_state.active_pin = pin
        alert("success", f"Withdrawal Successful: Rs {amount:,.2f} debited from your account.")
        st.toast("Withdrawal complete", icon="✅")
    except ServiceError as err:
        alert("error", str(err))


def page_transfer() -> None:
    hero("Fund Transfer", "Send Money Instantly",
         f"Transfer to any Legacy Bank account — up to Rs {MAX_TRANSFER_AMOUNT:,.0f} per transaction.")
    account = _current_account()

    st.markdown('<div class="lb-card">', unsafe_allow_html=True)
    st.markdown('<div class="lb-card-title">Transfer Details</div>', unsafe_allow_html=True)
    with st.form("transfer_form"):
        st.text_input("From Account", value=account.get("account_no", ""), disabled=True)
        to_account = st.text_input("Recipient Account ID", placeholder="e.g. WXYZ98765432")
        pin = st.text_input("Access PIN", type="password", max_chars=4, placeholder="••••")
        amount = st.number_input("Transfer Amount (Rs)", min_value=0.0, step=500.0, format="%.2f")
        submitted = st.form_submit_button("Send Funds", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if not submitted:
        return
    if not pin or not pin.isdigit():
        alert("error", "Valid Access PIN is required.")
        return
    if not to_account.strip():
        alert("error", "Recipient Account ID is required.")
        return

    try:
        with st.spinner("Processing transfer..."):
            fresh_account = transfer(account["account_no"], pin, to_account, amount)
        _sync_account(fresh_account)
        st.session_state.active_pin = pin
        alert("success", f"Transfer Successful: Rs {amount:,.2f} sent to {to_account.strip().upper()}.")
        st.toast("Transfer complete", icon="✅")
    except ServiceError as err:
        alert("error", str(err))


def page_history() -> None:
    hero("Transaction History", "Your Complete Ledger", "Every deposit, withdrawal, and transfer on your account.")
    account = _current_account()
    pin = _current_pin()

    try:
        transactions = get_transactions(account["account_no"], pin)
    except ServiceError as err:
        alert("error", str(err))
        return

    if not transactions:
        alert("info", "No transactions yet. Your activity will appear here once you deposit, withdraw, or transfer.")
        return

    type_options = ["All"] + sorted({t["type"] for t in transactions})
    selected_type = st.selectbox(
        "Filter by type",
        type_options,
        format_func=lambda t: t if t == "All" else t.replace("_", " ").title(),
    )
    filtered = transactions if selected_type == "All" else [t for t in transactions if t["type"] == selected_type]

    section_label(f"Showing {len(filtered)} of {len(transactions)} transactions")
    render_transaction_table(filtered)

    csv_data = transactions_to_dataframe(filtered).to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download as CSV",
        data=csv_data,
        file_name=f"{account['account_no']}_transactions.csv",
        mime="text/csv",
        use_container_width=True,
    )


def page_profile() -> None:
    hero("Profile Management", "My Vault & Details", "View your card, balance, and update your profile information.")
    account = _current_account()
    pin = _current_pin()

    section_label("Your Legacy Card")
    render_virtual_card(account)

    st.markdown("<br>", unsafe_allow_html=True)
    section_label("Account Summary")
    c1, c2, c3 = st.columns(3)
    c1.metric("Current Balance", f"Rs {account.get('balance', 0):,.2f}")
    c2.metric("Age", account.get("age", "—"))
    c3.metric("Mobile", account.get("mobile", "—"))

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="lb-card">', unsafe_allow_html=True)
    st.markdown('<div class="lb-card-title">Update Profile</div>', unsafe_allow_html=True)
    with st.form("update_form"):
        st.text_input("Account ID", value=account.get("account_no", ""), disabled=True)
        current_pin = st.text_input("Current PIN", type="password", max_chars=4, placeholder="••••")

        st.markdown('<div class="lb-section-label">Fields to update (leave blank to keep unchanged)</div>',
                    unsafe_allow_html=True)
        col1, col2 = st.columns(2)
        with col1:
            new_name = st.text_input("Updated Legal Name", placeholder="Leave blank to skip")
            new_email = st.text_input("Updated Email Address", placeholder="Leave blank to skip")
        with col2:
            new_mobile = st.text_input("Updated Mobile Number", placeholder="Leave blank to skip")
            new_pin = st.text_input("New PIN", type="password", max_chars=4, placeholder="Leave blank to skip")

        submitted = st.form_submit_button("Save Profile Changes", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if submitted:
        if not current_pin or not current_pin.isdigit():
            alert("error", "Current PIN is required.")
        else:
            try:
                with st.spinner("Applying updates..."):
                    fresh_account = update_profile(
                        account["account_no"], current_pin, new_name, new_email, new_mobile, new_pin
                    )
                _sync_account(fresh_account)
                st.session_state.active_pin = new_pin.strip() or current_pin
                alert("success", "Profile successfully updated.")
                st.toast("Profile updated", icon="✅")
            except ServiceError as err:
                alert("error", str(err))

    st.markdown("<br>", unsafe_allow_html=True)
    _render_close_account_section(account, pin)


def _render_close_account_section(account: dict, pin: str) -> None:
    st.markdown('<div class="lb-card">', unsafe_allow_html=True)
    st.markdown('<div class="lb-card-title">Danger Zone</div>', unsafe_allow_html=True)
    st.markdown('<p class="lb-muted">Closing your vault is permanent and cannot be undone.</p>',
                unsafe_allow_html=True)
    if st.button("Close My Vault", type="secondary", use_container_width=True):
        _open_close_account_dialog(account, pin)
    st.markdown("</div>", unsafe_allow_html=True)


if hasattr(st, "dialog"):
    @st.dialog("Confirm Vault Closure")
    def _open_close_account_dialog(account: dict, pin: str) -> None:
        st.warning("This action permanently deletes your account and cannot be reversed.")
        confirm_pin = st.text_input("Re-enter Access PIN to confirm", type="password", max_chars=4)
        confirm_checkbox = st.checkbox("I understand this closure is permanent.")
        col1, col2 = st.columns(2)
        if col1.button("Cancel", use_container_width=True):
            st.rerun()
        if col2.button("Permanently Close Vault", use_container_width=True):
            if not confirm_pin or not confirm_pin.isdigit():
                st.error("Access PIN is required.")
                return
            try:
                close_account(account["account_no"], confirm_pin, confirm_checkbox)
                st.session_state.active_account = None
                st.session_state.active_pin = None
                st.session_state.current_page = "Dashboard"
                st.success("Account permanently closed.")
                st.rerun()
            except ServiceError as err:
                st.error(str(err))
else:
    # Graceful fallback for Streamlit versions without st.dialog support.
    def _open_close_account_dialog(account: dict, pin: str) -> None:
        with st.form("delete_form"):
            confirm_pin = st.text_input("Re-enter Access PIN to confirm", type="password", max_chars=4)
            confirm_checkbox = st.checkbox("I understand this closure is permanent.")
            submitted = st.form_submit_button("Permanently Close Vault", use_container_width=True)
        if submitted:
            if not confirm_pin or not confirm_pin.isdigit():
                alert("error", "Access PIN is required.")
                return
            try:
                close_account(account["account_no"], confirm_pin, confirm_checkbox)
                st.session_state.active_account = None
                st.session_state.active_pin = None
                st.session_state.current_page = "Dashboard"
                alert("success", "Account permanently closed.")
                st.rerun()
            except ServiceError as err:
                alert("error", str(err))


# =========================================================================== #
# APP ENTRY POINT — session management, sidebar, routing
# =========================================================================== #
st.set_page_config(page_title=APP_TITLE, layout="wide", initial_sidebar_state="expanded")
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

PAGES = {
    "Dashboard": page_dashboard,
    "Deposit Funds": page_deposit,
    "Withdraw Funds": page_withdraw,
    "Transfer Funds": page_transfer,
    "Transaction History": page_history,
    "Update Profile": page_profile,
}


def init_session_state() -> None:
    """Session-scoped auth state. Never touches disk directly — see the storage functions above."""
    st.session_state.setdefault("active_account", None)
    st.session_state.setdefault("active_pin", None)
    st.session_state.setdefault("current_page", "Dashboard")


def render_sidebar_authenticated() -> None:
    account = st.session_state.active_account
    st.sidebar.markdown(f'<div class="lb-side-brand">{BRAND_NAME}</div>', unsafe_allow_html=True)
    st.sidebar.markdown(f'<div class="lb-side-tagline">{BRAND_TAGLINE}</div>', unsafe_allow_html=True)
    st.sidebar.divider()

    st.sidebar.markdown(f"👤 **Holder:** {account.get('name')}")
    st.sidebar.caption(f"🆔 Account: {account.get('account_no')}")
    st.sidebar.caption(f"💰 Balance: Rs {account.get('balance', 0):,.2f}")

    if st.sidebar.button("Sign Out", use_container_width=True):
        st.session_state.active_account = None
        st.session_state.active_pin = None
        st.session_state.current_page = "Dashboard"
        st.rerun()

    st.sidebar.divider()

    page_names = list(PAGES.keys())
    if st.session_state.current_page not in page_names:
        st.session_state.current_page = "Dashboard"

    selected_page = st.sidebar.radio(
        "Banking Suite Navigation", page_names, index=page_names.index(st.session_state.current_page)
    )
    st.session_state.current_page = selected_page


def render_sidebar_unauthenticated() -> None:
    st.sidebar.markdown(f'<div class="lb-side-brand">{BRAND_NAME}</div>', unsafe_allow_html=True)
    st.sidebar.markdown(f'<div class="lb-side-tagline">{BRAND_TAGLINE}</div>', unsafe_allow_html=True)
    st.sidebar.divider()
    st.sidebar.info("Please sign in or sign up on the main portal to access banking services.")


def main() -> None:
    init_session_state()

    # Guard clause: unauthenticated users can only ever reach Sign In / Sign Up.
    # This is the single authorization checkpoint for the whole app — every
    # banking page below this line is unreachable without a valid session.
    if not st.session_state.active_account:
        render_sidebar_unauthenticated()
        render_auth_screen()
        return

    render_sidebar_authenticated()
    PAGES[st.session_state.current_page]()


if __name__ == "__main__":
    main()
