# German Flashcards Telegram Bot

The German Flashcards system is a multi-user Telegram bot designed for spaced repetition language learning. It utilizes the Free Spaced Repetition Scheduler (FSRS) algorithm to optimize vocabulary retention and relies on Google Cloud Firestore for scalable data storage.

## Features

* **Spaced Repetition System:** Automatically schedules reviews of German vocabulary based on how well you remember them, using the robust FSRS algorithm.
* **Multi-User Support:** Allows multiple users to study simultaneously, maintaining isolated progress and personalized settings.
* **CEFR Level Filtering:** Users can select specific difficulty levels (A1, A2, B1, B2) for the new vocabulary they want to learn.
* **Study Direction:** Supports flexible study directions, allowing users to practice translating from German to English or English to German.
* **Automated Reminders:** Background schedulers send out morning notifications of due cards and periodic reminders to complete daily study sessions.
* **Secure Registration:** Onboarding is handled via a one-time password (OTP) system managed by the bot administrator.

## Setup and Installation

### Prerequisites
* Python 3.11 or higher
* A Telegram Bot Token (obtained from BotFather)
* Google Cloud project with Firestore enabled in native mode

### Local Development

1. Clone the repository and navigate to the project directory.
2. Create and activate a virtual environment.
3. Install the dependencies:
   `pip install -r requirements.txt`
4. Set up the environment variables. You will need:
   `BOT_TOKEN`: Your Telegram bot token.
   `AUTHORIZED_CHAT_ID`: The Telegram chat ID of the administrator.
5. If running outside of Google Cloud, ensure you have set up Application Default Credentials for Firestore access.

### Seed Data

The vocabulary/grammar JSON used to populate Firestore is kept on the separate
[`data`](../../tree/data) branch (not on `master`), so the main repo stays
code-only. The running bot doesn't need it — it serves cards from Firestore.

To seed a fresh database, fetch the JSON and run the upload scripts:

```bash
git checkout data -- '*.json'        # pull the seed JSON into your working tree
python scripts/upload_grammar.py     # grammar cards
python -m scripts.migrate_v2         # vocabulary cards (also needs MONGODB_URI)
```

## Usage

* `/start`: Intro to the bot. New users get a **Request access** button (invite-only).
* `/grammar`, `/vocab`: Begin a grammar or vocabulary study session.
* `/stats`: Displays your current study progress, card counts, and streak.
* `/leaderboard`: Top streak holders across all users.
* `/settings`: Opens a menu to configure your study direction and preferred CEFR levels.
* `/developer`: About the developer.

### Onboarding

Access is invite-only and approved by the admin. A new user opens the bot, taps
**Request access**, and the admin receives a message with their name and ID plus
**Approve** / **Deny** buttons. On approval the user is registered and notified.

## Documentation

For a deeper understanding of the internal architecture, database schema, and component interactions, please refer to the `system.md` file located in this repository.