import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
AUTHORIZED_CHAT_ID: int = int(os.environ["AUTHORIZED_CHAT_ID"])
MONGODB_URI: str = os.environ["MONGODB_URI"]
DB_NAME: str = os.environ["DB_NAME"]
