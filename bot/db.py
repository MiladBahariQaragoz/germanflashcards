"""
Firestore database layer.

Collections:
  cards/          — global vocabulary (word, translation, sentences, cefr_level)
  user_progress/  — per-user FSRS state, one doc per (user_id, card_id) pair
  users/          — registered user profiles and preferences
  otps/           — one-time invite codes

user_progress document ID format: "{user_id}_{card_id}"
This allows O(1) lookups without queries when we know both IDs.

Vocabulary fields (word, translation, etc.) are denormalized into user_progress
so session queries never need a join.
"""

import secrets
from datetime import datetime, timedelta, timezone

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

_db = firestore.AsyncClient()
_progress_col = _db.collection("user_progress")
_users_col = _db.collection("users")
_otps_col = _db.collection("otps")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _end_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=23, minute=59, second=59, microsecond=999999)


def _progress_doc_id(user_id: int, card_id: str) -> str:
    return f"{user_id}_{card_id}"


def _doc_to_card(doc) -> dict:
    """Convert a Firestore user_progress document to a card dict.
    Sets '_id' to the card_id so the rest of the app can identify the card."""
    d = doc.to_dict()
    d["_id"] = d["card_id"]
    return d


# ---------------------------------------------------------------------------
# Session queries
# ---------------------------------------------------------------------------

async def get_due_cards(user_id: int) -> list[dict]:
    """Non-New cards whose due_date falls on or before end of today."""
    cutoff = _end_of_today_utc()
    query = (
        _progress_col
        .where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("due_date", "<=", cutoff))
    )
    docs = await query.get()
    return [
        _doc_to_card(doc)
        for doc in docs
        if doc.to_dict().get("fsrs_state") != "New"
    ]


async def count_due_cards(user_id: int) -> int:
    return len(await get_due_cards(user_id))


async def get_new_cards(
    user_id: int,
    limit: int = 20,
    cefr_levels: list[str] | None = None,
) -> list[dict]:
    """Cards the user has never reviewed yet, optionally filtered by CEFR level."""
    query = (
        _progress_col
        .where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("fsrs_state", "==", "New"))
    )
    if cefr_levels:
        query = query.where(filter=FieldFilter("cefr_level", "in", cefr_levels))
    query = query.limit(limit)
    docs = await query.get()
    return [_doc_to_card(doc) for doc in docs]


async def get_card_by_id(user_id: int, card_id: str) -> dict | None:
    """Fetch a single user_progress document by user + card ID."""
    doc_ref = _progress_col.document(_progress_doc_id(user_id, card_id))
    doc = await doc_ref.get()
    if not doc.exists:
        return None
    return _doc_to_card(doc)


async def update_card_after_review(
    user_id: int, card_id: str, update_fields: dict
) -> None:
    """Apply FSRS update fields to the user's progress document for a card."""
    doc_ref = _progress_col.document(_progress_doc_id(user_id, card_id))
    await doc_ref.update(update_fields)


async def get_card_counts_by_state(
    user_id: int,
    cefr_levels: list[str] | None = None,
) -> dict[str, int]:
    """Return card counts per FSRS state for the given user, optionally filtered by CEFR level."""
    states = ["New", "Learning", "Review", "Relearning"]
    counts = {}
    for state in states:
        query = (
            _progress_col
            .where(filter=FieldFilter("user_id", "==", user_id))
            .where(filter=FieldFilter("fsrs_state", "==", state))
        )
        if cefr_levels:
            query = query.where(filter=FieldFilter("cefr_level", "in", cefr_levels))
        result = await query.count().get()
        counts[state] = result[0][0].value
    return counts


# ---------------------------------------------------------------------------
# OTP functions
# ---------------------------------------------------------------------------

async def create_otp(ttl_hours: int = 48) -> str:
    """Generate a random OTP, store it in Firestore, return the code string."""
    code = secrets.token_urlsafe(8)  # ~11 printable chars
    now = datetime.now(timezone.utc)
    await _otps_col.add({
        "code": code,
        "created_at": now,
        "expires_at": now + timedelta(hours=ttl_hours),
        "used": False,
    })
    return code


async def consume_otp(code: str) -> bool:
    """
    Validate and consume an OTP.
    Returns True if code was valid (unused, not expired) and is now marked used.
    Returns False if code doesn't exist, is already used, or is expired.
    """
    now = datetime.now(timezone.utc)
    results = await (
        _otps_col
        .where(filter=FieldFilter("code", "==", code))
        .where(filter=FieldFilter("used", "==", False))
        .limit(1)
        .get()
    )
    if not results:
        return False
    doc = results[0]
    data = doc.to_dict()
    if data["expires_at"] < now:
        return False
    await doc.reference.update({"used": True})
    return True


# ---------------------------------------------------------------------------
# User functions
# ---------------------------------------------------------------------------

DEFAULT_CEFR_LEVELS = ["A1", "A2", "B1", "B2"]
DEFAULT_STUDY_DIRECTION = "DE->EN"


async def register_user(user_id: int, username: str | None) -> None:
    """Create a user document. Safe to call if user already exists (no-op)."""
    doc_ref = _users_col.document(str(user_id))
    doc = await doc_ref.get()
    if doc.exists:
        return
    await doc_ref.set({
        "user_id": user_id,
        "username": username or "",
        "registered_at": datetime.now(timezone.utc),
        "study_direction": DEFAULT_STUDY_DIRECTION,
        "cefr_levels": DEFAULT_CEFR_LEVELS,
    })


async def is_registered_user(user_id: int) -> bool:
    """Return True if the user has a document in the users collection."""
    doc = await _users_col.document(str(user_id)).get()
    return doc.exists


async def get_all_users() -> list[dict]:
    """Return all registered user documents as dicts."""
    docs = await _users_col.get()
    return [d.to_dict() for d in docs]


async def get_user_settings(user_id: int) -> dict:
    """
    Return the user's preferences dict.
    Falls back to defaults if the user document doesn't exist.
    """
    doc = await _users_col.document(str(user_id)).get()
    if not doc.exists:
        return {
            "study_direction": DEFAULT_STUDY_DIRECTION,
            "cefr_levels": DEFAULT_CEFR_LEVELS,
        }
    data = doc.to_dict()
    return {
        "study_direction": data.get("study_direction", DEFAULT_STUDY_DIRECTION),
        "cefr_levels": data.get("cefr_levels", DEFAULT_CEFR_LEVELS),
    }


async def update_user_settings(user_id: int, fields: dict) -> None:
    """Partial update of user settings (study_direction, cefr_levels)."""
    await _users_col.document(str(user_id)).update(fields)
