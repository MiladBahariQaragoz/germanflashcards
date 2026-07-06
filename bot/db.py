"""
Firestore database layer.

Collections:
  cards/            — global vocabulary (word, translation, sentences, cefr_level: A1/A2/B1/B2)
  user_progress/    — per-user FSRS state for REVIEWED vocab cards only (no "New" docs)
  grammar_cards/    — global grammar exercises (word, german_sentence, english_translation,
                      cefr_level with "_Grammar" suffix e.g. A1_Grammar/B2_Grammar)
  grammar_progress/ — per-user FSRS state for REVIEWED grammar cards (same pattern as user_progress)
  users/            — registered user profiles and preferences
  access_requests/  — invite-onboarding state (pending/approved/denied)
  meta/             — singleton app state (e.g. meta/deploy = last announced version)
  otps/             — one-time invite codes

Progress document ID format: "{user_id}_{card_id}" (same for both user_progress and grammar_progress)

Design principle: progress collections only store cards that have been reviewed at
least once. "New" cards are discovered on-demand by querying the cards/grammar_cards
collections. This means zero writes on user registration — new users cost nothing
to onboard regardless of vocabulary/grammar size.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

from bot import streak as streak_logic
from bot import catchup as catchup_logic

# Backlog catch-up: offer to spread when combined due exceeds this. Keep up to
# CATCHUP_PER_DAY review cards due today, then cap later days at CATCHUP_NEXT_DAY so
# the ~20 new cards introduced daily still fit under a ~60-card total. See
# spread_backlog.
BACKLOG_OFFER_THRESHOLD = 100
CATCHUP_PER_DAY = 60
CATCHUP_NEXT_DAY = 40
# Hard cap on how many DUE review cards a single session serves, regardless of how
# large the backlog is. The rest stay due and surface in later sessions, so a
# returning user with a huge pile never faces a wall. This is the durable guard —
# it holds even though every session start advances session_counter (which alone
# would re-expose spread-out installments). See catchup.cap_daily_due.
DAILY_REVIEW_CAP = CATCHUP_PER_DAY  # 60
# New cards are paused while the user has a review backlog: they're only introduced
# when COMBINED (vocab+grammar) due cards are at or below this steady-state cap, so a
# catch-up day (more due than this) shows 0 new cards. Resumes once caught up.
NEW_CARD_PAUSE_THRESHOLD = CATCHUP_NEXT_DAY  # 40
_BATCH_LIMIT = 400  # Firestore allows 500 writes/batch; stay under for safety

# The "study day" for streaks is a Berlin calendar date (not UTC), so an early
# 1 a.m. / 6 a.m. session counts toward that day. See get_streak / record_session_cleared.
_BERLIN = ZoneInfo("Europe/Berlin")


def _berlin_date(offset_days: int = 0) -> str:
    return (datetime.now(_BERLIN).date() - timedelta(days=offset_days)).isoformat()

_db = firestore.AsyncClient()
_progress_col = _db.collection("user_progress")
_users_col = _db.collection("users")
_requests_col = _db.collection("access_requests")
_cards_col = _db.collection("cards")
_grammar_cards_col = _db.collection("grammar_cards")
_grammar_progress_col = _db.collection("grammar_progress")
_meta_col = _db.collection("meta")  # small singleton docs (e.g. last deployed version)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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
    current_session: int,
    cefr_levels: list[str] | None = None,
) -> list[dict]:
    """Non-New cards whose due_session is at or before the user's session counter.

    current_session — the user's shared session counter; a card is due once this
    reaches its stored `due_session`.
    cefr_levels — when provided, only cards whose cefr_level is in the list
    are returned.  Filtering is done in Python to avoid a composite index on
    (user_id, due_session <=, cefr_level in).
    """
    query = (
        _progress_col
        .where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("due_session", "<=", current_session))
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
    current_session: int,
    cefr_levels: list[str] | None = None,
) -> int:
    return len(await get_due_cards(user_id, current_session, cefr_levels=cefr_levels))


async def preview_next_session(
    user_id: int,
    current_session: int,
    cefr_levels: list[str] | None = None,
) -> dict:
    """
    What the user's NEXT session will actually serve. A session start advances the
    counter by one before querying, so we preview at `current_session + 1`, combine
    both domains, and apply the daily cap — the same math the session builder uses.
    Returns {'vocab': n, 'grammar': n, 'total': n, 'allow_new': bool}, where
    total is the capped review load and allow_new reflects the true (uncapped)
    combined backlog (new cards pause while a backlog exists).
    """
    nxt = current_session + 1
    due_vocab, due_grammar = await asyncio.gather(
        get_due_cards(user_id, nxt, cefr_levels=cefr_levels),
        get_due_grammar_cards(user_id, nxt, cefr_levels=cefr_levels),
    )
    combined = due_vocab + due_grammar
    vocab, grammar, total = catchup_logic.session_preview(combined, DAILY_REVIEW_CAP)
    return {
        "vocab": vocab,
        "grammar": grammar,
        "total": total,
        "allow_new": catchup_logic.new_cards_allowed(
            len(combined), NEW_CARD_PAUSE_THRESHOLD
        ),
    }


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
# Access requests (admin-approved onboarding)
# ---------------------------------------------------------------------------
# Onboarding flow: a new user taps "Request access" → a doc is created here →
# the admin approves/denies via inline buttons. Approval creates the users/ doc
# (which is what is_registered_user / _is_authorized check). Doc ID = str(user_id).
#   status: 'pending' | 'approved' | 'denied'

async def create_access_request(
    user_id: int, username: str | None, name: str | None
) -> str:
    """
    Record a pending access request. Returns one of:
      'registered' — already has access (no request made),
      'pending'    — a request is already awaiting review,
      'created'    — a new pending request was stored.
    """
    if await is_registered_user(user_id):
        return "registered"
    ref = _requests_col.document(str(user_id))
    doc = await ref.get()
    if doc.exists and doc.to_dict().get("status") == "pending":
        return "pending"
    await ref.set({
        "user_id": user_id,
        "username": username or "",
        "name": name or "",
        "status": "pending",
        "requested_at": datetime.now(timezone.utc),
    })
    return "created"


async def approve_access_request(user_id: int) -> bool:
    """Register the user and mark their request approved. False if no request exists."""
    ref = _requests_col.document(str(user_id))
    doc = await ref.get()
    if not doc.exists:
        return False
    data = doc.to_dict()
    await register_user(user_id, data.get("username"))
    await ref.update({"status": "approved", "decided_at": datetime.now(timezone.utc)})
    return True


async def deny_access_request(user_id: int) -> bool:
    """Mark a request denied. False if no request exists."""
    ref = _requests_col.document(str(user_id))
    doc = await ref.get()
    if not doc.exists:
        return False
    await ref.update({"status": "denied", "decided_at": datetime.now(timezone.utc)})
    return True


# ---------------------------------------------------------------------------
# User functions
# ---------------------------------------------------------------------------

DEFAULT_CEFR_LEVELS = ["A1"]
DEFAULT_STUDY_DIRECTION = "EN->DE"


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
        "session_counter": 0,
    })


async def get_session_counter(user_id: int) -> int:
    """The user's current shared session counter (0 if never set)."""
    doc = await _users_col.document(str(user_id)).get()
    return (doc.to_dict() or {}).get("session_counter", 0) if doc.exists else 0


async def increment_session_counter(user_id: int) -> int:
    """Advance the user's session counter by one and return the new value.

    Called once per session start (any domain). Cards are due when their stored
    `due_session` is <= this counter.
    """
    ref = _users_col.document(str(user_id))
    await ref.set({"session_counter": firestore.Increment(1)}, merge=True)
    doc = await ref.get()
    return (doc.to_dict() or {}).get("session_counter", 0)


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
            "session_counter": 0,
        }
    data = doc.to_dict()
    return {
        "study_direction": data.get("study_direction", DEFAULT_STUDY_DIRECTION),
        "cefr_levels": data.get("cefr_levels", DEFAULT_CEFR_LEVELS),
        "session_counter": data.get("session_counter", 0),
    }


async def update_user_settings(user_id: int, fields: dict) -> None:
    # set(merge=True) creates the doc if missing, or merges fields if it exists.
    # This prevents update() failing silently on users who never called /start.
    await _users_col.document(str(user_id)).set(fields, merge=True)


# ---------------------------------------------------------------------------
# Admin: user roster + silent removal
# ---------------------------------------------------------------------------

async def _user_overview_row(data: dict) -> dict:
    """Per-user admin summary: identity + current streak + due counts (both domains)."""
    user_id = data.get("user_id")
    cefr = data.get("cefr_levels", DEFAULT_CEFR_LEVELS)
    counter = data.get("session_counter", 0)
    vocab_due, grammar_due = await asyncio.gather(
        count_due_cards(user_id, counter, cefr_levels=cefr),
        count_due_grammar_cards(user_id, counter, cefr_levels=cefr),
    )
    return {
        "user_id": user_id,
        "username": data.get("username") or "",
        "streak": streak_logic.current_streak(data, _berlin_date(), _berlin_date(1)),
        "cefr_levels": cefr,
        "vocab_due": vocab_due,
        "grammar_due": grammar_due,
        "registered_at": data.get("registered_at"),
    }


async def get_admin_overview() -> list[dict]:
    """
    Roster of every registered user with how they're doing (streak + due counts),
    for the admin's /admin view. Sorted by current streak (desc), then username.
    """
    users = await get_all_users()
    rows = await asyncio.gather(*(_user_overview_row(u) for u in users))
    rows.sort(key=lambda r: (-r["streak"], r["username"].lower()))
    return rows


async def _delete_progress_for_user(col, user_id: int) -> int:
    """Batch-delete every progress doc owned by `user_id` in `col`. Returns count."""
    docs = await col.where(filter=FieldFilter("user_id", "==", user_id)).get()
    batch = _db.batch()
    pending = 0
    for doc in docs:
        batch.delete(doc.reference)
        pending += 1
        if pending == _BATCH_LIMIT:
            await batch.commit()
            batch = _db.batch()
            pending = 0
    if pending:
        await batch.commit()
    return len(docs)


async def remove_user(user_id: int) -> dict:
    """
    Silently remove a user from the bot: delete their users/ doc (so they lose
    access), their access_requests/ doc (so a fresh /start can request again), and
    all their progress docs in both domains. Does NOT notify the user. Returns
    {'vocab': n, 'grammar': n} progress docs deleted.
    """
    vocab_deleted, grammar_deleted = await asyncio.gather(
        _delete_progress_for_user(_progress_col, user_id),
        _delete_progress_for_user(_grammar_progress_col, user_id),
    )
    await asyncio.gather(
        _users_col.document(str(user_id)).delete(),
        _requests_col.document(str(user_id)).delete(),
    )
    return {"vocab": vocab_deleted, "grammar": grammar_deleted}


# ---------------------------------------------------------------------------
# Deploy version tracking (powers the "new version deployed" admin ping)
# ---------------------------------------------------------------------------

async def get_last_deploy_version() -> str | None:
    """The version string last announced to the admin, or None if never set."""
    doc = await _meta_col.document("deploy").get()
    return (doc.to_dict() or {}).get("version") if doc.exists else None


async def set_last_deploy_version(version: str) -> None:
    """Record the version just announced so the next restart on the same commit stays quiet."""
    await _meta_col.document("deploy").set(
        {"version": version, "announced_at": datetime.now(timezone.utc)}, merge=True
    )


# ---------------------------------------------------------------------------
# Streak tracking
# ---------------------------------------------------------------------------
# A streak day requires BOTH the grammar and vocab sessions to be "satisfied" on
# the same Berlin calendar date. A domain is satisfied when its session is cleared
# OR it had nothing due that day (no required reviews). State on the user doc:
#   streak_count          — current run length
#   last_streak_date      — Berlin date the streak last advanced (YYYY-MM-DD)
#   vocab_cleared_date    — Berlin date the vocab session was last cleared
#   grammar_cleared_date  — Berlin date the grammar session was last cleared

async def record_session_cleared(
    user_id: int, domain: str, *, other_domain_due: int
) -> dict:
    """
    Mark `domain` ('vocab'|'grammar') cleared for today and advance the streak —
    clearing either domain is enough (once per Berlin day).

    other_domain_due — count of cards still due in the *other* domain. Used only to
    report `both_done` (true if the other domain was cleared today or has 0 due),
    which drives the completion message; it no longer gates the streak.

    Returns {'both_done': bool, 'streak': int, 'advanced': bool}.
    """
    ref = _users_col.document(str(user_id))
    doc = await ref.get()
    data = doc.to_dict() or {}
    updates, result = streak_logic.streak_transition(
        data, domain, _berlin_date(), _berlin_date(1), other_domain_due
    )
    await ref.set(updates, merge=True)
    return result


async def get_streak(user_id: int) -> int:
    """Current streak, or 0 if it has lapsed (last advance older than yesterday)."""
    doc = await _users_col.document(str(user_id)).get()
    data = doc.to_dict() or {}
    return streak_logic.current_streak(data, _berlin_date(), _berlin_date(1))


async def get_leaderboard(limit: int = 10) -> list[dict]:
    """Top active streaks across all users: [{'user_id','username','streak'}, ...]."""
    users = await get_all_users()
    return streak_logic.rank_streaks(users, _berlin_date(), _berlin_date(1), limit)


# ---------------------------------------------------------------------------
# Grammar session queries (parallel to vocab session queries above)
# ---------------------------------------------------------------------------

async def get_due_grammar_cards(
    user_id: int,
    current_session: int,
    cefr_levels: list[str] | None = None,
) -> list[dict]:
    """Non-New grammar cards whose due_session is at or before the session counter."""
    grammar_cefr = _grammar_cefr_levels(cefr_levels)
    query = (
        _grammar_progress_col
        .where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("due_session", "<=", current_session))
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
    current_session: int,
    cefr_levels: list[str] | None = None,
) -> int:
    return len(await get_due_grammar_cards(user_id, current_session, cefr_levels=cefr_levels))


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


# ---------------------------------------------------------------------------
# Backlog catch-up (installments)
# ---------------------------------------------------------------------------

async def _spread_due_cards(
    due_cards: list[dict], current_session: int, today_max: int, next_day_max: int
) -> dict[str, int]:
    """
    Reschedule the overflow of a COMBINED (vocab + grammar) due-card list across
    upcoming sessions: keep the `today_max` earliest-due cards due now, then
    ~next_day_max per session after — one shared budget across both domains. Each
    card keeps its progress doc; only `due_session` moves, routed to the right
    collection by `_card_type` ('grammar' → grammar_progress, else user_progress).
    Doc IDs are `{user_id}_{card_id}` in both. Returns {'vocab': n, 'grammar': n} moved.
    """
    moved = {"vocab": 0, "grammar": 0}
    offsets = catchup_logic.plan_installments(len(due_cards), today_max, next_day_max)
    if not offsets:
        return moved

    # Earliest-due cards (either domain) stay; the latest-due get pushed furthest.
    due_cards.sort(key=lambda c: c.get("due_session", 0))
    overflow = due_cards[today_max:]

    batch = _db.batch()
    pending = 0
    for card, session_offset in zip(overflow, offsets):
        doc_id = f"{card['user_id']}_{card['card_id']}"
        is_grammar = card.get("_card_type") == "grammar"
        col = _grammar_progress_col if is_grammar else _progress_col
        batch.update(col.document(doc_id), {"due_session": current_session + session_offset})
        moved["grammar" if is_grammar else "vocab"] += 1
        pending += 1
        if pending == _BATCH_LIMIT:
            await batch.commit()
            batch = _db.batch()
            pending = 0
    if pending:
        await batch.commit()
    return moved


async def spread_backlog(
    user_id: int,
    current_session: int,
    cefr_levels: list[str] | None = None,
    today_max: int = CATCHUP_PER_DAY,
    next_day_max: int = CATCHUP_NEXT_DAY,
) -> dict[str, int]:
    """
    Spread the user's COMBINED (vocab + grammar) due backlog into installments:
    keep up to `today_max` cards due now across both domains together, then
    ~next_day_max per upcoming session. Returns {'vocab': moved, 'grammar': moved}.
    """
    due_vocab, due_grammar = await asyncio.gather(
        get_due_cards(user_id, current_session, cefr_levels=cefr_levels),
        get_due_grammar_cards(user_id, current_session, cefr_levels=cefr_levels),
    )
    return await _spread_due_cards(
        due_vocab + due_grammar, current_session, today_max, next_day_max
    )
