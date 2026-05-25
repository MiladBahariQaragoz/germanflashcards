import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
AUTHORIZED_CHAT_ID: int = int(os.environ["AUTHORIZED_CHAT_ID"])
# MONGODB_URI is only used by scripts/migrate_v2.py during the one-time migration.
# The bot itself uses Firestore via Application Default Credentials — no URI needed.
