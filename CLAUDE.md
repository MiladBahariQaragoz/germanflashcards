# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## Work log — 2026-06-17 (5 enhancements, all shipped ✅)

All five items below are implemented and covered by `pytest`. Catch-up is a
one-time button offered at >100 due.
Kept here as a changelog; details are folded into the sections below.

> **Update 2026-06-23:** (a) streak now advances on clearing **either** grammar
> **or** vocab on a Berlin day (was: required both); (b) catch-up keeps 60 due
> today but caps later days at 40/day (`CATCHUP_NEXT_DAY`) so the ~20 daily new
> cards keep the total ~60. Sections below reflect these.

- [x] **#3 Reminder timing** — `scheduler.py` nags fire at fixed Berlin clock
  times (10–22, every 2h) via `CronTrigger` (was a drifting `IntervalTrigger`);
  added `coalesce`/`max_instances=1`/`misfire_grace_time` job defaults.
- [x] **#2 Progress bar** — `SessionQueue.total`/`reviewed` +
  `mark_reviewed()`/`progress()`; `_progress_bar()` prepends `████░░░░░░ 4/10 (40%)`
  to each card front. Advances on any non-Again grade.
- [x] **#5 Streak counter** — pure logic in `bot/streak.py`; persisted by
  `db.record_session_cleared`/`get_streak`. Shown in `/stats`.
- [x] **#1 Catch-up installments** — pure math in `bot/catchup.py`;
  `db.spread_backlog` reschedules overflow `due_date`s. `🧩 spread` button shown
  by `scheduler._session_keyboard` + `handlers.callback_spread_backlog`.
- [x] **#4** — answered, no code: no 8:00 gate; early sessions run normally;
  streak anchors to Berlin calendar date (see latent UTC quirk under "gotchas").

## Pure, unit-tested logic modules

These hold business logic deliberately split out of `db.py`/`handlers.py` (which
need Firestore / Telegram) so it can be tested offline. Follow this pattern for
new logic — keep it pure, persist separately.

- `bot/queue_manager.py` — session queues + progress tracking.
- `bot/streak.py` — `streak_transition()` / `current_streak()` / `rank_streaks()`.
  A day counts when **either** domain's session is cleared on a Berlin date;
  streak advances once/day, resets to 1 after a gap. (`both_done` — other domain
  cleared today OR 0 due — is still computed but only drives messaging.) `db` stores
  `streak_count`, `last_streak_date`, `{vocab,grammar}_cleared_date` on the user
  doc; empty sessions auto-satisfy a domain via `handlers._on_domain_empty`.
  `rank_streaks()` backs the public `/leaderboard` (`db.get_leaderboard` →
  `handlers.cmd_leaderboard`): top active streaks, lapsed excluded, ties by
  username. Rendered as **plain text** (no Markdown) since Telegram usernames
  often contain `_`.
- `bot/catchup.py` — `plan_installments(due_count, today_max, next_day_max=None)`
  → day-offset per overflow card (keep `today_max` today, then chunks of
  `next_day_max`). `db.spread_backlog` applies it (only `due_date` moves; batched
  writes). Thresholds: `db.BACKLOG_OFFER_THRESHOLD` (100), `db.CATCHUP_PER_DAY` (60
  today), `db.CATCHUP_NEXT_DAY` (40/day after).

## What this is

A **multi-user Telegram bot** for learning German via spaced repetition (FSRS).
Backend is **Google Cloud Firestore**. Runtime is **Python 3.11+ / asyncio**,
built on `python-telegram-bot`. Deployed on a GCE VM as a systemd service.

There are two **independent** study domains that mirror each other exactly:

| Domain  | Card collection  | Progress collection  | Session registry (in-memory) |
|---------|------------------|----------------------|------------------------------|
| Vocab   | `cards`          | `user_progress`      | `queue_manager._sessions`    |
| Grammar | `grammar_cards`  | `grammar_progress`   | `queue_manager._grammar_sessions` |

When you add a feature to one domain, the parallel domain almost always needs
the same change. Keep them symmetrical.

## Module map (`bot/`)

- `main.py` — entry point. Builds the app, registers every command/callback
  handler, sets the single Telegram command menu, starts the scheduler, then
  `run_polling`. **New handlers must be registered here.**
- `handlers.py` — all command + callback logic, auth gate (`_is_authorized`),
  message rendering. Vocab and grammar handlers live side by side.
- `db.py` — the **only** module that touches Firestore. All functions are async.
- `fsrs_service.py` — pure wrapper around the `fsrs` library. Converts between
  card dicts and FSRS `Card` objects; no I/O.
- `queue_manager.py` — in-memory per-user session queues. No I/O, no async.
  This is the most unit-testable module.
- `scheduler.py` — APScheduler jobs: `morning_trigger` (08:00 Europe/Berlin,
  resets queues + sends due counts) and `nag_check` (every 2h, reminds unfinished).
- `config.py` — reads env vars (`BOT_TOKEN`, `AUTHORIZED_CHAT_ID`) via `.env`.
- `streak.py`, `catchup.py` — pure logic modules (see "Pure, unit-tested logic").

## Key design decisions (don't break these)

- **Progress docs exist only for *reviewed* cards.** New cards are discovered
  on-demand by querying the `cards`/`grammar_cards` collections and excluding any
  that already have a progress doc. This means **registering a user is zero
  writes** — onboarding stays free regardless of deck size. The `_progress_exists`
  flag on a card dict tracks whether a write is an `update` (existing) or a full
  `set` (first review). Don't pre-provision per-user card docs.
- **Progress docs are denormalized** — they copy `word`, `translation`,
  sentences, and `cefr_level` from the card so a session never needs a join.
- **CEFR filtering for grammar** uses the `_Grammar` suffix (`A1` → `A1_Grammar`).
  Always map user levels through `db._grammar_cefr_levels()` before querying
  `grammar_cards`/`grammar_progress`.
- **Some filtering is done in Python, not Firestore** (e.g. `fsrs_state != "New"`
  and CEFR membership in `get_due_cards`) specifically to avoid extra composite
  indexes. If you add a query, prefer this pattern over a new composite index
  unless the result set is large.
- **Session queues are in-memory only.** They are lost on restart and reset every
  morning. Never assume queue state survives a deploy.
- Document ID convention: progress docs are `f"{user_id}_{card_id}"`;
  grammar card IDs are `md5(word|german_sentence)` (see `scripts/upload_grammar.py`).

## Conventions

- **All Firestore access goes through `db.py`.** Don't import `firestore`
  elsewhere. Keep handlers free of raw queries.
- Everything that touches the network is `async`; use `asyncio.gather` for
  parallel reads (see `cmd_stats`, `morning_trigger`).
- Use `FieldFilter(...)` in `.where()` calls (positional `.where()` is deprecated).
- Adding a command is a 3-step change: handler in `handlers.py` → registration in
  `main.py` → menu entry in `main.py`'s `set_commands()`.
- User-facing strings mix German flavor + English and use emoji + Markdown.
  Match the existing tone.
- **Onboarding is invite-only, admin-approved** (no OTP/passwords). New user taps
  `request_access` on `/start` → `db.create_access_request` → admin gets a message
  with Approve/Deny buttons (`approve_user:<id>` / `deny_user:<id>`) →
  `db.approve_access_request` registers them. State lives in the `access_requests`
  collection (`pending`/`approved`/`denied`); the `users` doc is created only on
  approval, so `is_registered_user` / `_is_authorized` stay accurate.

## Commands

```bash
# Install
pip install -r requirements.txt -r requirements-dev.txt

# Run the bot locally (needs .env with BOT_TOKEN + AUTHORIZED_CHAT_ID, and ADC for Firestore)
python -m bot.main

# Tests (queue_manager + fsrs_service are pure and fully testable offline)
pytest
pytest tests/test_queue_manager.py -q

# One-time data loads (require ADC / GOOGLE_CLOUD_PROJECT)
python scripts/upload_grammar.py     # → grammar_cards
python -m scripts.migrate_v2         # → cards + user_progress (also needs MONGODB_URI)
```

## Testing

- `pytest` + `pytest-asyncio`. Pure modules (`queue_manager`, `fsrs_service`) are
  tested without mocks. There is **no Firestore emulator wired up** — `db.py` and
  handlers are not currently covered. When changing queue/scheduling logic, add a
  test; when changing `db.py`, test the pure helpers (e.g. `_grammar_cefr_levels`,
  doc-id builders) at minimum.
- Keep new business logic out of `db.py`/`handlers.py` where practical so it stays
  unit-testable (the queue manager is the model to follow).

## Deployment

- Hosted on GCE VM `german-bot` (project `learn-german-bot`, zone
  `us-central1-a`), running as systemd service **`germanbot`** (no hyphen).
  Working dir + venv: `/home/Student/germanflashcards` (capital S), `User=Student`,
  `Restart=always`. Env from `/home/Student/germanflashcards/.env`.
- `cloudbuild.yaml` deploys by SSHing to the VM, `git pull`, then
  `systemctl restart germanbot`. Pushing to `master` is the release.
- Manual deploy from a terminal (what to run if Cloud Build isn't wired up):
  `gcloud compute ssh --zone=us-central1-a german-bot --project=learn-german-bot --command="cd /home/Student/germanflashcards && git pull && sudo systemctl restart germanbot"`
- `bot/main.py` raises the `httpx`/`httpcore` loggers to WARNING so the bot token
  (embedded in Telegram API URLs) never lands in journald.
- Auth to Firestore is via **Application Default Credentials** — no key files,
  no `MONGODB_URI` for the running bot.
- **Firestore composite indexes are manual.** Session queries need
  `(user_id ASC, due_date ASC)` on both `user_progress` and `grammar_progress`.
  Adding a new multi-field query may require creating a new index in the console.

## Repo notes / gotchas

- ⚠️ **`SETUP.md` is stale** — it describes the old MongoDB + Render stack. The
  current stack is Firestore + GCE (see `README.md` and `system.md`). Don't follow
  `SETUP.md` for new work; update or delete it if you touch setup docs.
- `motor` in `requirements.txt` exists **only** for the one-time `migrate_v2`
  MongoDB read and is safe to remove once migration is confirmed done.
- The `combined_words_*.json` / `*_grammar_flashcards_*.json` seed files are **not
  in this repo** — they live in a separate **private** repo `germanflashcards-data`
  and are git-ignored here. They feed the one-time upload scripts, never the
  runtime. Maintainers `git clone` that repo and copy the JSON in. The public
  history was scrubbed of these files (force-pushed), so they're gone from `master`.
- Architecture/design history lives in `docs/superpowers/`.
- ⚠️ **Latent due-date timezone quirk:** "due today" in `db.get_due_cards` is
  computed off `_end_of_today_utc()` (UTC calendar date), while the daily reset and
  streaks use **Berlin** dates. Around midnight Berlin these disagree by a day. Not
  fixed (out of scope for the 2026-06-17 work); be aware when touching due-date or
  streak logic. The streak "study day" is intentionally Berlin-based.
