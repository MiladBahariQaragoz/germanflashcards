"""
Firestore database layer.

Collections:
  cards/            — global vocabulary (word, translation, sentences, cefr_level: A1/A2/B1/B2)
  user_progress/    — per-user FSRS state for REVIEWED vocab cards only (no "New" docs)
  grammar_cards/    — global grammar exercises (word, german_sentence, english_translation,
                      cefr_level with "_Grammar" suffix e.g. A1_Grammar/B2_Grammar)
  grammar_progress/ — per-user FSRS state for REVIEWED grammar cards (same pattern as user_progress)
  users/            — registered user profiles and preferences
  otps/             — one-time invite codes

Progress document ID format: "{user_id}_{card_id}" (same for both user_progress and grammar_progress)

Design principle: progress collections only store cards that have been reviewed at
least once. "New" cards are discovered on-demand by querying the cards/grammar_cards
collections. This means zero writes on user registration — new users cost nothing
to onboard regardless of vocabulary/grammar size.
"""

import asyncio
import secrets
from datetime import datetime, timedelta, timezone

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

_db = firestore.AsyncClient()
_progress_col = _db.collection("user_progress")
_users_col = _db.collection("users")
_otps_col = _db.collection("otps")
_cards_col = _db.collection("cards")
_grammar_cards_col = _db.collection("grammar_cards")
_grammar_progress_col = _db.collection("grammar_progress")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _end_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=23, minute=59, second=59, microsecond=999999)


def _progress_doc_id(user_id: int, card_id: str) -> str:
    return f"{user_id}_{card_id}"


def _doc_to_card(doc) -> dict:
    """Convert a Firestore user_progress document to a card dict."""
    d = doc.to_dict()
    d["_id"] = d["card_id"]
    d["_progress_exists"] = True
    return d


def _card_col_doc_to_card(doc, user_id: int) -> dict:
    """Convert a cards collection document to a card dict (no progress doc yet)."""
    d = doc.to_dict()
    d["_id"] = doc.id
    d["card_id"] = doc.id
    d["user_id"] = user_id
    d["fsrs_state"] = "New"
    d["state"] = 1
    d["step"] = 0
    d["stability"] = None
    d["difficulty"] = None
    d["last_review"] = None
    d["_progress_exists"] = False
    return d


def _grammar_progress_doc_id(user_id: int, card_id: str) -> str:
    return f"{user_id}_{card_id}"


def _grammar_cefr_levels(cefr_levels: list[str] | None) -> list[str] | None:
    """Map user CEFR settings ['A1','A2'...] → grammar levels ['A1_Grammar','A2_Grammar'...]."""
    if cefr_levels is None:
        return None
    return [f"{level}_Grammar" for level in cefr_levels]


def _grammar_progress_doc_to_card(doc) -> dict:
    """Convert a grammar_progress document to a card dict."""
    d = doc.to_dict()
    d["_id"] = d["card_id"]
    d["_progress_exists"] = True
    d["_card_type"] = "grammar"
    return d


def _grammar_col_doc_to_card(doc, user_id: int) -> dict:
    """Convert a grammar_cards document to a card dict (no progress doc yet)."""
    d = doc.to_dict()
    d["_id"] = doc.id
    d["card_id"] = doc.id
    d["user_id"] = user_id
    d["fsrs_state"] = "New"
    d["state"] = 1
    d["step"] = 0
    d["stability"] = None
    d["difficulty"] = None
    d["last_review"] = None
    d["_progress_exists"] = False
    d["_card_type"] = "grammar"
    return d


# ---------------------------------------------------------------------------
# Session queries
# ---------------------------------------------------------------------------

async def get_due_cards(
    user_id: int,
    cefr_levels: list[str] | None = None,
) -> list[dict]:
    """Non-New cards whose due_date falls on or before end of today.

    cefr_levels — when provided, only cards whose cefr_level is in the list
    are returned.  Filtering is done in Python to avoid a composite index on
    (user_id, due_date <=, cefr_level in).
    """
    cutoff = _end_of_today_utc()
    query = (
        _progress_col
        .where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("due_date", "<=", cutoff))
    )
    docs = await query.get()
    cefr_set = set(cefr_levels) if cefr_levels else None
    return [
        _doc_to_card(doc)
        for doc in docs
        if doc.to_dict().get("fsrs_state") != "New"
        and (cefr_set is None or doc.to_dict().get("cefr_level") in cefr_set)
    ]


async def count_due_cards(
    user_id: int,
    cefr_levels: list[str] | None = None,
) -> int:
    return len(await get_due_cards(user_id, cefr_levels=cefr_levels))


async def get_new_cards(
    user_id: int,
    limit: int = 20,
    cefr_levels: list[str] | None = None,
) -> list[dict]:
    """
    Return up to `limit` cards the user has never reviewed.
    Queries the cards collection directly — no pre-provisioning needed.
    Uses get_all() for efficient batch existence checks on user_progress.
    """
    query = _cards_col
    if cefr_levels:
        query = query.where(filter=FieldFilter("cefr_level", "in", cefr_levels))

    result: list[dict] = []
    last_doc = None
    batch_size = limit * 3  # over-fetch to compensate for already-reviewed cards

    while len(result) < limit:
        q = query.limit(batch_size)
        if last_doc is not None:
            q = q.start_after(last_doc)

        candidates = await q.get()
        if not candidates:
            break

        # Batch-check which candidates already have a progress doc (reviewed)
        progress_refs = [
            _progress_col.document(_progress_doc_id(user_id, c.id))
            for c in candidates
        ]
        progress_map: dict[str, bool] = {}
        async for pdoc in _db.get_all(progress_refs):
            # A progress doc that exists but is still "New" (legacy migration data)
            # is treated as "not yet reviewed" so it remains available as a new card.
            if pdoc.exists and pdoc.to_dict().get("fsrs_state") != "New":
                progress_map[pdoc.id] = True

        for card_doc in candidates:
            doc_id = _progress_doc_id(user_id, card_doc.id)
            if not progress_map.get(doc_id, False):
                result.append(_card_col_doc_to_card(card_doc, user_id))
                if len(result) >= limit:
                    break

        last_doc = candidates[-1]
        if len(candidates) < batch_size:
            break  # exhausted the cards collection

    return result[:limit]


async def get_card_by_id(user_id: int, card_id: str) -> dict | None:
    """
    Fetch a card for display/rating.
    Tries user_progress first (reviewed cards); falls back to cards collection
    (new card, no progress doc yet).
    """
    doc_ref = _progress_col.document(_progress_doc_id(user_id, card_id))
    doc = await doc_ref.get()
    if doc.exists:
        return _doc_to_card(doc)

    # Not reviewed yet — fetch vocab from cards collection
    card_doc = await _cards_col.document(card_id).get()
    if not card_doc.exists:
        return None
    return _card_col_doc_to_card(card_doc, user_id)


async def update_card_after_review(
    user_id: int, card_id: str, update_fields: dict, card: dict
) -> None:
    """
    Apply FSRS update fields after a card is rated.
    If this is the card's first review (no progress doc), creates the full doc.
    If the card already has a progress doc, updates it.
    """
    doc_ref = _progress_col.document(_progress_doc_id(user_id, card_id))
    if card.get("_progress_exists", True):
        await doc_ref.update(update_fields)
    else:
        # First review — create full denormalized progress document
        await doc_ref.set({
            "user_id": user_id,
            "card_id": card_id,
            "word": card.get("word", ""),
            "translation": card.get("translation", ""),
            "german_sentence": card.get("german_sentence", ""),
            "english_translation": card.get("english_translation", ""),
            "cefr_level": card.get("cefr_level", "Unknown"),
            **update_fields,
        })


async def get_card_counts_by_state(
    user_id: int,
    cefr_levels: list[str] | None = None,
) -> dict[str, int]:
    """
    Return card counts per FSRS state.
    Learning/Review/Relearning come from user_progress.
    New = total cards (CEFR filtered) minus reviewed cards — no pre-provisioning needed.
    """
    reviewed_states = ["Learning", "Review", "Relearning"]

    # Build count queries for reviewed states + total cards — run all in parallel
    async def count_progress(state: str) -> int:
        q = (
            _progress_col
            .where(filter=FieldFilter("user_id", "==", user_id))
            .where(filter=FieldFilter("fsrs_state", "==", state))
        )
        if cefr_levels:
            q = q.where(filter=FieldFilter("cefr_level", "in", cefr_levels))
        result = await q.count().get()
        return result[0][0].value

    async def count_total_cards() -> int:
        q = _cards_col
        if cefr_levels:
            q = q.where(filter=FieldFilter("cefr_level", "in", cefr_levels))
        result = await q.count().get()
        return result[0][0].value

    counts_list = await asyncio.gather(
        *[count_progress(s) for s in reviewed_states],
        count_total_cards(),
    )

    counts = dict(zip(reviewed_states, counts_list[:3]))
    total_cards = counts_list[3]
    counts["New"] = max(0, total_cards - sum(counts.values()))
    return counts


# ---------------------------------------------------------------------------
# OTP functions
# ---------------------------------------------------------------------------

async def create_otp(ttl_hours: int = 48) -> str:
    """Generate a random OTP, store it in Firestore, return the code string."""
    code = secrets.token_urlsafe(8)
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
    if doc.to_dict()["expires_at"] < now:
        return False
    await doc.reference.update({"used": True})
    return True


# ---------------------------------------------------------------------------
# User functions
# ---------------------------------------------------------------------------

DEFAULT_CEFR_LEVELS = ["A1", "A2", "B1", "B2"]
DEFAULT_STUDY_DIRECTION = "DE->EN"


async def register_user(user_id: int, username: str | None) -> None:
    """Create a user document. No-op if already exists."""
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
    doc = await _users_col.document(str(user_id)).get()
    return doc.exists


async def get_all_users() -> list[dict]:
    docs = await _users_col.get()
    return [d.to_dict() for d in docs]


async def get_user_settings(user_id: int) -> dict:
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
    # set(merge=True) creates the doc if missing, or merges fields if it exists.
    # This prevents update() failing silently on users who never called /start.
    await _users_col.document(str(user_id)).set(fields, merge=True)


# ---------------------------------------------------------------------------
# Grammar session queries (parallel to vocab session queries above)
# ---------------------------------------------------------------------------

async def get_due_grammar_cards(
    user_id: int,
    cefr_levels: list[str] | None = None,
) -> list[dict]:
    """Non-New grammar cards whose due_date falls on or before end of today."""
    cutoff = _end_of_today_utc()
    grammar_cefr = _grammar_cefr_levels(cefr_levels)
    query = (
        _grammar_progress_col
        .where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("due_date", "<=", cutoff))
    )
    docs = await query.get()
    cefr_set = set(grammar_cefr) if grammar_cefr else None
    return [
        _grammar_progress_doc_to_card(doc)
        for doc in docs
        if doc.to_dict().get("fsrs_state") != "New"
        and (cefr_set is None or doc.to_dict().get("cefr_level") in cefr_set)
    ]


async def count_due_grammar_cards(
    user_id: int,
    cefr_levels: list[str] | None = None,
) -> int:
    return len(await get_due_grammar_cards(user_id, cefr_levels=cefr_levels))


async def get_new_grammar_cards(
    user_id: int,
    limit: int = 20,
    cefr_levels: list[str] | None = None,
) -> list[dict]:
    """Return up to `limit` grammar cards the user has never reviewed."""
    grammar_cefr = _grammar_cefr_levels(cefr_levels)
    query = _grammar_cards_col
    if grammar_cefr:
        query = query.where(filter=FieldFilter("cefr_level", "in", grammar_cefr))

    result: list[dict] = []
    last_doc = None
    batch_size = limit * 3

    while len(result) < limit:
        q = query.limit(batch_size)
        if last_doc is not None:
            q = q.start_after(last_doc)

        candidates = await q.get()
        if not candidates:
            break

        progress_refs = [
            _grammar_progress_col.document(_grammar_progress_doc_id(user_id, c.id))
            for c in candidates
        ]
        progress_map: dict[str, bool] = {}
        async for pdoc in _db.get_all(progress_refs):
            if pdoc.exists and pdoc.to_dict().get("fsrs_state") != "New":
                progress_map[pdoc.id] = True

        for card_doc in candidates:
            doc_id = _grammar_progress_doc_id(user_id, card_doc.id)
            if not progress_map.get(doc_id, False):
                result.append(_grammar_col_doc_to_card(card_doc, user_id))
                if len(result) >= limit:
                    break

        last_doc = candidates[-1]
        if len(candidates) < batch_size:
            break

    return result[:limit]


async def get_grammar_card_by_id(user_id: int, card_id: str) -> dict | None:
    """Fetch a grammar card. Tries grammar_progress first, then grammar_cards."""
    doc_ref = _grammar_progress_col.document(_grammar_progress_doc_id(user_id, card_id))
    doc = await doc_ref.get()
    if doc.exists:
        return _grammar_progress_doc_to_card(doc)

    card_doc = await _grammar_cards_col.document(card_id).get()
    if not card_doc.exists:
        return None
    return _grammar_col_doc_to_card(card_doc, user_id)


async def update_grammar_card_after_review(
    user_id: int, card_id: str, update_fields: dict, card: dict
) -> None:
    """Apply FSRS update after a grammar card is rated. Creates progress doc on first review."""
    doc_ref = _grammar_progress_col.document(_grammar_progress_doc_id(user_id, card_id))
    if card.get("_progress_exists", True):
        await doc_ref.update(update_fields)
    else:
        await doc_ref.set({
            "user_id": user_id,
            "card_id": card_id,
            "word": card.get("word", ""),
            "translation": card.get("translation", ""),
            "german_sentence": card.get("german_sentence", ""),
            "english_translation": card.get("english_translation", ""),
            "cefr_level": card.get("cefr_level", "Unknown"),
            **update_fields,
        })


async def get_grammar_card_counts_by_state(
    user_id: int,
    cefr_levels: list[str] | None = None,
) -> dict[str, int]:
    """Return grammar card counts per FSRS state."""
    grammar_cefr = _grammar_cefr_levels(cefr_levels)
    reviewed_states = ["Learning", "Review", "Relearning"]

    async def count_grammar_progress(state: str) -> int:
        q = (
            _grammar_progress_col
            .where(filter=FieldFilter("user_id", "==", user_id))
            .where(filter=FieldFilter("fsrs_state", "==", state))
        )
        if grammar_cefr:
            q = q.where(filter=FieldFilter("cefr_level", "in", grammar_cefr))
        result = await q.count().get()
        return result[0][0].value

    async def count_total_grammar_cards() -> int:
        q = _grammar_cards_col
        if grammar_cefr:
            q = q.where(filter=FieldFilter("cefr_level", "in", grammar_cefr))
        result = await q.count().get()
        return result[0][0].value

    counts_list = await asyncio.gather(
        *[count_grammar_progress(s) for s in reviewed_states],
        count_total_grammar_cards(),
    )

    counts = dict(zip(reviewed_states, counts_list[:3]))
    total_cards = counts_list[3]
    counts["New"] = max(0, total_cards - sum(counts.values()))
    return counts
