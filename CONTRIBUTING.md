# Contributing

Thanks for your interest in improving the German Flashcards bot! This is a
Python 3.11+ / asyncio Telegram bot (`python-telegram-bot`) backed by Google
Cloud Firestore. The sections below cover everything you need to get going.

## Development setup

```bash
git clone https://github.com/MiladBahariQaragoz/germanflashcards.git
cd germanflashcards
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

To run the bot locally you need:

- `BOT_TOKEN` — a Telegram bot token from [@BotFather](https://t.me/BotFather)
- `AUTHORIZED_CHAT_ID` — your Telegram user ID (the admin)
- Firestore access via Application Default Credentials (`gcloud auth application-default login`)

Put the two env vars in a `.env` file (git-ignored), then:

```bash
python -m bot.main
```

## Running tests

```bash
pytest
```

The pure-logic modules (`bot/queue_manager.py`, `bot/streak.py`, `bot/catchup.py`,
`bot/fsrs_service.py`) are fully unit-tested without Firestore. **Please add or
update tests when you touch them.** There is no Firestore emulator wired up, so
`db.py` and the handlers aren't covered directly — keep new business logic in the
pure modules where practical so it stays testable.

## Seed data

The vocabulary/grammar JSON is **not** in this repo (it lives in a private
maintainers-only repo). If you're running your own instance, supply your own card
JSON in the same shape and load it with the `scripts/` uploaders — see the
"Seed Data" section in the [README](README.md). The running bot never reads these
files; it serves everything from Firestore.

## Conventions

A few house rules (see [`CLAUDE.md`](CLAUDE.md) for the full architecture notes):

- **All Firestore access goes through `bot/db.py`.** Don't import `firestore`
  elsewhere or put raw queries in handlers.
- **Vocab and grammar are parallel domains** (`cards`/`user_progress` vs
  `grammar_cards`/`grammar_progress`). A change to one usually needs the mirror
  change to the other — keep them symmetrical.
- Anything touching the network is `async`; use `asyncio.gather` for parallel reads.
- Use `FieldFilter(...)` in `.where()` calls.
- **Adding a command is a 3-step change:** handler in `handlers.py` → registration
  in `main.py` → menu entry in `main.py`'s `set_commands()`.
- Match the existing user-facing tone (German flavor + English, emoji, Markdown).

## Submitting changes

1. Fork the repo and create a feature branch off `master`.
2. Make your change with tests; ensure `pytest` is green.
3. Open a pull request describing what changed and why.

Keep PRs focused — small, single-purpose changes are easier to review and merge.

## Reporting bugs / ideas

Open a GitHub issue with steps to reproduce (for bugs) or a short description of
the proposed feature. Thanks for contributing! 🙌
