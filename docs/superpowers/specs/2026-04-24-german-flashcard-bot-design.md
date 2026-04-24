# German Flashcard Telegram Bot — Design Spec
**Date:** 2026-04-24
**Status:** Approved

---

## Overview

A 24/7 Telegram flashcard bot for learning German using spaced repetition (FSRS algorithm). Single user, deployed as a Render Background Worker, backed by MongoDB Atlas. 6,734 German→English words sourced from a local JSON file.

---

## Tech Stack

| Concern | Library |
|---|---|
| Telegram bot | python-telegram-bot 21.5 (async, PTB v20+ API) |
| MongoDB async driver | motor 3.5.1 |
| FSRS algorithm | fsrs 1.5.0 |
| Background scheduler | apscheduler 3.10.4 |
| Config/secrets | python-dotenv 1.0.1 |
| Hosting | Render Background Worker (free tier) |
| Database | MongoDB Atlas M0 (free tier) |

---

## Architecture

### Module Map

```
LearnGermanTB/
├── bot/
│   ├── config.py          # All env vars (token, chat ID, Mongo URI)
│   ├── db.py              # Async motor client, all query/update functions
│   ├── fsrs_service.py    # py-fsrs wrapper — rating → new card state
│   ├── queue_manager.py   # Builds & holds the in-memory session queue
│   ├── scheduler.py       # APScheduler jobs (morning trigger + nagging loop)
│   ├── handlers.py        # All PTB callback/command handlers
│   └── main.py            # Wires everything together, starts the bot
├── scripts/
│   └── migrate.py         # One-time JSON → MongoDB upload
├── requirements.txt
└── .env                   # Never committed — holds secrets
```

### Data Flow

1. `scheduler.py` fires at 08:00 Europe/Berlin → calls `db.py` to count due cards → sends Telegram message via bot
2. User taps **[Start Session]** → `handlers.py` calls `queue_manager.py` to build queue from MongoDB → first card sent
3. User taps a grade button → `handlers.py` calls `fsrs_service.py` for new intervals → `db.py` async-updates MongoDB → `queue_manager.py` pops next card
4. Every 2 hours, `scheduler.py` checks `queue_manager.remaining_count()` → sends reminder if > 0, else sets kill switch

---

## Data Model

### `flashcards` collection

```json
{
  "_id": "ObjectId",
  "word": "(he)runterfahren",
  "translation": "to shut down, to reduce, to power down, to go down",
  "fsrs_state": "New",
  "due_date": "ISODate (UTC)",
  "stability": 0.0,
  "difficulty": 0.0,
  "elapsed_days": 0,
  "scheduled_days": 0,
  "reps": 0,
  "lapses": 0,
  "state": 0
}
```

**Field notes:**
- `fsrs_state`: readable string (`New` / `Learning` / `Review` / `Relearning`) derived from py-fsrs `State` enum — used for throttle query
- `due_date`: BSON Date in UTC — enables fast range queries
- All other fields are raw py-fsrs `Card` fields stored flat for single `$set` atomic updates
- Migration sets `due_date = datetime.utcnow()` for all cards so everything is immediately due on day one

**Index** (created by `migrate.py`):
```
db.flashcards.create_index([("due_date", 1), ("fsrs_state", 1)])
```

---

## Queue Logic

### Session Queue Build (on [Start Session])

```
Step 1 — Query due/overdue:
  WHERE due_date <= today UTC midnight
  Fetch all, shuffle randomly in Python

Step 2 — Throttle check:
  IF len(due_cards) > 150 → skip new cards entirely
  IF len(due_cards) <= 150 → proceed to Step 3

Step 3 — Query new cards:
  WHERE fsrs_state == "New"
  LIMIT 20
  Append to end of queue (reviews shown first)
```

### In-Memory Session State (`queue_manager.py`)

```python
class SessionQueue:
    queue: list[dict]        # remaining cards to show
    again_pile: list[dict]   # cards graded Again — shown at session end
    active: bool             # True once Start Session tapped
    kill_switch: bool        # True once queue + again_pile both empty
```

### "Again" Card Handling

- Card graded Again → FSRS updates MongoDB with the short interval (keeps DB consistent)
- Card is also appended to `again_pile` in memory
- When `queue` is exhausted, `again_pile` is appended back to `queue` for one more pass
- After `again_pile` also exhausted → `kill_switch = True`, congratulatory message sent

### Daily Reset

- At 08:00 the scheduler resets `SessionQueue` to fresh empty state
- `kill_switch` cleared at 08:00
- User must tap [Start Session] to build a new queue for the day

---

## Telegram Interface

### Card Message Flow

```
FRONT:
──────────────────────
🇩🇪 (he)runterfahren
──────────────────────
[ Show Answer ]

↓ user taps [Show Answer] — bot EDITS the same message

BACK:
──────────────────────
🇩🇪 (he)runterfahren
🇬🇧 to shut down, to reduce, to power down

[ Again (10m) ]  [ Hard (4d) ]
[ Good (7d) ]    [ Easy (15d) ]
──────────────────────

↓ user taps a grade

→ MongoDB updated async (non-blocking)
→ next card sent as new message
→ previous message inline keyboard removed
```

### Callback Data Format

```
show_answer:<card_id>
grade:<card_id>:<rating>     # rating: 1=Again 2=Hard 3=Good 4=Easy
start_session
```

### Commands

| Command | Action |
|---|---|
| `/start` | Welcome message |
| `/session` | Same as tapping [Start Session] |
| `/stats` | Shows New / Learning / Review / Relearning counts |

### Interval Display

- `fsrs_service.py` exposes a `preview_intervals(card)` function that calls the fsrs library to compute next due dates for all 4 ratings without mutating the card (exact API method confirmed during implementation against fsrs 1.5.0 docs)
- Intervals < 1 day → shown as `10m`, `4h`; ≥ 1 day → shown as `Xd`
- Actual rating applied only when user taps the button

### Guard Rails

- All callback handlers verify `update.effective_user.id == AUTHORIZED_CHAT_ID` — other users silently ignored
- Stale grade callbacks (double-tap) checked against current queue head by `card_id` and ignored

---

## Scheduler & Nagging Loop

**Scheduler type:** `AsyncIOScheduler` (apscheduler) — same event loop as PTB, no threading conflicts
**Timezone:** `Europe/Berlin` for all jobs

### Jobs

**`morning_trigger` — cron 08:00 daily**
1. Reset `SessionQueue` (clear state, clear kill switch)
2. Count cards where `due_date <= today`
3. If count ≤ 150: add 20 to count for display
4. Send: `"Guten Morgen! You have [X] cards due today."` + [Start Session] button

**`nag_check` — interval every 2 hours**
1. If `kill_switch` is True → no-op
2. Otherwise → send: `"Friendly reminder: [X] cards remaining today."` + [Start Session]/[Continue] button

Note: congratulations message and `kill_switch = True` are set by the grade handler the moment the last card is graded — not by `nag_check`. The nag_check only reads the flag.

### Kill Switch Lifecycle

```
08:00 → kill_switch = False, fresh state
  ↓
User completes all cards (queue + again_pile empty)
  ↓
kill_switch = True → nag_check no-ops for rest of day
  ↓
08:00 next day → reset
```

### Edge Cases

- Session completed before first nag fires: kill switch set immediately on last card grade
- Bot restarts mid-day (Render redeploy): `SessionQueue` resets to empty; `/session` command lets user manually restart; all card state is safe in MongoDB

---

## Setup & Deployment

### Step 1 — Telegram Bot Token

1. Message `@BotFather` on Telegram → `/newbot`
2. Choose name and username → save the token
3. Send any message to your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat_id`

### Step 2 — MongoDB Atlas

1. Create free account at mongodb.com/atlas
2. Create free **M0** cluster
3. Database Access → add user with password
4. Network Access → add IP `0.0.0.0/0` (required for Render dynamic IPs)
5. Connect → Drivers → copy connection string
6. Run `scripts/migrate.py` once locally to upload JSON and create index

### Step 3 — Render Deployment

1. Push code to GitHub (private repo is fine)
2. Render Dashboard → New → **Background Worker**
3. Build command: `pip install -r requirements.txt`
4. Start command: `python -m bot.main`
5. Add environment variables:
   ```
   BOT_TOKEN=...
   AUTHORIZED_CHAT_ID=...
   MONGODB_URI=...
   DB_NAME=learngerman
   ```

### Environment Variables

| Key | Example |
|---|---|
| `BOT_TOKEN` | `123456789:ABCdef...` |
| `AUTHORIZED_CHAT_ID` | `123456789` |
| `MONGODB_URI` | `mongodb+srv://user:pass@cluster.mongodb.net/` |
| `DB_NAME` | `learngerman` |

### `requirements.txt`

```
python-telegram-bot==21.5
motor==3.5.1
fsrs==1.5.0
apscheduler==3.10.4
python-dotenv==1.0.1
```

---

## Out of Scope

- Multi-user support
- Audio pronunciation
- Custom FSRS parameters (default parameters used)
- Offline/local fallback
- Web dashboard
