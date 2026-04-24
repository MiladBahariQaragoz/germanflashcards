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
    """Non-New cards with due_date <= end of today.
    New cards are handled separately to enforce the 20/day cap."""
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
