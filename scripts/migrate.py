"""One-time script: upload combined_words.json to MongoDB and create index."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import os

import motor.motor_asyncio
from dotenv import load_dotenv

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
        "state": 1,
        "step": 0,
        "stability": None,
        "difficulty": None,
        "last_review": None,
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
