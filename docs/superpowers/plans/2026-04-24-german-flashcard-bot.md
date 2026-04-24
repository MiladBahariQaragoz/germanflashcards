# German Flashcard Telegram Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 24/7 Telegram bot that serves FSRS-scheduled German flashcards from MongoDB Atlas, with a daily morning trigger and nagging reminders until the queue is cleared.

**Architecture:** Modular Python package (`bot/`) with separate files for DB, FSRS logic, session queue, scheduler, and Telegram handlers. APScheduler runs on the same async event loop as python-telegram-bot. Motor (async MongoDB driver) handles all DB I/O without blocking.

**Tech Stack:** python-telegram-bot 21.5, motor 3.5.1, fsrs 1.5.0, apscheduler 3.10.4, python-dotenv 1.0.1, pytest + pytest-asyncio for tests.

---

## File Map

| File | Responsibility |
|---|---|
| `bot/config.py` | Load and validate all env vars at import time |
| `bot/db.py` | All async Motor queries and updates |
| `bot/fsrs_service.py` | FSRS Card↔dict conversion, rating, interval preview |
| `bot/queue_manager.py` | In-memory session queue: build, pop, again pile, kill switch |
| `bot/handlers.py` | All PTB command and callback handlers |
| `bot/scheduler.py` | APScheduler job definitions (morning trigger, nag check) |
| `bot/main.py` | Wire app + scheduler + handlers; start polling |
| `scripts/migrate.py` | One-time JSON → MongoDB upload with index creation |
| `tests/test_fsrs_service.py` | Unit tests for FSRS wrapper |
| `tests/test_queue_manager.py` | Unit tests for session queue logic |

---

## Task 1: Project Scaffold

**Files:**
- Create: `bot/__init__.py`
- Create: `tests/__init__.py`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `.env.example`
- Create: `.gitignore`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p bot scripts tests
touch bot/__init__.py tests/__init__.py
```

- [ ] **Step 2: Write requirements.txt**

```
python-telegram-bot==21.5
motor==3.5.1
fsrs==1.5.0
apscheduler==3.10.4
python-dotenv==1.0.1
pytz==2024.1
```

Save to `requirements.txt`.

- [ ] **Step 3: Write requirements-dev.txt**

```
pytest==8.2.0
pytest-asyncio==0.23.7
```

Save to `requirements-dev.txt`.

- [ ] **Step 4: Write .env.example**

```
BOT_TOKEN=123456789:ABCdef...
AUTHORIZED_CHAT_ID=123456789
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/
DB_NAME=learngerman
```

Save to `.env.example`.

- [ ] **Step 5: Write .gitignore**

```
.env
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
dist/
.venv/
```

Save to `.gitignore`.

- [ ] **Step 6: Install dependencies**

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Expected: all packages install without error.

- [ ] **Step 7: Commit**

```bash
git init
git add requirements.txt requirements-dev.txt .env.example .gitignore bot/__init__.py tests/__init__.py
git commit -m "feat: project scaffold"
```

---

## Task 2: bot/config.py

**Files:**
- Create: `bot/config.py`

- [ ] **Step 1: Write config.py**

```python
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
AUTHORIZED_CHAT_ID: int = int(os.environ["AUTHORIZED_CHAT_ID"])
MONGODB_URI: str = os.environ["MONGODB_URI"]
DB_NAME: str = os.environ["DB_NAME"]
```

Save to `bot/config.py`.

- [ ] **Step 2: Create .env from .env.example and fill in real values**

```bash
cp .env.example .env
# Edit .env and fill in your BOT_TOKEN, AUTHORIZED_CHAT_ID, MONGODB_URI, DB_NAME
```

- [ ] **Step 3: Verify config loads**

```bash
python -c "from bot.config import BOT_TOKEN, AUTHORIZED_CHAT_ID; print('OK', AUTHORIZED_CHAT_ID)"
```

Expected: prints `OK` followed by your chat ID number.

- [ ] **Step 4: Commit**

```bash
git add bot/config.py
git commit -m "feat: config module with env var loading"
```

---

## Task 3: bot/fsrs_service.py + Tests

**Files:**
- Create: `bot/fsrs_service.py`
- Create: `tests/test_fsrs_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fsrs_service.py
from datetime import datetime, timezone
from bot.fsrs_service import rate_card, preview_intervals, format_interval

NEW_CARD = {
    "stability": 0.0,
    "difficulty": 0.0,
    "elapsed_days": 0,
    "scheduled_days": 0,
    "reps": 0,
    "lapses": 0,
    "state": 0,
    "due_date": datetime(2026, 4, 24, 0, 0, 0, tzinfo=timezone.utc),
}


def test_rate_card_good_returns_update_dict():
    update_dict, _ = rate_card(NEW_CARD, rating_int=3)
    assert "due_date" in update_dict
    assert "stability" in update_dict
    assert "difficulty" in update_dict
    assert "fsrs_state" in update_dict
    assert update_dict["reps"] == 1


def test_rate_card_again_lapses_incremented():
    update_dict, _ = rate_card(NEW_CARD, rating_int=1)
    assert update_dict["lapses"] == 1


def test_rate_card_good_increases_stability():
    update_dict, _ = rate_card(NEW_CARD, rating_int=3)
    assert update_dict["stability"] > 0.0


def test_preview_intervals_returns_four_entries():
    intervals = preview_intervals(NEW_CARD)
    assert set(intervals.keys()) == {1, 2, 3, 4}
    for v in intervals.values():
        assert isinstance(v, str)


def test_format_interval_minutes():
    assert format_interval(0) == "10m"


def test_format_interval_days():
    assert format_interval(7) == "7d"
```

Save to `tests/test_fsrs_service.py`.

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_fsrs_service.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `fsrs_service` does not exist yet.

- [ ] **Step 3: Write bot/fsrs_service.py**

```python
from datetime import datetime, timezone
from fsrs import FSRS, Card, Rating, State

_scheduler = FSRS()

_STATE_NAMES = {
    State.New: "New",
    State.Learning: "Learning",
    State.Review: "Review",
    State.Relearning: "Relearning",
}


def _dict_to_card(card_dict: dict) -> Card:
    card = Card()
    card.stability = card_dict.get("stability", 0.0)
    card.difficulty = card_dict.get("difficulty", 0.0)
    card.elapsed_days = card_dict.get("elapsed_days", 0)
    card.scheduled_days = card_dict.get("scheduled_days", 0)
    card.reps = card_dict.get("reps", 0)
    card.lapses = card_dict.get("lapses", 0)
    card.state = State(card_dict.get("state", 0))
    raw_due = card_dict.get("due_date", datetime.now(timezone.utc))
    if raw_due.tzinfo is None:
        raw_due = raw_due.replace(tzinfo=timezone.utc)
    card.due = raw_due
    return card


def _card_to_update_dict(card: Card) -> dict:
    return {
        "due_date": card.due,
        "stability": card.stability,
        "difficulty": card.difficulty,
        "elapsed_days": card.elapsed_days,
        "scheduled_days": card.scheduled_days,
        "reps": card.reps,
        "lapses": card.lapses,
        "state": card.state.value,
        "fsrs_state": _STATE_NAMES[card.state],
    }


def rate_card(card_dict: dict, rating_int: int) -> tuple[dict, str]:
    """Apply rating to card. Returns (mongo_update_fields, interval_label)."""
    card = _dict_to_card(card_dict)
    now = datetime.now(timezone.utc)
    rating = Rating(rating_int)
    scheduling_cards = _scheduler.repeat(card, now)
    updated_card = scheduling_cards[rating].card
    label = format_interval_from_due(updated_card.due, now)
    return _card_to_update_dict(updated_card), label


def preview_intervals(card_dict: dict) -> dict[int, str]:
    """Return interval labels for all 4 ratings without mutating the card."""
    card = _dict_to_card(card_dict)
    now = datetime.now(timezone.utc)
    scheduling_cards = _scheduler.repeat(card, now)
    return {
        rating.value: format_interval_from_due(scheduling_cards[rating].card.due, now)
        for rating in Rating
    }


def format_interval_from_due(due: datetime, now: datetime) -> str:
    delta_seconds = max(0, (due - now).total_seconds())
    if delta_seconds < 3600:
        return f"{max(1, round(delta_seconds / 60))}m"
    if delta_seconds < 86400:
        return f"{round(delta_seconds / 3600)}h"
    return f"{round(delta_seconds / 86400)}d"


def format_interval(scheduled_days: int) -> str:
    if scheduled_days == 0:
        return "10m"
    return f"{scheduled_days}d"
```

Save to `bot/fsrs_service.py`.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_fsrs_service.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/fsrs_service.py tests/test_fsrs_service.py
git commit -m "feat: fsrs_service wrapper with rating and interval preview"
```

---

## Task 4: bot/queue_manager.py + Tests

**Files:**
- Create: `bot/queue_manager.py`
- Create: `tests/test_queue_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_queue_manager.py
import pytest
from bot.queue_manager import SessionQueue

CARD_A = {"_id": "aaa", "word": "Hund", "translation": "dog"}
CARD_B = {"_id": "bbb", "word": "Katze", "translation": "cat"}
CARD_C = {"_id": "ccc", "word": "Baum", "translation": "tree"}


def make_queue(due=None, new=None):
    q = SessionQueue()
    q.build(due_cards=due or [], new_cards=new or [])
    return q


def test_build_combines_due_then_new():
    q = make_queue(due=[CARD_A], new=[CARD_B])
    assert q.remaining_count() == 2


def test_pop_returns_cards_in_order():
    q = make_queue(due=[CARD_A, CARD_B])
    first = q.pop_next()
    assert first["_id"] in ("aaa", "bbb")
    assert q.remaining_count() == 1


def test_pop_returns_none_when_empty():
    q = make_queue()
    assert q.pop_next() is None


def test_again_pile_appended_after_main_queue():
    q = make_queue(due=[CARD_A])
    q.pop_next()
    q.add_to_again_pile(CARD_B)
    assert q.remaining_count() == 1
    second = q.pop_next()
    assert second["_id"] == "bbb"


def test_kill_switch_false_initially():
    q = make_queue(due=[CARD_A])
    assert q.kill_switch is False


def test_kill_switch_set_when_all_exhausted():
    q = make_queue(due=[CARD_A])
    q.pop_next()
    q.check_and_set_kill_switch()
    assert q.kill_switch is True


def test_reset_clears_state():
    q = make_queue(due=[CARD_A])
    q.kill_switch = True
    q.reset()
    assert q.remaining_count() == 0
    assert q.kill_switch is False
    assert q.active is False


def test_throttle_no_new_cards_when_due_exceeds_150():
    due_cards = [{"_id": str(i)} for i in range(151)]
    new_cards = [{"_id": "new_1"}]
    q = SessionQueue()
    q.build(due_cards=due_cards, new_cards=new_cards)
    assert q.remaining_count() == 151
```

Save to `tests/test_queue_manager.py`.

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_queue_manager.py -v
```

Expected: `ImportError` — module does not exist.

- [ ] **Step 3: Write bot/queue_manager.py**

```python
import random


class SessionQueue:
    def __init__(self):
        self.queue: list[dict] = []
        self.again_pile: list[dict] = []
        self.active: bool = False
        self.kill_switch: bool = False

    def build(self, due_cards: list[dict], new_cards: list[dict]) -> None:
        shuffled = list(due_cards)
        random.shuffle(shuffled)
        if len(due_cards) <= 150:
            shuffled.extend(new_cards)
        self.queue = shuffled
        self.again_pile = []
        self.active = True
        self.kill_switch = False

    def pop_next(self) -> dict | None:
        if self.queue:
            return self.queue.pop(0)
        if self.again_pile:
            self.queue = list(self.again_pile)
            self.again_pile = []
            return self.queue.pop(0)
        return None

    def add_to_again_pile(self, card: dict) -> None:
        self.again_pile.append(card)

    def remaining_count(self) -> int:
        return len(self.queue) + len(self.again_pile)

    def check_and_set_kill_switch(self) -> bool:
        if len(self.queue) == 0 and len(self.again_pile) == 0:
            self.kill_switch = True
            return True
        return False

    def reset(self) -> None:
        self.queue = []
        self.again_pile = []
        self.active = False
        self.kill_switch = False


# Module-level singleton shared across handlers and scheduler
session = SessionQueue()
```

Save to `bot/queue_manager.py`.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_queue_manager.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/queue_manager.py tests/test_queue_manager.py
git commit -m "feat: session queue with again pile and throttle logic"
```

---

## Task 5: scripts/migrate.py

**Files:**
- Create: `scripts/migrate.py`

- [ ] **Step 1: Write migrate.py**

```python
"""One-time script: upload combined_words.json to MongoDB and create index."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import motor.motor_asyncio
from dotenv import load_dotenv
import os

load_dotenv()

MONGODB_URI = os.environ["MONGODB_URI"]
DB_NAME = os.environ["DB_NAME"]
JSON_PATH = Path(__file__).parent.parent / "combined_words.json"


def make_document(word: str, translation: str) -> dict:
    return {
        "word": word,
        "translation": translation,
        "fsrs_state": "New",
        "due_date": datetime.now(timezone.utc),
        "stability": 0.0,
        "difficulty": 0.0,
        "elapsed_days": 0,
        "scheduled_days": 0,
        "reps": 0,
        "lapses": 0,
        "state": 0,
    }


async def migrate():
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
    db = client[DB_NAME]
    collection = db["flashcards"]

    count = await collection.count_documents({})
    if count > 0:
        print(f"Collection already has {count} documents. Skipping upload.")
        print("To re-run, drop the collection first in Atlas.")
        client.close()
        return

    print(f"Loading {JSON_PATH} ...")
    with open(JSON_PATH, encoding="utf-8") as f:
        words = json.load(f)

    documents = [make_document(w["word"], w["translation"]) for w in words]
    print(f"Inserting {len(documents)} documents ...")
    result = await collection.insert_many(documents)
    print(f"Inserted {len(result.inserted_ids)} documents.")

    print("Creating index on (due_date, fsrs_state) ...")
    await collection.create_index([("due_date", 1), ("fsrs_state", 1)])
    print("Index created.")

    client.close()
    print("Migration complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
```

Save to `scripts/migrate.py`.

- [ ] **Step 2: Run the migration**

```bash
python scripts/migrate.py
```

Expected output:
```
Loading .../combined_words.json ...
Inserting 6734 documents ...
Inserted 6734 documents.
Creating index on (due_date, fsrs_state) ...
Index created.
Migration complete.
```

- [ ] **Step 3: Verify in Atlas**

Log in to MongoDB Atlas → Browse Collections → `learngerman.flashcards`. Confirm documents exist with all FSRS fields present.

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate.py
git commit -m "feat: one-time migration script for JSON to MongoDB"
```

---

## Task 6: bot/db.py

**Files:**
- Create: `bot/db.py`

- [ ] **Step 1: Write bot/db.py**

```python
from datetime import datetime, timezone

import motor.motor_asyncio
from bson import ObjectId

from bot.config import MONGODB_URI, DB_NAME

_client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
_db = _client[DB_NAME]
_col = _db["flashcards"]


def _end_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=23, minute=59, second=59, microsecond=999999)


async def get_due_cards() -> list[dict]:
    """Returns non-New cards with due_date <= end of today.
    New cards are handled separately to enforce the 20/day limit."""
    cutoff = _end_of_today_utc()
    cursor = _col.find({
        "due_date": {"$lte": cutoff},
        "fsrs_state": {"$ne": "New"},
    })
    return await cursor.to_list(length=None)


async def count_due_cards() -> int:
    cutoff = _end_of_today_utc()
    return await _col.count_documents({
        "due_date": {"$lte": cutoff},
        "fsrs_state": {"$ne": "New"},
    })


async def get_new_cards(limit: int = 20) -> list[dict]:
    cursor = _col.find({"fsrs_state": "New"}).limit(limit)
    return await cursor.to_list(length=limit)


async def get_card_by_id(card_id: ObjectId) -> dict | None:
    return await _col.find_one({"_id": card_id})


async def update_card_after_review(card_id: ObjectId, update_fields: dict) -> None:
    await _col.update_one({"_id": card_id}, {"$set": update_fields})


async def get_card_counts_by_state() -> dict[str, int]:
    pipeline = [{"$group": {"_id": "$fsrs_state", "count": {"$sum": 1}}}]
    results = await _col.aggregate(pipeline).to_list(length=10)
    counts = {"New": 0, "Learning": 0, "Review": 0, "Relearning": 0}
    for r in results:
        if r["_id"] in counts:
            counts[r["_id"]] = r["count"]
    return counts
```

Save to `bot/db.py`.

- [ ] **Step 2: Smoke-test db.py**

```bash
python -c "
import asyncio
from bot.db import count_due_cards, get_card_counts_by_state
async def check():
    due = await count_due_cards()
    states = await get_card_counts_by_state()
    print('Due today:', due)
    print('States:', states)
asyncio.run(check())
"
```

Expected: prints due count (should be 6734 on day 1) and `{'New': 6734, 'Learning': 0, 'Review': 0, 'Relearning': 0}`.

- [ ] **Step 3: Commit**

```bash
git add bot/db.py
git commit -m "feat: async motor db layer with due card queries and state counts"
```

---

## Task 7: bot/handlers.py

**Files:**
- Create: `bot/handlers.py`

- [ ] **Step 1: Write bot/handlers.py**

```python
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bson import ObjectId

from bot import config
from bot import db
from bot import fsrs_service
from bot import queue_manager as qm


def _auth(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == config.AUTHORIZED_CHAT_ID


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _auth(update):
        return
    await update.message.reply_text(
        "Willkommen! Use /session to start studying or /stats to see your progress."
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _auth(update):
        return
    counts = await db.get_card_counts_by_state()
    text = (
        f"New: {counts['New']}\n"
        f"Learning: {counts['Learning']}\n"
        f"Review: {counts['Review']}\n"
        f"Relearning: {counts['Relearning']}"
    )
    await update.message.reply_text(text)


async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _auth(update):
        return
    await _start_session(context, chat_id=update.effective_chat.id)


async def callback_start_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not _auth(update):
        return
    await _start_session(context, chat_id=update.effective_chat.id)


async def _start_session(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    due = await db.get_due_cards()
    new = await db.get_new_cards(20) if len(due) <= 150 else []
    qm.session.build(due_cards=due, new_cards=new)
    card = qm.session.pop_next()
    if card is None:
        await context.bot.send_message(chat_id=chat_id, text="No cards due today!")
        return
    await _send_card_front(context, chat_id, card)


async def _send_card_front(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, card: dict
) -> None:
    card_id = str(card["_id"])
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Show Answer", callback_data=f"show_answer:{card_id}")]]
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🇩🇪 {card['word']}",
        reply_markup=keyboard,
    )


async def callback_show_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not _auth(update):
        return

    card_id_str = query.data.split(":", 1)[1]
    card_id = ObjectId(card_id_str)

    card = await db.get_card_by_id(card_id)
    if card is None:
        await query.edit_message_text("Card not found.")
        return

    intervals = fsrs_service.preview_intervals(card)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Again ({intervals[1]})",
                    callback_data=f"grade:{card_id_str}:1",
                ),
                InlineKeyboardButton(
                    f"Hard ({intervals[2]})",
                    callback_data=f"grade:{card_id_str}:2",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"Good ({intervals[3]})",
                    callback_data=f"grade:{card_id_str}:3",
                ),
                InlineKeyboardButton(
                    f"Easy ({intervals[4]})",
                    callback_data=f"grade:{card_id_str}:4",
                ),
            ],
        ]
    )
    await query.edit_message_text(
        text=f"🇩🇪 {card['word']}\n🇬🇧 {card['translation']}",
        reply_markup=keyboard,
    )


async def callback_grade(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not _auth(update):
        return

    _, card_id_str, rating_str = query.data.split(":")
    card_id = ObjectId(card_id_str)
    rating_int = int(rating_str)

    card = await db.get_card_by_id(card_id)
    if card is None:
        return

    update_fields, _ = fsrs_service.rate_card(card, rating_int)
    await db.update_card_after_review(card_id, update_fields)

    # Remove inline buttons from graded message
    await query.edit_message_reply_markup(reply_markup=None)

    if rating_int == 1:
        updated_card = {**card, **update_fields}
        qm.session.add_to_again_pile(updated_card)

    next_card = qm.session.pop_next()
    chat_id = update.effective_chat.id

    if next_card is None:
        qm.session.check_and_set_kill_switch()
        await context.bot.send_message(
            chat_id=chat_id,
            text="Glückwunsch! All done for today. See you tomorrow! 🎉",
        )
        return

    await _send_card_front(context, chat_id, next_card)
```

Save to `bot/handlers.py`.

- [ ] **Step 2: Commit**

```bash
git add bot/handlers.py
git commit -m "feat: telegram handlers for session start, show answer, and grading"
```

---

## Task 8: bot/scheduler.py

**Files:**
- Create: `bot/scheduler.py`

- [ ] **Step 1: Write bot/scheduler.py**

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pytz import timezone as pytz_timezone

from bot import config
from bot import db
from bot import queue_manager as qm
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


BERLIN = pytz_timezone("Europe/Berlin")


async def morning_trigger(bot) -> None:
    qm.session.reset()
    due_count = await db.count_due_cards()
    display_count = due_count + (20 if due_count <= 150 else 0)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Start Session", callback_data="start_session")]]
    )
    await bot.send_message(
        chat_id=config.AUTHORIZED_CHAT_ID,
        text=f"Guten Morgen! You have {display_count} cards due today.",
        reply_markup=keyboard,
    )


async def nag_check(bot) -> None:
    if qm.session.kill_switch:
        return
    remaining = qm.session.remaining_count()
    if not qm.session.active:
        due_count = await db.count_due_cards()
        display_count = due_count + (20 if due_count <= 150 else 0)
        remaining = display_count
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Start Session", callback_data="start_session")]]
    )
    await bot.send_message(
        chat_id=config.AUTHORIZED_CHAT_ID,
        text=f"Friendly reminder: {remaining} cards remaining today.",
        reply_markup=keyboard,
    )


def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=BERLIN)
    scheduler.add_job(
        morning_trigger,
        CronTrigger(hour=8, minute=0, timezone=BERLIN),
        args=[bot],
        id="morning_trigger",
    )
    scheduler.add_job(
        nag_check,
        IntervalTrigger(hours=2),
        args=[bot],
        id="nag_check",
    )
    return scheduler
```

Save to `bot/scheduler.py`.

- [ ] **Step 2: Commit**

```bash
git add bot/scheduler.py
git commit -m "feat: apscheduler morning trigger and nag check jobs"
```

---

## Task 9: bot/main.py

**Files:**
- Create: `bot/main.py`

- [ ] **Step 1: Write bot/main.py**

```python
import logging
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
)

from bot.config import BOT_TOKEN
from bot.handlers import (
    cmd_start,
    cmd_session,
    cmd_stats,
    callback_start_session,
    callback_show_answer,
    callback_grade,
)
from bot.scheduler import setup_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("session", cmd_session))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(
        CallbackQueryHandler(callback_start_session, pattern="^start_session$")
    )
    app.add_handler(
        CallbackQueryHandler(callback_show_answer, pattern="^show_answer:")
    )
    app.add_handler(CallbackQueryHandler(callback_grade, pattern="^grade:"))

    scheduler = setup_scheduler(app.bot)
    scheduler.start()

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
```

Save to `bot/main.py`.

- [ ] **Step 2: Commit**

```bash
git add bot/main.py
git commit -m "feat: main entry point wiring handlers and scheduler"
```

---

## Task 10: Local Smoke Test

- [ ] **Step 1: Run all unit tests**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 2: Start the bot locally**

```bash
python -m bot.main
```

Expected: logs show `Application started` and `Scheduler started` with no errors.

- [ ] **Step 3: Send /start to your bot on Telegram**

Expected: bot replies `Willkommen! Use /session to start studying or /stats to see your progress.`

- [ ] **Step 4: Send /stats to your bot**

Expected: bot replies with counts like:
```
New: 6734
Learning: 0
Review: 0
Relearning: 0
```

- [ ] **Step 5: Send /session to your bot**

Expected: bot sends a message with a German word and a [Show Answer] button.

- [ ] **Step 6: Tap Show Answer**

Expected: message edits to show the German word + English translation + 4 grade buttons with intervals.

- [ ] **Step 7: Tap Good**

Expected: grade buttons disappear from previous message, next card appears.

- [ ] **Step 8: Stop the bot with Ctrl+C**

- [ ] **Step 9: Verify MongoDB updated**

```bash
python -c "
import asyncio
from bot.db import get_card_counts_by_state
async def check():
    print(await get_card_counts_by_state())
asyncio.run(check())
"
```

Expected: `Learning` or `Review` count is now > 0 (the graded card moved out of `New`).

---

## Task 11: Render Deployment

- [ ] **Step 1: Push to GitHub**

```bash
git remote add origin https://github.com/YOUR_USERNAME/LearnGermanTB.git
git push -u origin main
```

- [ ] **Step 2: Create Render Background Worker**

1. Go to dashboard.render.com → New → Background Worker
2. Connect your GitHub repo
3. Name: `learngerman-bot`
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `python -m bot.main`
6. Click **Advanced** → add these Environment Variables:
   - `BOT_TOKEN` = your token from BotFather
   - `AUTHORIZED_CHAT_ID` = your Telegram chat ID
   - `MONGODB_URI` = your Atlas connection string
   - `DB_NAME` = `learngerman`
7. Click **Create Background Worker**

- [ ] **Step 3: Watch deploy logs**

In Render dashboard, click your service → Logs. Expected to see:
```
Application started
Scheduler started
```
with no errors.

- [ ] **Step 4: Test from Telegram**

Send `/session` to your bot. Confirm a card arrives. Grade it. Confirm next card arrives and MongoDB updates.

- [ ] **Step 5: Confirm morning trigger fires at 08:00 Berlin time**

Wait until 08:00 Europe/Berlin. Bot should send `Guten Morgen! You have X cards due today.` with a [Start Session] button.
