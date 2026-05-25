# V2 Multi-User Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement OTP invite flow, user registration/auth, per-user settings, and multi-user scheduler to complete the V2 plan.

**Architecture:** New DB functions handle OTP and user collections; `_auth()` becomes async and checks the `users` collection; `/settings` stores preferences in the user doc and they feed into card queries; the scheduler iterates all registered users instead of just the admin.

**Tech Stack:** Python 3.11+, python-telegram-bot v21.5, google-cloud-firestore 2.19, apscheduler

---

## File Map

| File | Change |
|---|---|
| `bot/db.py` | Add OTP CRUD + user CRUD functions |
| `bot/handlers.py` | Add `/create_invite`, `/login`, `/settings`; async-ify `_auth`; direction-aware card display |
| `bot/scheduler.py` | `morning_trigger` and `nag_check` iterate all registered users |
| `bot/main.py` | Register 3 new command handlers + settings callback handlers |

---

## Task 1: DB layer — OTP and User functions

**Files:**
- Modify: `bot/db.py`

### Background

The Firestore `users` collection stores:
```json
{
  "user_id": 123456789,
  "username": "johndoe",
  "registered_at": "<timestamp>",
  "study_direction": "DE->EN",
  "cefr_levels": ["A1", "A2", "B1", "B2"]
}
```

The `otps` collection stores:
```json
{
  "code": "abc123xyz",
  "created_at": "<timestamp>",
  "expires_at": "<timestamp>",
  "used": false
}
```

- [ ] **Step 1: Add OTP and user collection references + helper imports**

Open `bot/db.py`. After the existing `_progress_col` line, add:

```python
import secrets
from datetime import timedelta

_users_col = _db.collection("users")
_otps_col = _db.collection("otps")
```

- [ ] **Step 2: Add `create_otp` function**

Append to `bot/db.py`:

```python
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
```

- [ ] **Step 3: Add `consume_otp` function**

Append to `bot/db.py`:

```python
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
```

- [ ] **Step 4: Add user CRUD functions**

Append to `bot/db.py`:

```python
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
```

- [ ] **Step 5: Update `get_new_cards` to accept optional CEFR filter**

Replace the existing `get_new_cards` function:

```python
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
```

- [ ] **Step 6: Commit**

```bash
git add bot/db.py
git commit -m "feat(db): add OTP and user CRUD functions; CEFR filter for get_new_cards"
```

---

## Task 2: `/create_invite` admin command

**Files:**
- Modify: `bot/handlers.py`
- Modify: `bot/main.py`

- [ ] **Step 1: Add `cmd_create_invite` to handlers.py**

After the `cmd_stats` function in `bot/handlers.py`, append:

```python
async def cmd_create_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: generate a one-time invite code (valid 48 h) and send it."""
    if update.effective_user is None or update.effective_user.id != config.AUTHORIZED_CHAT_ID:
        return
    code = await db.create_otp(ttl_hours=48)
    await update.message.reply_text(
        f"New invite code (valid 48 h):\n\n`{code}`\n\n"
        "Share this with the person you want to invite. "
        "They should send: `/login {code}`",
        parse_mode="Markdown",
    )
```

- [ ] **Step 2: Register in main.py**

In `bot/main.py`, add the import:

```python
from bot.handlers import (
    cmd_start,
    cmd_session,
    cmd_stats,
    cmd_create_invite,
    callback_start_session,
    callback_show_answer,
    callback_grade,
)
```

And add the handler after `app.add_handler(CommandHandler("stats", cmd_stats))`:

```python
app.add_handler(CommandHandler("create_invite", cmd_create_invite))
```

- [ ] **Step 3: Commit**

```bash
git add bot/handlers.py bot/main.py
git commit -m "feat: add /create_invite admin command"
```

---

## Task 3: `/login` onboarding command

**Files:**
- Modify: `bot/handlers.py`
- Modify: `bot/main.py`

- [ ] **Step 1: Add `cmd_login` to handlers.py**

After `cmd_create_invite` in `bot/handlers.py`, append:

```python
async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow an unregistered user to register using an OTP invite code."""
    user = update.effective_user
    if user is None:
        return

    # Admin never needs to log in
    if user.id == config.AUTHORIZED_CHAT_ID:
        await update.message.reply_text("You're the admin — no login needed!")
        return

    # Already registered?
    if await db.is_registered_user(user.id):
        await update.message.reply_text("You're already registered. Use /session to start.")
        return

    # Require exactly one argument: the OTP code
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: /login <invite_code>")
        return

    code = context.args[0]
    valid = await db.consume_otp(code)
    if not valid:
        await update.message.reply_text(
            "Invalid or expired invite code. Ask the admin for a new one."
        )
        return

    await db.register_user(user.id, user.username)
    await update.message.reply_text(
        "Welcome! You're now registered. 🎉\n\n"
        "Use /session to start studying or /settings to configure your preferences."
    )
```

- [ ] **Step 2: Register in main.py**

Add to the imports in `bot/main.py`:

```python
from bot.handlers import (
    cmd_start,
    cmd_session,
    cmd_stats,
    cmd_create_invite,
    cmd_login,
    callback_start_session,
    callback_show_answer,
    callback_grade,
)
```

Add the handler after `create_invite`:

```python
app.add_handler(CommandHandler("login", cmd_login))
```

- [ ] **Step 3: Commit**

```bash
git add bot/handlers.py bot/main.py
git commit -m "feat: add /login onboarding command with OTP validation"
```

---

## Task 4: Async auth — check users collection

**Files:**
- Modify: `bot/handlers.py`

The current `_auth()` is synchronous and only checks the hardcoded admin ID. Replace it with an async version that also accepts any registered user. The admin bypass ensures the admin is never accidentally locked out.

- [ ] **Step 1: Replace `_auth` with `_is_authorized`**

In `bot/handlers.py`, replace:

```python
def _auth(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == config.AUTHORIZED_CHAT_ID
```

with:

```python
async def _is_authorized(update: Update) -> bool:
    """Return True if the user is the admin OR a registered user."""
    user = update.effective_user
    if user is None:
        return False
    if user.id == config.AUTHORIZED_CHAT_ID:
        return True
    return await db.is_registered_user(user.id)
```

- [ ] **Step 2: Update all handlers that call `_auth`**

Replace every `if not _auth(update):` with `if not await _is_authorized(update):`.

Affected functions: `cmd_start`, `cmd_stats`, `cmd_session`, `callback_start_session`, `callback_show_answer`, `callback_grade`.

The `cmd_login` and `cmd_create_invite` functions handle their own auth checks — do NOT add `_is_authorized` to those.

Final state of each affected function's first two lines (example for `cmd_start`):

```python
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    ...
```

Apply the same pattern to `cmd_stats`, `cmd_session`, `callback_start_session`, `callback_show_answer`, `callback_grade`.

- [ ] **Step 3: Commit**

```bash
git add bot/handlers.py
git commit -m "feat(auth): replace sync _auth with async _is_authorized using users collection"
```

---

## Task 5: `/settings` command — study direction + CEFR levels

**Files:**
- Modify: `bot/handlers.py`
- Modify: `bot/main.py`

The settings menu is an inline keyboard sent in response to `/settings`. Pressing a button toggles the setting and immediately refreshes the keyboard to show the new state.

Callback data format:
- `settings_direction` — toggles between DE→EN and EN→DE
- `settings_cefr:A1` — toggles A1 on/off (same pattern for A2, B1, B2)

- [ ] **Step 1: Add `_settings_keyboard` helper**

Append to `bot/handlers.py`:

```python
def _settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    """Build the settings inline keyboard showing current state."""
    direction = settings["study_direction"]
    cefr = settings["cefr_levels"]

    direction_label = (
        "Direction: 🇩🇪→🇬🇧 (tap to flip)"
        if direction == "DE->EN"
        else "Direction: 🇬🇧→🇩🇪 (tap to flip)"
    )

    def cefr_btn(level: str) -> InlineKeyboardButton:
        active = level in cefr
        label = f"{'✅' if active else '◻'} {level}"
        return InlineKeyboardButton(label, callback_data=f"settings_cefr:{level}")

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(direction_label, callback_data="settings_direction")],
        [cefr_btn("A1"), cefr_btn("A2"), cefr_btn("B1"), cefr_btn("B2")],
    ])
```

- [ ] **Step 2: Add `cmd_settings` handler**

Append to `bot/handlers.py`:

```python
async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    user_id = update.effective_user.id
    settings = await db.get_user_settings(user_id)
    keyboard = _settings_keyboard(settings)
    await update.message.reply_text("⚙️ Your settings:", reply_markup=keyboard)
```

- [ ] **Step 3: Add `callback_settings_direction` handler**

Append to `bot/handlers.py`:

```python
async def callback_settings_direction(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return
    user_id = update.effective_user.id
    settings = await db.get_user_settings(user_id)
    new_direction = "EN->DE" if settings["study_direction"] == "DE->EN" else "DE->EN"
    await db.update_user_settings(user_id, {"study_direction": new_direction})
    settings["study_direction"] = new_direction
    await query.edit_message_reply_markup(reply_markup=_settings_keyboard(settings))
```

- [ ] **Step 4: Add `callback_settings_cefr` handler**

Append to `bot/handlers.py`:

```python
async def callback_settings_cefr(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return
    user_id = update.effective_user.id
    level = query.data.split(":")[1]  # "A1" | "A2" | "B1" | "B2"
    settings = await db.get_user_settings(user_id)
    levels = list(settings["cefr_levels"])
    if level in levels:
        if len(levels) == 1:
            await query.answer("You must keep at least one CEFR level active.", show_alert=True)
            return
        levels.remove(level)
    else:
        levels.append(level)
    await db.update_user_settings(user_id, {"cefr_levels": levels})
    settings["cefr_levels"] = levels
    await query.edit_message_reply_markup(reply_markup=_settings_keyboard(settings))
```

- [ ] **Step 5: Wire settings into `_start_session`**

In `_start_session`, replace:

```python
async def _start_session(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    due = await db.get_due_cards(user_id)
    new = await db.get_new_cards(user_id, 20) if len(due) <= 150 else []
    qm.get_session(user_id).build(due_cards=due, new_cards=new)
```

with:

```python
async def _start_session(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    settings = await db.get_user_settings(user_id)
    due = await db.get_due_cards(user_id)
    new = (
        await db.get_new_cards(user_id, 20, cefr_levels=settings["cefr_levels"])
        if len(due) <= 150
        else []
    )
    qm.get_session(user_id).build(due_cards=due, new_cards=new)
```

- [ ] **Step 6: Wire study direction into card display**

Replace `_send_card_front` and the back-text block in `callback_show_answer`:

```python
async def _send_card_front(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, card: dict, study_direction: str = "DE->EN"
) -> None:
    card_id = card["_id"]
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Show Answer", callback_data=f"show_answer:{card_id}")]]
    )
    cefr = card.get("cefr_level", "?")
    if study_direction == "DE->EN":
        front_text = f"[{cefr}] 🇩🇪 {card['word']}"
        if card.get("german_sentence"):
            front_text += f"\n📝 {card['german_sentence']}"
    else:
        front_text = f"[{cefr}] 🇬🇧 {card.get('translation', '')}"
        if card.get("english_translation"):
            front_text += f"\n📝 {card['english_translation']}"
    await context.bot.send_message(chat_id=chat_id, text=front_text, reply_markup=keyboard)
```

Update `_start_session` to pass the direction:

```python
async def _start_session(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    settings = await db.get_user_settings(user_id)
    due = await db.get_due_cards(user_id)
    new = (
        await db.get_new_cards(user_id, 20, cefr_levels=settings["cefr_levels"])
        if len(due) <= 150
        else []
    )
    qm.get_session(user_id).build(due_cards=due, new_cards=new)
    card = qm.get_session(user_id).pop_next()
    if card is None:
        await context.bot.send_message(chat_id=chat_id, text="No cards due today!")
        return
    await _send_card_front(context, chat_id, card, study_direction=settings["study_direction"])
```

Update `callback_show_answer` back-text block:

```python
async def callback_show_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return

    user_id = update.effective_user.id
    card_id = query.data.split(":", 1)[1]

    card = await db.get_card_by_id(user_id, card_id)
    if card is None:
        await query.edit_message_text("Card not found.")
        return

    settings = await db.get_user_settings(user_id)
    intervals = fsrs_service.preview_intervals(card)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"Again ({intervals[1]})", callback_data=f"grade:{card_id}:1"),
                InlineKeyboardButton(f"Hard ({intervals[2]})", callback_data=f"grade:{card_id}:2"),
            ],
            [
                InlineKeyboardButton(f"Good ({intervals[3]})", callback_data=f"grade:{card_id}:3"),
                InlineKeyboardButton(f"Easy ({intervals[4]})", callback_data=f"grade:{card_id}:4"),
            ],
        ]
    )
    cefr = card.get("cefr_level", "?")
    if settings["study_direction"] == "DE->EN":
        back_text = f"[{cefr}] 🇩🇪 {card['word']}\n🇬🇧 {card['translation']}"
        if card.get("german_sentence"):
            back_text += f"\n\n📝 {card['german_sentence']}"
        if card.get("english_translation"):
            back_text += f"\n🇬🇧 {card['english_translation']}"
    else:
        back_text = f"[{cefr}] 🇬🇧 {card.get('translation', '')}\n🇩🇪 {card['word']}"
        if card.get("english_translation"):
            back_text += f"\n\n📝 {card['english_translation']}"
        if card.get("german_sentence"):
            back_text += f"\n🇩🇪 {card['german_sentence']}"

    await query.edit_message_text(text=back_text, reply_markup=keyboard)
```

Update `callback_grade` to pass direction when showing the next card:

```python
async def callback_grade(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return

    user_id = update.effective_user.id
    _, card_id, rating_str = query.data.split(":")
    rating_int = int(rating_str)

    card = await db.get_card_by_id(user_id, card_id)
    if card is None:
        return

    update_fields, _ = fsrs_service.rate_card(card, rating_int)
    await db.update_card_after_review(user_id, card_id, update_fields)
    await query.edit_message_reply_markup(reply_markup=None)

    if rating_int == 1:
        updated_card = {**card, **update_fields}
        qm.get_session(user_id).add_to_again_pile(updated_card)

    next_card = qm.get_session(user_id).pop_next()
    chat_id = update.effective_chat.id

    if next_card is None:
        qm.get_session(user_id).check_and_set_kill_switch()
        await context.bot.send_message(
            chat_id=chat_id,
            text="Glückwunsch! All done for today. See you tomorrow! 🎉",
        )
        return

    settings = await db.get_user_settings(user_id)
    await _send_card_front(context, chat_id, next_card, study_direction=settings["study_direction"])
```

- [ ] **Step 7: Register new handlers in main.py**

Update imports in `bot/main.py`:

```python
from bot.handlers import (
    cmd_start,
    cmd_session,
    cmd_stats,
    cmd_create_invite,
    cmd_login,
    cmd_settings,
    callback_start_session,
    callback_show_answer,
    callback_grade,
    callback_settings_direction,
    callback_settings_cefr,
)
```

Add after the existing handler registrations:

```python
app.add_handler(CommandHandler("settings", cmd_settings))
app.add_handler(
    CallbackQueryHandler(callback_settings_direction, pattern="^settings_direction$")
)
app.add_handler(
    CallbackQueryHandler(callback_settings_cefr, pattern="^settings_cefr:")
)
```

- [ ] **Step 8: Commit**

```bash
git add bot/handlers.py bot/main.py
git commit -m "feat: add /settings command with study direction and CEFR level toggles"
```

---

## Task 6: Multi-user scheduler

**Files:**
- Modify: `bot/scheduler.py`

Both `morning_trigger` and `nag_check` currently hardcode the admin's chat ID. Replace those with a loop over all users returned by `db.get_all_users()`. The admin is included naturally because `register_user` is called during `/login` — but the admin never calls `/login`. 

**Important:** The admin must always receive their morning message. Since the admin doesn't have a `users` doc (they skip registration), handle them separately: always include `config.AUTHORIZED_CHAT_ID` in the notification loop in addition to the registered users list.

- [ ] **Step 1: Rewrite `morning_trigger`**

Replace the entire `morning_trigger` function:

```python
async def morning_trigger(bot) -> None:
    """Reset all queues and send each user their daily card count."""
    qm.reset_all_sessions()

    all_users = await db.get_all_users()
    # Always include admin (they have no users doc but must always be notified)
    user_ids = {u["user_id"] for u in all_users}
    user_ids.add(config.AUTHORIZED_CHAT_ID)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Start Session", callback_data="start_session")]]
    )

    for user_id in user_ids:
        try:
            due_count = await db.count_due_cards(user_id)
            display_count = due_count + (20 if due_count <= 150 else 0)
            await bot.send_message(
                chat_id=user_id,
                text=f"Guten Morgen! You have {display_count} cards due today.",
                reply_markup=keyboard,
            )
        except Exception as e:
            # Don't let one user's failure stop others
            import logging
            logging.getLogger(__name__).warning(
                "morning_trigger failed for user %s: %s", user_id, e
            )
```

- [ ] **Step 2: Rewrite `nag_check`**

Replace the entire `nag_check` function:

```python
async def nag_check(bot) -> None:
    """Remind every user who hasn't finished their session today."""
    all_users = await db.get_all_users()
    user_ids = {u["user_id"] for u in all_users}
    user_ids.add(config.AUTHORIZED_CHAT_ID)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Start Session", callback_data="start_session")]]
    )

    for user_id in user_ids:
        try:
            session = qm.get_session(user_id)
            if session.kill_switch:
                continue

            if session.active:
                remaining = session.remaining_count()
            else:
                due_count = await db.count_due_cards(user_id)
                remaining = due_count + (20 if due_count <= 150 else 0)

            if remaining == 0:
                continue

            await bot.send_message(
                chat_id=user_id,
                text=f"Friendly reminder: {remaining} cards remaining today.",
                reply_markup=keyboard,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "nag_check failed for user %s: %s", user_id, e
            )
```

- [ ] **Step 3: Commit**

```bash
git add bot/scheduler.py
git commit -m "feat(scheduler): iterate all registered users in morning_trigger and nag_check"
```

---

## Task 7: Register admin in users collection on first `/start`

**Files:**
- Modify: `bot/handlers.py`

The admin is hardcoded — they never go through `/login`. But `get_user_settings` falls back to defaults if no doc exists, so settings work. However, the admin's user_id won't be in the users collection, so `get_all_users()` won't include them. We already handle this in Task 6 by always adding `config.AUTHORIZED_CHAT_ID` to the loop.

This task is for a quality-of-life improvement: auto-create the admin's user doc on `/start` so the admin's preferences persist.

- [ ] **Step 1: Auto-register admin in `cmd_start`**

Replace `cmd_start`:

```python
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    user = update.effective_user
    # Auto-register (idempotent — no-op if already exists)
    await db.register_user(user.id, user.username)
    await update.message.reply_text(
        "Willkommen! Use /session to start studying or /stats to see your progress."
    )
```

- [ ] **Step 2: Commit**

```bash
git add bot/handlers.py
git commit -m "feat: auto-register user on /start so settings persist"
```

---

## Spec Coverage Self-Check

| plan.md section | Covered by task |
|---|---|
| `cards` collection (read-only vocab) | Already done in previous session |
| `users` collection | Task 1 (register_user, get_all_users) |
| `user_progress` collection | Already done in previous session |
| `otps` collection | Task 1 (create_otp, consume_otp) |
| `/create_invite` admin command | Task 2 |
| `/login <password>` user onboarding | Task 3 |
| Auth check against `users` (not just hardcoded ID) | Task 4 |
| Per-user queue (keyed by user_id) | Already done in previous session |
| Per-user kill_switch | Already done in previous session |
| `/settings` command with inline keyboard | Task 5 |
| Study direction (DE→EN / EN→DE) | Task 5 |
| CEFR level selection | Task 5 |
| Scheduler iterates all registered users | Task 6 |
| Morning reset per-user | Task 6 |
| Nag check per-user kill switch | Task 6 |
