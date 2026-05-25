"""
V2 Migration Script — MongoDB Atlas → Firestore
------------------------------------------------
Run this ONCE on the GCE VM (or locally with ADC configured).

What it does:
  Step 1 — Populate the Firestore `cards` collection from the four JSON files.
  Step 2 — Read existing FSRS progress from MongoDB `flashcards` collection
            and create `user_progress` documents in Firestore for the admin user.
            Cards with no review history (still "New") get a blank progress doc.
  Step 3 — Create composite indexes (where possible via SDK; others are noted).

Prerequisites:
  - GOOGLE_CLOUD_PROJECT env var set, or running on GCE (ADC auto-detected).
  - MONGODB_URI env var set (to read the old data).
  - AUTHORIZED_CHAT_ID env var set (identifies the admin user in user_progress).

After this script completes successfully, `motor` can be removed from requirements.txt.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import motor.motor_asyncio
from dotenv import load_dotenv
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

load_dotenv()

MONGODB_URI = os.environ["MONGODB_URI"]
ADMIN_CHAT_ID = int(os.environ["AUTHORIZED_CHAT_ID"])

ROOT = Path(__file__).parent.parent
JSON_FILES = [
    ROOT / "combined_words_a1.json",
    ROOT / "combined_words_a2.json",
    ROOT / "combined_words_b1.json",
    ROOT / "combined_words_b2.json",
]

OLD_DB_NAME = os.environ.get("DB_NAME", "german_flashcards")


# ---------------------------------------------------------------------------
# Step 1 — Populate `cards` collection from JSON files
# ---------------------------------------------------------------------------

async def step1_populate_cards(fs_db) -> dict[str, str]:
    """
    Inserts vocabulary from all JSON files into Firestore `cards` collection.
    Upserts by `word` field (safe to re-run).
    Returns a dict mapping word -> Firestore document ID for use in Step 2.
    """
    print("\n--- Step 1: Populating Firestore `cards` collection ---")
    cards_col = fs_db.collection("cards")

    word_to_id: dict[str, str] = {}
    total_new = 0
    total_existing = 0

    for json_file in JSON_FILES:
        if not json_file.exists():
            print(f"  WARNING: {json_file.name} not found — skipping.")
            continue

        with open(json_file, encoding="utf-8") as f:
            words = json.load(f)

        print(f"  Loading {json_file.name} ({len(words)} words) ...")

        for item in words:
            word = item["word"]

            # Check if card already exists
            existing = await cards_col.where(
                filter=FieldFilter("word", "==", word)
            ).limit(1).get()

            if existing:
                doc_id = existing[0].id
                word_to_id[word] = doc_id
                total_existing += 1
            else:
                doc = {
                    "word": word,
                    "translation": item["translation"],
                    "german_sentence": item.get("german_sentence", ""),
                    "english_translation": item.get("english_translation", ""),
                    "cefr_level": item.get("cefr_level", "Unknown"),
                }
                _, ref = await cards_col.add(doc)
                word_to_id[word] = ref.id
                total_new += 1

    print(f"  Done. Inserted: {total_new}, Already existed: {total_existing}")
    print(f"  Total unique words mapped: {len(word_to_id)}")
    return word_to_id


# ---------------------------------------------------------------------------
# Step 2 — Migrate V1 MongoDB progress → Firestore user_progress
# ---------------------------------------------------------------------------

def _progress_doc_id(user_id: int, card_id: str) -> str:
    return f"{user_id}_{card_id}"


async def step2_migrate_progress(fs_db, word_to_id: dict[str, str]) -> None:
    """
    Reads the old MongoDB `flashcards` collection and creates user_progress
    documents in Firestore for the admin user.
    - Cards with prior review history: migrated with existing FSRS state.
    - Cards still "New" (no review): created with blank FSRS state.
    - Cards not found in the new `cards` collection: skipped with a warning.
    Safe to re-run (skips already-existing progress docs).
    """
    print("\n--- Step 2: Migrating MongoDB progress → Firestore user_progress ---")

    mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
    old_col = mongo_client[OLD_DB_NAME]["flashcards"]
    progress_col = fs_db.collection("user_progress")

    old_count = await old_col.count_documents({})
    print(f"  Found {old_count} documents in MongoDB `flashcards`.")

    # Also create progress docs for cards in JSON files not present in MongoDB
    # (in case the old DB was only partially populated).
    all_words_in_json = set(word_to_id.keys())
    words_seen_in_mongo: set[str] = set()

    migrated_reviewed = 0
    migrated_new = 0
    skipped_no_match = 0
    skipped_exists = 0

    async for old_card in old_col.find({}):
        word = old_card.get("word", "")
        words_seen_in_mongo.add(word)
        card_id = word_to_id.get(word)

        if card_id is None:
            print(f"  WARNING: No Firestore card found for word '{word}' — skipping.")
            skipped_no_match += 1
            continue

        doc_id = _progress_doc_id(ADMIN_CHAT_ID, card_id)
        existing = await progress_col.document(doc_id).get()
        if existing.exists:
            skipped_exists += 1
            continue

        # Fetch the vocab fields from Firestore cards collection (for denormalization)
        card_doc = await fs_db.collection("cards").document(card_id).get()
        card_data = card_doc.to_dict()

        # Determine FSRS state from old document
        due_date = old_card.get("due_date")
        if due_date is not None and due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)

        last_review = old_card.get("last_review")
        if last_review is not None and last_review.tzinfo is None:
            last_review = last_review.replace(tzinfo=timezone.utc)

        progress_doc = {
            "user_id": ADMIN_CHAT_ID,
            "card_id": card_id,
            # Denormalized vocab
            "word": card_data["word"],
            "translation": card_data["translation"],
            "german_sentence": card_data.get("german_sentence", ""),
            "english_translation": card_data.get("english_translation", ""),
            "cefr_level": card_data.get("cefr_level", "Unknown"),
            # FSRS fields
            "fsrs_state": old_card.get("fsrs_state", "New"),
            "due_date": due_date or datetime.now(timezone.utc),
            "state": old_card.get("state", 1),
            "step": old_card.get("step", 0),
            "stability": old_card.get("stability"),
            "difficulty": old_card.get("difficulty"),
            "last_review": last_review,
        }

        await progress_col.document(doc_id).set(progress_doc)

        if old_card.get("fsrs_state", "New") == "New":
            migrated_new += 1
        else:
            migrated_reviewed += 1

    # Create "New" progress docs for any JSON cards that weren't in MongoDB at all
    words_only_in_json = all_words_in_json - words_seen_in_mongo
    print(f"  Creating blank New-state docs for {len(words_only_in_json)} cards not in MongoDB ...")
    for word in words_only_in_json:
        card_id = word_to_id[word]
        doc_id = _progress_doc_id(ADMIN_CHAT_ID, card_id)
        existing = await progress_col.document(doc_id).get()
        if existing.exists:
            continue

        card_doc = await fs_db.collection("cards").document(card_id).get()
        card_data = card_doc.to_dict()

        progress_doc = {
            "user_id": ADMIN_CHAT_ID,
            "card_id": card_id,
            "word": card_data["word"],
            "translation": card_data["translation"],
            "german_sentence": card_data.get("german_sentence", ""),
            "english_translation": card_data.get("english_translation", ""),
            "cefr_level": card_data.get("cefr_level", "Unknown"),
            "fsrs_state": "New",
            "due_date": datetime.now(timezone.utc),
            "state": 1,
            "step": 0,
            "stability": None,
            "difficulty": None,
            "last_review": None,
        }
        await progress_col.document(doc_id).set(progress_doc)
        migrated_new += 1

    mongo_client.close()
    print(f"  Done. Reviewed cards migrated: {migrated_reviewed}, "
          f"New cards created: {migrated_new}, "
          f"Skipped (already existed): {skipped_exists}, "
          f"Skipped (no card match): {skipped_no_match}")


# ---------------------------------------------------------------------------
# Step 3 — Index notes
# ---------------------------------------------------------------------------

async def step3_index_notes() -> None:
    print("\n--- Step 3: Firestore index notes ---")
    print(
        "  Firestore creates single-field indexes automatically.\n"
        "  The following COMPOSITE index must be created manually in the\n"
        "  Firestore console (or via gcloud) for session queries to work:\n\n"
        "  Collection : user_progress\n"
        "  Fields     : user_id ASC, due_date ASC\n\n"
        "  Console URL:\n"
        "  https://console.cloud.google.com/firestore/indexes\n\n"
        "  gcloud command:\n"
        "  gcloud firestore indexes composite create \\\n"
        "    --collection-group=user_progress \\\n"
        "    --field-config field-path=user_id,order=ascending \\\n"
        "    --field-config field-path=due_date,order=ascending\n"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    print("=== V2 Migration: MongoDB Atlas → Firestore ===")
    print(f"Admin chat ID : {ADMIN_CHAT_ID}")
    print(f"MongoDB DB    : {OLD_DB_NAME}")

    fs_db = firestore.AsyncClient()

    try:
        word_to_id = await step1_populate_cards(fs_db)
        await step2_migrate_progress(fs_db, word_to_id)
        await step3_index_notes()
    finally:
        await fs_db.close()

    print("\n=== Migration complete ===")
    print("Next: create the composite index shown above, then deploy the bot.")
    print("Once confirmed working, remove 'motor' from requirements.txt.")


if __name__ == "__main__":
    asyncio.run(main())
