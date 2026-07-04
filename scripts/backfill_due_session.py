"""
One-time migration: give every existing progress doc a `due_session`.

Scheduling moved from a calendar axis (`due_date`) to a session axis
(`due_session` compared against each user's `session_counter`). Progress docs
written before this change have only `due_date`, so a `due_session <=` query would
never match them and reviewed cards would silently vanish. This backfills both
`user_progress` and `grammar_progress`:

  - Read each user's current `session_counter` (0 if unset).
  - Already-due cards (`due_date <= now`) → `due_session = counter` (due immediately).
  - Future cards → `due_session = counter + interval_to_sessions(due_date, now)`, so
    the relative order is preserved as whole sessions.

Idempotent — docs that already have `due_session` are left untouched, so it's safe
to re-run.

Run from the project root (needs ADC / GOOGLE_CLOUD_PROJECT, like the other scripts):
  python -m scripts.backfill_due_session
"""
import asyncio
from datetime import datetime, timezone

from google.cloud import firestore

from bot.scheduling import interval_to_sessions

BATCH_SIZE = 400  # Firestore max is 500; stay under for safety
PROGRESS_COLLECTIONS = ["user_progress", "grammar_progress"]


async def _session_counters(db) -> dict[int, int]:
    """user_id → session_counter for every registered user (default 0)."""
    counters: dict[int, int] = {}
    async for doc in db.collection("users").stream():
        data = doc.to_dict() or {}
        uid = data.get("user_id")
        if uid is not None:
            counters[uid] = data.get("session_counter", 0)
    return counters


async def backfill() -> None:
    db = firestore.AsyncClient()
    counters = await _session_counters(db)
    now = datetime.now(timezone.utc)

    grand_total = 0
    for col_name in PROGRESS_COLLECTIONS:
        col = db.collection(col_name)
        batch = db.batch()
        pending = 0
        updated = 0
        skipped = 0

        async for doc in col.stream():
            data = doc.to_dict() or {}
            if "due_session" in data:
                skipped += 1
                continue

            counter = counters.get(data.get("user_id"), 0)
            due = data.get("due_date")
            if due is not None and due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)

            if due is None or due <= now:
                due_session = counter
            else:
                due_session = counter + interval_to_sessions(due, now)

            batch.update(doc.reference, {"due_session": due_session})
            pending += 1
            updated += 1
            if pending == BATCH_SIZE:
                await batch.commit()
                batch = db.batch()
                pending = 0

        if pending:
            await batch.commit()

        grand_total += updated
        print(f"{col_name}: {updated} updated, {skipped} already had due_session")

    print(f"Done. {grand_total} progress docs backfilled.")


if __name__ == "__main__":
    asyncio.run(backfill())
