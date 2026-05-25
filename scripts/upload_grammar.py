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


BATCH_SIZE = 400  # Firestore max is 500; stay under for safety


async def upload_grammar() -> None:
    db = firestore.AsyncClient(project="learn-german-bot")
    col = db.collection("grammar_cards")

    total = 0

    for json_file in GRAMMAR_FILES:
        if not json_file.exists():
            print(f"  WARNING: {json_file.name} not found — skipping.")
            continue

        with open(json_file, encoding="utf-8") as f:
            cards = json.load(f)

        print(f"  Processing {json_file.name} ({len(cards)} cards) ...")

        # Split into batches of BATCH_SIZE
        for i in range(0, len(cards), BATCH_SIZE):
            batch = db.batch()
            chunk = cards[i : i + BATCH_SIZE]
            for card in chunk:
                doc_id = _doc_id(card)
                ref = col.document(doc_id)
                batch.set(ref, {
                    "word": card["word"],
                    "translation": card["translation"],
                    "german_sentence": card.get("german_sentence", ""),
                    "english_translation": card.get("english_translation", ""),
                    "cefr_level": card.get("cefr_level", "Unknown"),
                })
            await batch.commit()
            total += len(chunk)
            print(f"    Committed {total} cards so far...")

    db.close()
    print(f"\nDone. Total cards upserted: {total}")
    print(
        "\nNote: This script only populates the grammar_cards collection.\n"
        "When grammar_progress is in use, create this composite index:\n"
        "  Collection : grammar_progress\n"
        "  Fields     : user_id ASC, due_date ASC\n"
        "  Console    : https://console.firebase.google.com/project/_/firestore/indexes"
    )


if __name__ == "__main__":
    asyncio.run(upload_grammar())
