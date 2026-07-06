import logging
import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone as pytz_timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot import config
from bot import db
from bot import queue_manager as qm

BERLIN = pytz_timezone("Europe/Berlin")
logger = logging.getLogger(__name__)


def _leaderboard_block(entries: list[dict]) -> str:
    """
    Compact plain-text streak leaderboard for the morning nudge (no Markdown —
    Telegram usernames often contain '_'). Mirrors handlers.cmd_leaderboard.
    """
    if not entries:
        return "🏆 Streak Leaders\nNo active streaks yet — be the first today!"
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    lines = ["🏆 Streak Leaders"]
    for i, e in enumerate(entries):
        name = f"@{e['username']}" if e["username"] else f"User {e['user_id']}"
        rank = medals.get(i, f"{i + 1}.")
        days = "day" if e["streak"] == 1 else "days"
        lines.append(f"{rank} {name} — 🔥 {e['streak']} {days}")
    return "\n".join(lines)


def _session_keyboard() -> InlineKeyboardMarkup:
    """Grammar / Vocab start buttons. Backlog is auto-capped per session now, so
    there is no manual 'spread' offer anymore."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Start Grammar", callback_data="start_grammar_session"),
            InlineKeyboardButton("🗂️ Start Vocab", callback_data="start_session"),
        ]
    ])


async def morning_trigger(bot) -> None:
    """Reset all queues and send each user their daily grammar + vocab card counts."""
    qm.reset_all_sessions()  # one unified session per user

    all_users = await db.get_all_users()
    user_ids = {u["user_id"] for u in all_users}
    user_ids.add(config.AUTHORIZED_CHAT_ID)

    # Global leaderboard — fetched once and appended to everyone's morning nudge.
    leaderboard_text = _leaderboard_block(await db.get_leaderboard(limit=5))

    for user_id in user_ids:
        try:
            settings = await db.get_user_settings(user_id)
            cefr = settings["cefr_levels"]
            counter = settings["session_counter"]

            # Preview what the next session serves (counter+1, capped) so the counts
            # match what tapping Start actually shows. The review pool is unified, so
            # both buttons serve the same capped due pool; they differ only in which
            # new cards get added. Each line therefore shows: shared due + 20 new.
            preview = await db.preview_next_session(user_id, counter, cefr_levels=cefr)
            new_each = 20 if preview["allow_new"] else 0
            display = preview["total"] + new_each
            new_note = f" (incl. {new_each} new)" if new_each else ""

            text = (
                f"Guten Morgen! 🌅\n\n"
                f"📅 Next session: {display} card{'s' if display != 1 else ''}{new_note}\n"
                f"🔁 Grammar and vocab share one review queue — tap either to begin.\n\n"
                f"{leaderboard_text}"
            )
            await bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=_session_keyboard(),
            )
        except Exception as e:
            logger.warning("morning_trigger failed for user %s: %s", user_id, e)


async def nag_check(bot) -> None:
    """Remind users who still have cards waiting in their session today."""
    all_users = await db.get_all_users()
    user_ids = {u["user_id"] for u in all_users}
    user_ids.add(config.AUTHORIZED_CHAT_ID)

    for user_id in user_ids:
        try:
            # One unified session per user (vocab + grammar share the same queue).
            session = qm.get_session(user_id)

            # Already cleared today — skip entirely.
            if session.kill_switch:
                continue

            settings = await db.get_user_settings(user_id)
            cefr = settings["cefr_levels"]
            counter = settings["session_counter"]

            # Preview what the next session serves (counter+1, capped) so the nag
            # count matches what tapping Start actually shows.
            preview = await db.preview_next_session(user_id, counter, cefr_levels=cefr)
            new_each = 20 if preview["allow_new"] else 0

            # Live remaining once a session is running; otherwise the capped review
            # load plus a new-card allowance for the first session they start.
            if session.active:
                remaining = session.remaining_count()
            else:
                remaining = preview["total"] + new_each

            if remaining == 0:
                continue

            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"⏰ You still have {remaining} card{'s' if remaining != 1 else ''} "
                    f"waiting.\n\n"
                    f"A few minutes now keeps your 🔥 streak alive and your memory "
                    f"fresh. Tap below to pick up where you left off! 💪"
                ),
                reply_markup=_session_keyboard(),
            )
        except Exception as e:
            logger.warning("nag_check failed for user %s: %s", user_id, e)


def setup_scheduler(bot) -> AsyncIOScheduler:
    # job_defaults make firing predictable and idempotent within a single process:
    #   coalesce          — if several runs were missed (e.g. VM asleep), run once, not N times
    #   max_instances=1   — never overlap a job with itself
    #   misfire_grace_time — still run a job that fired up to 5 min late (deploy/restart window)
    scheduler = AsyncIOScheduler(
        timezone=BERLIN,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )
    # Daily reset + due counts at 08:00 Berlin.
    scheduler.add_job(
        morning_trigger,
        CronTrigger(hour=8, minute=0, timezone=BERLIN),
        args=[bot],
        id="morning_trigger",
        replace_existing=True,
    )
    # Reminders at FIXED Berlin clock times (every 2h, 10:00–22:00) rather than a
    # drifting interval anchored to process start. Predictable, and de-duplicated
    # within one process. NOTE: if you ever see reminders minutes apart, that means
    # more than one bot process is running — check `systemctl status german-bot`.
    scheduler.add_job(
        nag_check,
        CronTrigger(hour="10,12,14,16,18,20,22", minute=0, timezone=BERLIN),
        args=[bot],
        id="nag_check",
        replace_existing=True,
    )
    return scheduler
