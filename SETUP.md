# Setup Guide

Follow these steps in order. Do not skip ahead.

---

## Step 1 — Create a Telegram Bot

1. Open Telegram on your phone or desktop
2. Search for **@BotFather** and open the chat
3. Send: `/newbot`
4. When asked for a name, type anything (e.g. `LearnGerman`)
5. When asked for a username, type anything ending in `bot` (e.g. `LearnGermanTB_bot`)
6. BotFather will reply with a token that looks like:
   ```
   123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
   ```
7. **Copy and save this token** — you will need it shortly

---

## Step 2 — Find Your Telegram Chat ID

1. Open Telegram and send any message to your new bot (e.g. `/start`)
2. Paste this URL into your browser, replacing `<TOKEN>` with your actual token:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
   Example:
   ```
   https://api.telegram.org/bot123456789:ABCdef.../getUpdates
   ```
3. The page shows JSON. Look for `"from"` → `"id"`. That number is your chat ID:
   ```json
   "from": {
     "id": 987654321,
     "first_name": "Your Name"
   }
   ```
4. **Copy and save that number**

> If the page shows `"result":[]`, send another message to your bot and refresh the URL.

---

## Step 3 — Create a MongoDB Atlas Database

1. Go to [https://www.mongodb.com/atlas](https://www.mongodb.com/atlas) and create a free account
2. After signing in, click **Create** to create a new cluster
3. Choose **M0 Free** tier → select any region → click **Create Deployment**
4. You will be asked to create a database user:
   - Username: anything (e.g. `admin`)
   - Password: generate a strong password or type one — **save it**
   - Click **Create Database User**
5. You will be asked to add your IP address:
   - Click **Add My Current IP Address** — OR —
   - Type `0.0.0.0/0` in the IP field to allow all IPs (required for Render)
   - Click **Add Entry**
6. Click **Finish and Close**, then **Go to Overview**
7. Click **Connect** → **Drivers**
8. Copy the connection string. It looks like:
   ```
   mongodb+srv://admin:<password>@cluster0.abc123.mongodb.net/
   ```
9. Replace `<password>` with the password you created in step 4
10. **Save the full connection string**

---

## Step 4 — Fill In Your .env File

1. Open the project folder `LearnGermanTB` in your file explorer
2. Find the file `.env.example` and make a copy of it named `.env`
   - On Windows: right-click → Copy, then Paste, then rename to `.env`
   - Or in the terminal:
     ```bash
     cd "C:\Users\Student\Documents\LearnGermanTB"
     copy .env.example .env
     ```
3. Open `.env` in any text editor (Notepad is fine)
4. Fill in your values:
   ```
   BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
   AUTHORIZED_CHAT_ID=987654321
   MONGODB_URI=mongodb+srv://admin:yourpassword@cluster0.abc123.mongodb.net/
   DB_NAME=learngerman
   ```
5. Save the file

> `.env` is in `.gitignore` — it will never be accidentally committed to GitHub.

---

## Step 5 — Install Python Dependencies

Open a terminal in the project folder and run:

```bash
pip install -r requirements.txt
```

Expected output ends with something like:
```
Successfully installed apscheduler-3.10.4 motor-3.5.1 ...
```

---

## Step 6 — Upload Your Words to MongoDB (Run Once)

Run the migration script:

```bash
python scripts/migrate.py
```

Expected output:
```
Loading ...combined_words.json ...
Inserting 6734 documents ...
Inserted 6734 documents.
Creating index on (due_date, fsrs_state) ...
Index created.
Migration complete.
```

> If you see `Collection already has X documents. Skipping upload.` — the migration already ran. That is fine.

To verify it worked:
1. Go to [https://cloud.mongodb.com](https://cloud.mongodb.com)
2. Click your cluster → **Browse Collections**
3. You should see the `learngerman` database with a `flashcards` collection containing 6,734 documents

---

## Step 7 — Test the Bot Locally

Start the bot:

```bash
python -m bot.main
```

You should see log output like:
```
Application started
Scheduler started
```

Now go to Telegram and test these commands on your bot:

| Command | Expected result |
|---|---|
| `/start` | `Willkommen! Use /session to start studying...` |
| `/stats` | Shows New: 6734, Learning: 0, etc. |
| `/session` | Sends a German word with a [Show Answer] button |
| Tap [Show Answer] | Message edits to show German + English + 4 grade buttons |
| Tap [Good] | Grade buttons disappear, next card appears |

Press **Ctrl+C** to stop the bot when done testing.

---

## Step 8 — Create a GitHub Repository

1. Go to [https://github.com](https://github.com) and log in (or create an account)
2. Click **New repository**
3. Name it `LearnGermanTB`
4. Set it to **Private**
5. Do NOT check "Add a README" — the repo must be empty
6. Click **Create repository**
7. GitHub will show you setup commands. Run these in your terminal:

```bash
cd "C:\Users\Student\Documents\LearnGermanTB"
git remote add origin https://github.com/YOUR_USERNAME/LearnGermanTB.git
git push -u origin master
```

Replace `YOUR_USERNAME` with your GitHub username.

---

## Step 9 — Deploy to Render

1. Go to [https://dashboard.render.com](https://dashboard.render.com) and create a free account (sign in with GitHub if you want)
2. Click **New +** → **Background Worker**
3. Connect your GitHub account if prompted, then select the `LearnGermanTB` repository
4. Fill in the settings:
   - **Name:** `learngerman-bot`
   - **Branch:** `master`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python -m bot.main`
5. Scroll down to **Environment Variables** and add these one by one:

   | Key | Value |
   |---|---|
   | `BOT_TOKEN` | your bot token from Step 1 |
   | `AUTHORIZED_CHAT_ID` | your chat ID from Step 2 |
   | `MONGODB_URI` | your Atlas connection string from Step 3 |
   | `DB_NAME` | `learngerman` |

6. Click **Create Background Worker**
7. Render will build and deploy the bot. Watch the **Logs** tab — you should see:
   ```
   Application started
   Scheduler started
   ```

---

## Step 10 — Verify 24/7 Operation

- Send `/session` to your bot on Telegram. It should respond immediately.
- Every day at **08:00 Berlin time**, the bot will send:
  > *Guten Morgen! You have X cards due today.* [Start Session]
- Every **2 hours** until your queue is empty, the bot will remind you.
- Once all cards are reviewed, it will send a congratulations message and go quiet until the next morning.

---

## Troubleshooting

**Bot does not respond:**
- Check Render → Logs for errors
- Make sure `BOT_TOKEN` is correct (no extra spaces)
- Make sure `AUTHORIZED_CHAT_ID` is your own Telegram user ID (not the bot's ID)

**Migration fails with authentication error:**
- Double-check that the password in `MONGODB_URI` does not contain special characters that need URL-encoding (e.g. `@` in the password will break the URI). Use only letters and numbers in your Atlas password.

**"Collection already has X documents" on re-run:**
- The migration ran successfully before. Nothing to do.
- If you need to start fresh: go to Atlas → Browse Collections → drop the `flashcards` collection → re-run the script.

**Render deploy fails:**
- Go to Render → Logs and read the error
- Most common cause: a missing or mistyped environment variable
