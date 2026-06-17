# System Architecture

## Overview

The German Flashcards system is a Telegram bot application designed to help users study German vocabulary using a spaced repetition system (FSRS algorithm). The bot allows multiple users to have personalized learning experiences, scheduling study sessions and reminders based on their individual progress. The system is built with Python and relies on Google Cloud Firestore for its database and the Telegram Bot API for user interactions.

## Subsystems

### 1. Telegram Bot Interface
The frontend of the system is entirely within the Telegram app. Users interact with the bot through commands (such as /start, /session, /settings) and inline buttons. 

Key files:
* `bot/main.py`: The entry point that sets up the bot, registers command and callback handlers, and starts the polling loop.
* `bot/handlers.py`: Contains the logic for processing user inputs, managing the flow of study sessions, handling user authentication, and presenting the settings menu.

### 2. Spaced Repetition Scheduler
At the core of the learning experience is the FSRS (Free Spaced Repetition Scheduler). It determines when a card should be reviewed next based on how well the user remembered it during the current session.

Key files:
* `bot/fsrs_service.py`: Wraps the FSRS algorithm, providing functions to rate cards and preview the intervals for future reviews based on the user's rating (e.g., Again, Hard, Good, Easy).

### 3. Database Management
The system uses Google Cloud Firestore to store and retrieve data asynchronously. The data model is divided into several collections to handle multiple users, vocabulary reference data, and individual progress.

Key files:
* `bot/db.py`: Encapsulates all Firestore interactions. It provides functions to query due cards, fetch new cards, update user progress, and manage one-time passwords for new user registration.

Collections:
* `cards`: Read-only vocabulary repository containing words, translations, sentences, and CEFR levels.
* `user_progress`: Tracks the FSRS state for each user-card pair, determining when the user needs to review the card next.
* `users`: Stores user profiles, chat IDs, and preferences (such as study direction and chosen CEFR levels).
* `access_requests`: Pending/approved/denied onboarding requests, keyed by user ID.

### 4. Queue Management
To facilitate study sessions, the system manages memory-based queues of cards for each active user. 

Key files:
* `bot/queue_manager.py`: Responsible for combining due cards and new cards into a shuffled session queue. It handles the logic of presenting the next card, requeuing cards that need to be reviewed again in the same session, and signaling when a session is complete.

### 5. Task Scheduling
The bot includes background processes to handle daily resets and recurring reminders.

Key files:
* `bot/scheduler.py`: Uses AsyncIOScheduler to run timed tasks. It performs a morning reset of all session queues and sends out due card counts. It also runs a regular check to nag users who have pending cards, skipping those who have already completed their daily sessions.

## Workflows

1. **Onboarding**: A new user opens the bot and taps **Request access**. The bot records a pending `access_requests` document and sends the administrator a message with the user's name and ID plus **Approve**/**Deny** inline buttons. On approval, the bot creates the user's profile (registering them) and notifies them; on denial, the request is marked denied and the user is informed.
2. **Session Execution**: When a user starts a session, the system queries the database for cards due today and new cards matching their preferred CEFR levels. These cards are loaded into a personalized queue. The user is presented with the front of each card and rates their memory after revealing the back. The FSRS algorithm calculates the next review date, and the database is updated.
3. **Reminders**: A background job runs daily at 08:00 (Europe/Berlin) to reset session states and notify users of their due cards. Throughout the day, another job periodically checks for users who have not finished their sessions and sends them reminders.

## Deployment
The application is deployed on a Google Cloud Compute Engine VM and runs as a systemd service. It uses Google Cloud Application Default Credentials to securely connect to Firestore without requiring manual credential configuration in the environment variables.