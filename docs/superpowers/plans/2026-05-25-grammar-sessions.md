# Grammar Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second daily study session for grammar using four new JSON flashcard files, stored in a separate Firestore collection, with full FSRS spaced repetition, accessible via `/grammar` command, with combined morning notifications covering both grammar and vocabulary.

**Architecture:** Grammar cards go into a new `grammar_cards` Firestore collection (parallel to `cards`); grammar FSRS progress goes into `grammar_progress` (parallel to `user_progress`). The existing `SessionQueue` class is reused for grammar sessions via a separate in-memory registry. New callback patterns (`show_grammar:`, `grade_grammar:`) handle grammar card interactions without touching the existing vocab flow. A one-time upload script seeds the grammar collection idempotently.

**Tech Stack:** Python 3.11, python-telegram-bot v20, google-cloud-firestore, APScheduler, FSRS (via bot/fsrs_service.py)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `scripts/upload_grammar.py` | Create | One-time upload of 4 grammar JSON files → Firestore `grammar_cards` collection |
| `bot/db.py` | Modify | Add grammar collection references + 6 new grammar DB functions |
| `bot/queue_manager.py` | Modify | Add `_grammar_sessions` registry + `get_grammar_session` + `reset_all_grammar_sessions` |
| `bot/handlers.py` | Modify | Add `/grammar` command, 3 grammar callbacks, update `/stats`, update `/start` welcome |
| `bot/scheduler.py` | Modify | Combined morning message (grammar+vocab counts, 2 buttons), updated nag for both sessions |
| `bot/main.py` | Modify | Register `/grammar` command handler + 3 new callback handlers + update command menu |
| `tests/test_grammar_queue.py` | Create | Tests for grammar session registry isolation from vocab sessions |

---

## Task 1: Grammar Upload Script

**Files:**
- Create: `scripts/upload_grammar.py`

- [ ] **Step 1: Create the upload script**

```python
# scripts/upload_grammar.py
"""
One-time script: upload grammar flashcard JSON files to Firestore `grammar_cards` collection.
Safe to re-run — uses MD5(word|german_sentence) as document ID for idempotency.

Run from the project root:
  GOOGLE_CLOUD_PROJECT=<project_id> python scripts/upload_grammar.py

On GCE with ADC: GOOGLE_CLOUD_PROJECT is auto-detected; just run:
  python scripts/upload_grammar.py
"""
import asyncio
import hashlib
import json
from pathlib import Path

from google.cloud import firestore

ROOT = Path(__file__).parent.parent
GRAMMAR_FILES = [
    ROOT / "a1_grammar_flashcards_all_exercises.json",
    ROOT / "a2_grammar_flashcards_all_exercises.json",
    ROOT / "b1_grammar_flashcards_all_exercises.json",
    ROOT / "b2_grammar_flashcards_all_merged.json",
]


def _doc_id(card: dict) -> str:
    """Deterministic Firestore document ID from (word, german_sentence)."""
    key = f"{card['word']}|{card.get('german_sentence', '')}"
    return hashlib.md5(key.encode()).hexdigest()


async def upload_grammar() -> None:
    db = firestore.AsyncClient()
    col = db.collection("grammar_cards")

    total_new = 0
    total_existing = 0

    for json_file in GRAMMAR_FILES:
        if not json_file.exists():
            print(f"  WARNING: {json_file.name} not found — skipping.")
            continue

        with open(json_file, encoding="utf-8") as f:
            cards = json.load(f)

        print(f"  Processing {json_file.name} ({len(cards)} cards) ...")

        for card in cards:
            doc_id = _doc_id(card)
            ref = col.document(doc_id)
            snapshot = await ref.get()
            if snapshot.exists:
                total_existing += 1
                continue
            await ref.set({
                "word": card["word"],
                "translation": card["translation"],
                "german_sentence": card.get("german_sentence", ""),
                "english_translation": card.get("english_translation", ""),
                "cefr_level": card.get("cefr_level", "Unknown"),
            })
            total_new += 1

    await db.close()
    print(f"\nDone. New cards uploaded: {total_new}, Already existed: {total_existing}")
    print(
        "\nCreate this composite index in Firestore console:\n"
        "  Collection : grammar_progress\n"
        "  Fields     : user_id ASC, due_date ASC\n"
        "  Console    : https://console.firebase.google.com/project/_/firestore/indexes"
    )


if __name__ == "__main__":
    asyncio.run(upload_grammar())
```

- [ ] **Step 2: Run the script on the GCE VM (or locally with ADC configured)**

```bash
# On the GCE VM or locally with gcloud auth application-default login:
python scripts/upload_grammar.py
```

Expected output (first run, counts will vary by file sizes):
```
  Processing a1_grammar_flashcards_all_exercises.json (N cards) ...
  Processing a2_grammar_flashcards_all_exercises.json (N cards) ...
  Processing b1_grammar_flashcards_all_exercises.json (N cards) ...
  Processing b2_grammar_flashcards_all_merged.json (N cards) ...

Done. New cards uploaded: XXXX, Already existed: 0

Create this composite index in Firestore console:
  Collection : grammar_progress
  Fields     : user_id ASC, due_date ASC
```

- [ ] **Step 3: Create the Firestore composite index for `grammar_progress`**

Run this gcloud command:
```bash
gcloud firestore indexes composite create \
  --collection-group=grammar_progress \
  --field-config field-path=user_id,order=ascending \
  --field-config field-path=due_date,order=ascending \
  --project=<YOUR_PROJECT_ID>
```

Or go to https://console.firebase.google.com/project/_/firestore/indexes and create the index manually. Wait for status to become "Enabled" before running the bot.

- [ ] **Step 4: Commit**

```bash
git add scripts/upload_grammar.py
git commit -m "feat: add grammar cards upload script"
```

---

## Task 2: Grammar DB Functions

**Files:**
- Modify: `bot/db.py`

- [ ] **Step 1: Add collection references after the existing ones (around line 29)**

Open `bot/db.py`. After line 29 (`_cards_col = _db.collection("cards")`), add:

```python
_grammar_cards_col = _db.collection("grammar_cards")
_grammar_progress_col = _db.collection("grammar_progress")
```

- [ ] **Step 2: Add helper functions after `_card_col_doc_to_card` (around line 67)**

After the existing `_card_col_doc_to_card` function, add:

```python
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
```

- [ ] **Step 3: Add grammar session query functions at the end of `bot/db.py`**

Append these functions after the existing `update_user_settings` function:

```python
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
```

- [ ] **Step 4: Commit**

```bash
git add bot/db.py
git commit -m "feat: add grammar_cards and grammar_progress DB functions"
```

---

## Task 3: Grammar Session Queue

**Files:**
- Modify: `bot/queue_manager.py`
- Create: `tests/test_grammar_queue.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_grammar_queue.py`:

```python
from bot.queue_manager import (
    get_session,
    get_grammar_session,
    reset_all_sessions,
    reset_all_grammar_sessions,
    SessionQueue,
)

CARD_A = {"_id": "aaa", "word": "kommen", "translation": "to come"}
CARD_B = {"_id": "bbb", "word": "kaufen", "translation": "to buy"}


def test_get_grammar_session_returns_session_queue():
    s = get_grammar_session(1001)
    assert isinstance(s, SessionQueue)


def test_grammar_session_isolated_from_vocab_session():
    vocab_session = get_session(2001)
    vocab_session.build(due_cards=[CARD_A], new_cards=[])

    grammar_session = get_grammar_session(2001)
    assert grammar_session.remaining_count() == 0


def test_reset_all_grammar_sessions_does_not_affect_vocab():
    vocab_session = get_session(3001)
    vocab_session.build(due_cards=[CARD_A], new_cards=[])

    grammar_session = get_grammar_session(3001)
    grammar_session.build(due_cards=[CARD_B], new_cards=[])

    reset_all_grammar_sessions()

    assert grammar_session.remaining_count() == 0
    assert vocab_session.remaining_count() == 1


def test_reset_all_sessions_does_not_affect_grammar():
    vocab_session = get_session(4001)
    vocab_session.build(due_cards=[CARD_A], new_cards=[])

    grammar_session = get_grammar_session(4001)
    grammar_session.build(due_cards=[CARD_B], new_cards=[])

    reset_all_sessions()

    assert vocab_session.remaining_count() == 0
    assert grammar_session.remaining_count() == 1


def test_grammar_session_same_user_returns_same_instance():
    s1 = get_grammar_session(5001)
    s2 = get_grammar_session(5001)
    assert s1 is s2
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_grammar_queue.py -v
```

Expected: `ImportError: cannot import name 'get_grammar_session' from 'bot.queue_manager'`

- [ ] **Step 3: Add grammar session registry to `bot/queue_manager.py`**

Append to the end of `bot/queue_manager.py` (after `reset_all_sessions`):

```python
# Grammar session registry — completely independent from vocab sessions.
_grammar_sessions: dict[int, SessionQueue] = {}


def get_grammar_session(user_id: int) -> SessionQueue:
    """Return the grammar SessionQueue for this user, creating one if it doesn't exist."""
    if user_id not in _grammar_sessions:
        _grammar_sessions[user_id] = SessionQueue()
    return _grammar_sessions[user_id]


def reset_all_grammar_sessions() -> None:
    """Reset every active grammar session (called by the morning scheduler)."""
    for s in _grammar_sessions.values():
        s.reset()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_grammar_queue.py -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
pytest -v
```

Expected: All existing tests + 5 new tests PASS

- [ ] **Step 6: Commit**

```bash
git add bot/queue_manager.py tests/test_grammar_queue.py
git commit -m "feat: add grammar session registry to queue_manager"
```

---

## Task 4: Grammar Handlers

**Files:**
- Modify: `bot/handlers.py`

- [ ] **Step 1: Add grammar imports at the top of `bot/handlers.py`**

After line 13 (`from bot import queue_manager as qm`), the imports are already in place. But we need to also import the grammar queue manager functions. The `qm` module will have the new functions after Task 3, so no import changes are needed — `qm.get_grammar_session` and `qm.reset_all_grammar_sessions` will be available.

No import changes needed.

- [ ] **Step 2: Add `/grammar` command handler and session helpers**

After the existing `cmd_session` function (around line 80), add:

```python
async def cmd_grammar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    await _start_grammar_session(
        context,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
    )


async def _start_grammar_session(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    settings = await db.get_user_settings(user_id)
    cefr = settings["cefr_levels"]
    due = await db.get_due_grammar_cards(user_id, cefr_levels=cefr)
    new = (
        await db.get_new_grammar_cards(user_id, 20, cefr_levels=cefr)
        if len(due) <= 150
        else []
    )
    qm.get_grammar_session(user_id).build(due_cards=due, new_cards=new)
    card = qm.get_grammar_session(user_id).pop_next()
    if card is None:
        await context.bot.send_message(chat_id=chat_id, text="No grammar cards due today! 🎉")
        return
    await _send_grammar_card_front(
        context, chat_id, card, study_direction=settings["study_direction"]
    )


async def _send_grammar_card_front(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    card: dict,
    study_direction: str = "DE->EN",
) -> None:
    card_id = card["_id"]
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Show Answer", callback_data=f"show_grammar:{card_id}")]]
    )
    cefr = card.get("cefr_level", "?")
    if study_direction == "DE->EN":
        front_text = f"📚 [{cefr}] 🇩🇪 {card.get('german_sentence', card['word'])}"
    else:
        front_text = f"📚 [{cefr}] 🇬🇧 {card.get('english_translation', card.get('translation', ''))}"
    await context.bot.send_message(
        chat_id=chat_id, text=front_text, reply_markup=keyboard
    )
```

- [ ] **Step 3: Add grammar callback handlers**

After `_send_grammar_card_front`, add:

```python
async def callback_start_grammar_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return
    await _start_grammar_session(
        context,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
    )


async def callback_show_grammar_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return

    user_id = update.effective_user.id
    card_id = query.data.split(":", 1)[1]

    card = await db.get_grammar_card_by_id(user_id, card_id)
    if card is None:
        await query.edit_message_text("Grammar card not found.")
        return

    settings = await db.get_user_settings(user_id)
    intervals = fsrs_service.preview_intervals(card)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Again ({intervals[1]})", callback_data=f"grade_grammar:{card_id}:1"
                ),
                InlineKeyboardButton(
                    f"Hard ({intervals[2]})", callback_data=f"grade_grammar:{card_id}:2"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"Good ({intervals[3]})", callback_data=f"grade_grammar:{card_id}:3"
                ),
                InlineKeyboardButton(
                    f"Easy ({intervals[4]})", callback_data=f"grade_grammar:{card_id}:4"
                ),
            ],
        ]
    )
    cefr = card.get("cefr_level", "?")
    if settings["study_direction"] == "DE->EN":
        back_text = (
            f"📚 [{cefr}] 🇩🇪 {card.get('german_sentence', card['word'])}\n"
            f"🇬🇧 {card.get('english_translation', card.get('translation', ''))}\n\n"
            f"💡 {card['word']} — {card['translation']}"
        )
    else:
        back_text = (
            f"📚 [{cefr}] 🇬🇧 {card.get('english_translation', card.get('translation', ''))}\n"
            f"🇩🇪 {card.get('german_sentence', card['word'])}\n\n"
            f"💡 {card['word']} — {card['translation']}"
        )
    await query.edit_message_text(text=back_text, reply_markup=keyboard)


async def callback_grade_grammar(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return

    user_id = update.effective_user.id
    _, card_id, rating_str = query.data.split(":")
    rating_int = int(rating_str)

    card = await db.get_grammar_card_by_id(user_id, card_id)
    if card is None:
        return

    update_fields, _ = fsrs_service.rate_card(card, rating_int)
    await db.update_grammar_card_after_review(user_id, card_id, update_fields, card)
    await query.edit_message_reply_markup(reply_markup=None)

    if rating_int == 1:
        updated_card = {**card, **update_fields}
        qm.get_grammar_session(user_id).add_to_again_pile(updated_card)

    next_card = qm.get_grammar_session(user_id).pop_next()
    chat_id = update.effective_chat.id

    if next_card is None:
        qm.get_grammar_session(user_id).check_and_set_kill_switch()
        await context.bot.send_message(
            chat_id=chat_id,
            text="📚 Grammatik fertig! Grammar session complete. 🎉\n\nDon't forget your vocabulary session — /vocab",
        )
        return

    settings = await db.get_user_settings(user_id)
    await _send_grammar_card_front(
        context, chat_id, next_card, study_direction=settings["study_direction"]
    )
```

- [ ] **Step 4: Update `cmd_stats` to show both grammar and vocabulary sections**

Replace the existing `cmd_stats` function (lines 53–68) with:

```python
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    user_id = update.effective_user.id
    settings = await db.get_user_settings(user_id)
    cefr = settings["cefr_levels"]
    cefr_label = ", ".join(sorted(cefr))

    vocab_counts, grammar_counts = await asyncio.gather(
        db.get_card_counts_by_state(user_id, cefr_levels=cefr),
        db.get_grammar_card_counts_by_state(user_id, cefr_levels=cefr),
    )

    text = (
        f"📊 Stats (levels: {cefr_label})\n\n"
        f"🗂️ *Vocabulary*\n"
        f"New: {vocab_counts['New']}\n"
        f"Learning: {vocab_counts['Learning']}\n"
        f"Review: {vocab_counts['Review']}\n"
        f"Relearning: {vocab_counts['Relearning']}\n\n"
        f"📚 *Grammar*\n"
        f"New: {grammar_counts['New']}\n"
        f"Learning: {grammar_counts['Learning']}\n"
        f"Review: {grammar_counts['Review']}\n"
        f"Relearning: {grammar_counts['Relearning']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
```

- [ ] **Step 5: Add `asyncio` import to handlers.py**

At the top of `bot/handlers.py`, after `import logging`, add:

```python
import asyncio
```

- [ ] **Step 6: Update `/start` welcome message to mention `/grammar`**

Replace the `await update.message.reply_text(...)` call inside `cmd_start` with:

```python
    await update.message.reply_text(
        "👋 Willkommen! This bot helps you learn German using spaced repetition (FSRS).\n\n"
        "📋 *Commands*\n"
        "📚 /grammar — Start your grammar session (sentence exercises)\n"
        "▶️ /vocab — Start your vocabulary session\n"
        "📊 /stats — Grammar + vocabulary progress\n"
        "⚙️ /settings — Study direction & CEFR levels\n\n"
        "⏰ *Daily routine*\n"
        "Every morning at 8:00 (Berlin time) you'll get a message with today's grammar and vocab counts. "
        "Complete both sessions for the best results! "
        "Reminders are sent every 2 hours if sessions are unfinished.\n\n"
        "Ready? Start with 📚 /grammar or ▶️ /vocab! 🚀",
        parse_mode="Markdown",
    )
```

- [ ] **Step 7: Add `/vocab` as alias for `/session`**

After `cmd_grammar`, add:

```python
async def cmd_vocab(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias for /session — starts the vocabulary study session."""
    if not await _is_authorized(update):
        return
    await _start_session(
        context,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
    )
```

- [ ] **Step 8: Commit**

```bash
git add bot/handlers.py
git commit -m "feat: add /grammar command, grammar callbacks, update /stats and /start"
```

---

## Task 5: Scheduler Updates

**Files:**
- Modify: `bot/scheduler.py`

- [ ] **Step 1: Update `morning_trigger` to send a combined grammar+vocab message**

Replace the entire `morning_trigger` function with:

```python
async def morning_trigger(bot) -> None:
    """Reset all queues and send each user their daily grammar + vocab card counts."""
    qm.reset_all_sessions()
    qm.reset_all_grammar_sessions()

    all_users = await db.get_all_users()
    user_ids = {u["user_id"] for u in all_users}
    user_ids.add(config.AUTHORIZED_CHAT_ID)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Start Grammar", callback_data="start_grammar_session"),
            InlineKeyboardButton("🗂️ Start Vocab", callback_data="start_session"),
        ]
    ])

    for user_id in user_ids:
        try:
            settings = await db.get_user_settings(user_id)
            cefr = settings["cefr_levels"]

            grammar_due, vocab_due = await asyncio.gather(
                db.count_due_grammar_cards(user_id, cefr_levels=cefr),
                db.count_due_cards(user_id, cefr_levels=cefr),
            )
            grammar_display = grammar_due + (20 if grammar_due <= 150 else 0)
            vocab_display = vocab_due + (20 if vocab_due <= 150 else 0)

            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"Guten Morgen! 🌅\n\n"
                    f"📚 Grammar: {grammar_display} card{'s' if grammar_display != 1 else ''}\n"
                    f"🗂️ Vocab: {vocab_display} card{'s' if vocab_display != 1 else ''}"
                ),
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning("morning_trigger failed for user %s: %s", user_id, e)
```

- [ ] **Step 2: Add `asyncio` import to scheduler.py**

At the top of `bot/scheduler.py`, after `import logging`, add:

```python
import asyncio
```

- [ ] **Step 3: Update `nag_check` to cover both sessions**

Replace the entire `nag_check` function with:

```python
async def nag_check(bot) -> None:
    """Remind users who haven't finished one or both sessions today."""
    all_users = await db.get_all_users()
    user_ids = {u["user_id"] for u in all_users}
    user_ids.add(config.AUTHORIZED_CHAT_ID)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Start Grammar", callback_data="start_grammar_session"),
            InlineKeyboardButton("🗂️ Start Vocab", callback_data="start_session"),
        ]
    ])

    for user_id in user_ids:
        try:
            grammar_session = qm.get_grammar_session(user_id)
            vocab_session = qm.get_session(user_id)

            # Both done — skip entirely
            if grammar_session.kill_switch and vocab_session.kill_switch:
                continue

            settings = await db.get_user_settings(user_id)
            cefr = settings["cefr_levels"]

            # Grammar remaining
            grammar_remaining = 0
            if not grammar_session.kill_switch:
                if grammar_session.active:
                    grammar_remaining = grammar_session.remaining_count()
                else:
                    grammar_due = await db.count_due_grammar_cards(user_id, cefr_levels=cefr)
                    grammar_remaining = grammar_due + (20 if grammar_due <= 150 else 0)

            # Vocab remaining
            vocab_remaining = 0
            if not vocab_session.kill_switch:
                if vocab_session.active:
                    vocab_remaining = vocab_session.remaining_count()
                else:
                    vocab_due = await db.count_due_cards(user_id, cefr_levels=cefr)
                    vocab_remaining = vocab_due + (20 if vocab_due <= 150 else 0)

            if grammar_remaining == 0 and vocab_remaining == 0:
                continue

            parts = []
            if grammar_remaining > 0:
                parts.append(
                    f"📚 {grammar_remaining} grammar card{'s' if grammar_remaining != 1 else ''}"
                )
            if vocab_remaining > 0:
                parts.append(
                    f"🗂️ {vocab_remaining} vocab card{'s' if vocab_remaining != 1 else ''}"
                )

            remaining_text = " and ".join(parts)
            total = grammar_remaining + vocab_remaining
            tomorrow_pile = total * 2

            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"⏰ You still have {remaining_text} left for today.\n\n"
                    f"Skip today and these pile onto tomorrow — "
                    f"you could be facing ~{tomorrow_pile} instead of ~{total}. "
                    f"A few minutes now saves double the work tomorrow! 💪"
                ),
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning("nag_check failed for user %s: %s", user_id, e)
```

- [ ] **Step 4: Add grammar queue manager import to scheduler.py**

The `qm` import already exists (`from bot import queue_manager as qm`). The new `get_grammar_session` and `reset_all_grammar_sessions` will be available on that module. No import change needed.

- [ ] **Step 5: Commit**

```bash
git add bot/scheduler.py
git commit -m "feat: update morning trigger and nag for grammar+vocab dual sessions"
```

---

## Task 6: Register Handlers in main.py

**Files:**
- Modify: `bot/main.py`

- [ ] **Step 1: Update imports in `bot/main.py`**

Replace the existing import block from `bot.handlers` with:

```python
from bot.handlers import (
    cmd_start,
    cmd_session,
    cmd_vocab,
    cmd_grammar,
    cmd_stats,
    cmd_create_invite,
    cmd_login,
    cmd_settings,
    callback_start_session,
    callback_start_grammar_session,
    callback_show_answer,
    callback_show_grammar_answer,
    callback_grade,
    callback_grade_grammar,
    callback_settings_direction,
    callback_settings_cefr,
)
```

- [ ] **Step 2: Register new handlers inside `main()`**

After the existing `app.add_handler(CommandHandler("settings", cmd_settings))` line, add:

```python
    app.add_handler(CommandHandler("grammar", cmd_grammar))
    app.add_handler(CommandHandler("vocab", cmd_vocab))
```

After the existing `CallbackQueryHandler(callback_start_session, ...)` line, add:

```python
    app.add_handler(
        CallbackQueryHandler(callback_start_grammar_session, pattern="^start_grammar_session$")
    )
```

After the existing `CallbackQueryHandler(callback_show_answer, ...)` line, add:

```python
    app.add_handler(
        CallbackQueryHandler(callback_show_grammar_answer, pattern="^show_grammar:")
    )
```

After the existing `CallbackQueryHandler(callback_grade, ...)` line, add:

```python
    app.add_handler(CallbackQueryHandler(callback_grade_grammar, pattern="^grade_grammar:"))
```

- [ ] **Step 3: Update bot command menus in `set_commands`**

Replace the `user_commands` list in `set_commands` with:

```python
        user_commands = [
            BotCommand("grammar", "📚 Start grammar session"),
            BotCommand("vocab", "▶️ Start vocabulary session"),
            BotCommand("session", "▶️ Start vocabulary session"),
            BotCommand("stats", "📊 Grammar + vocabulary progress"),
            BotCommand("settings", "⚙️ Study direction & CEFR levels"),
            BotCommand("login", "🔑 Register with an invite code"),
            BotCommand("start", "ℹ️ About this bot"),
        ]
```

- [ ] **Step 4: Commit**

```bash
git add bot/main.py
git commit -m "feat: register grammar handlers and update bot command menu"
```

---

## Task 7: Deploy to Google Cloud

- [ ] **Step 1: Confirm cloudbuild.yaml is configured for deployment**

```bash
cat cloudbuild.yaml
```

Verify it builds and deploys the bot (e.g., to Cloud Run or GCE). No changes expected.

- [ ] **Step 2: Push the branch and trigger a deploy**

```bash
git push
```

If using Cloud Build trigger on push, the deploy will start automatically. Monitor in the Cloud Build console.

- [ ] **Step 3: Verify grammar cards are in Firestore**

In the Firebase console → Firestore → `grammar_cards` collection, confirm documents exist with fields: word, translation, german_sentence, english_translation, cefr_level (A1_Grammar / A2_Grammar / B1_Grammar / B2_Grammar).

- [ ] **Step 4: Smoke test `/grammar` command in Telegram**

Send `/grammar` to the bot. Expected:
- Bot replies with a grammar card showing a German sentence and a "Show Answer" button
- Tapping "Show Answer" shows the English translation, grammar concept, and Again/Hard/Good/Easy buttons
- Rating a card advances to the next one
- Finishing all cards shows the completion message

- [ ] **Step 5: Smoke test `/stats` command**

Send `/stats`. Expected: Two sections — Grammar and Vocabulary — each with New/Learning/Review/Relearning counts.

- [ ] **Step 6: Verify morning message format**

Trigger `morning_trigger` manually (or wait until 8:00 Berlin time). Expected: One message with both grammar and vocab counts, two Start buttons.

---

## Self-Review Notes

**Spec coverage check:**
- ✅ Upload grammar JSON files → Task 1
- ✅ Grammar cards in new Firestore collection → Task 1 + Task 2
- ✅ Full FSRS for grammar → Task 2 (`get_due_grammar_cards`, `update_grammar_card_after_review`)
- ✅ `/grammar` command → Task 4
- ✅ `/vocab` alias → Task 4
- ✅ Two separate sessions per day → Tasks 3+4+5
- ✅ Combined morning message (grammar+vocab counts, 2 buttons) → Task 5
- ✅ Nag covers both sessions → Task 5
- ✅ `/stats` shows both grammar and vocabulary → Task 4
- ✅ Grammar CEFR filter mapped from user settings → Task 2 (`_grammar_cefr_levels`)
- ✅ Registered handlers in main.py → Task 6
- ✅ Deploy → Task 7

**Type/method consistency:**
- `qm.get_grammar_session(user_id)` defined in Task 3, called in Tasks 4+5 ✅
- `qm.reset_all_grammar_sessions()` defined in Task 3, called in Task 5 ✅
- `db.get_due_grammar_cards`, `db.get_new_grammar_cards`, `db.count_due_grammar_cards`, `db.get_grammar_card_by_id`, `db.update_grammar_card_after_review`, `db.get_grammar_card_counts_by_state` — all defined in Task 2, called in Tasks 4+5 ✅
- `callback_start_grammar_session`, `callback_show_grammar_answer`, `callback_grade_grammar`, `cmd_grammar`, `cmd_vocab` — all defined in Task 4, imported in Task 6 ✅
- Callback patterns: `start_grammar_session`, `show_grammar:`, `grade_grammar:` — consistent across Tasks 4+6 ✅
