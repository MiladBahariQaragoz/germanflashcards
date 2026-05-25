import logging
import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pytz import timezone as pytz_timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot import config
from bot import db
from bot import queue_manager as qm

BERLIN = pytz_timezone("Europe/Berlin")
logger = logging.getLogger(__name__)


async def morning_trigger(bot) -> None:
    """Reset all queues and send each user their daily grammar + vocab card counts."""
    qm.reset_all_sessions()
    qm.reset_all_grammar_sessions()

    all_users = await db.get_all_users()
    user_ids = {u["user_id"] for u in all_users}
    user_ids.add(config.AUTHORIZED_CHAT_ID)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Start Grammar", callback_data="start_grammar_session"),
            InlineKeyboardButton("🗂️ Start Vocab", callback_data="start_session"),
        ]
    ])

    for user_id in user_ids:
        try:
            settings = await db.get_user_settings(user_id)
            cefr = settings["cefr_levels"]

            grammar_due, vocab_due = await asyncio.gather(
                db.count_due_grammar_cards(user_id, cefr_levels=cefr),
                db.count_due_cards(user_id, cefr_levels=cefr),
            )
            grammar_display = grammar_due + (20 if grammar_due <= 150 else 0)
            vocab_display = vocab_due + (20 if vocab_due <= 150 else 0)

            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"Guten Morgen! 🌅\n\n"
                    f"📚 Grammar: {grammar_display} card{'s' if grammar_display != 1 else ''}\n"
                    f"🗂️ Vocab: {vocab_display} card{'s' if vocab_display != 1 else ''}"
                ),
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning("morning_trigger failed for user %s: %s", user_id, e)


async def nag_check(bot) -> None:
    """Remind users who haven't finished one or both sessions today."""
    all_users = await db.get_all_users()
    user_ids = {u["user_id"] for u in all_users}
    user_ids.add(config.AUTHORIZED_CHAT_ID)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Start Grammar", callback_data="start_grammar_session"),
            InlineKeyboardButton("🗂️ Start Vocab", callback_data="start_session"),
        ]
    ])

    for user_id in user_ids:
        try:
            grammar_session = qm.get_grammar_session(user_id)
            vocab_session = qm.get_session(user_id)

            # Both done — skip entirely
            if grammar_session.kill_switch and vocab_session.kill_switch:
                continue

            settings = await db.get_user_settings(user_id)
            cefr = settings["cefr_levels"]

            # Grammar remaining
            grammar_remaining = 0
            if not grammar_session.kill_switch:
                if grammar_session.active:
                    grammar_remaining = grammar_session.remaining_count()
                else:
                    grammar_due = await db.count_due_grammar_cards(user_id, cefr_levels=cefr)
                    grammar_remaining = grammar_due + (20 if grammar_due <= 150 else 0)

            # Vocab remaining
            vocab_remaining = 0
            if not vocab_session.kill_switch:
                if vocab_session.active:
                    vocab_remaining = vocab_session.remaining_count()
                else:
                    vocab_due = await db.count_due_cards(user_id, cefr_levels=cefr)
                    vocab_remaining = vocab_due + (20 if vocab_due <= 150 else 0)

            if grammar_remaining == 0 and vocab_remaining == 0:
                continue

            parts = []
            if grammar_remaining > 0:
                parts.append(
                    f"📚 {grammar_remaining} grammar card{'s' if grammar_remaining != 1 else ''}"
                )
            if vocab_remaining > 0:
                parts.append(
                    f"🗂️ {vocab_remaining} vocab card{'s' if vocab_remaining != 1 else ''}"
                )

            remaining_text = " and ".join(parts)
            total = grammar_remaining + vocab_remaining
            tomorrow_pile = total * 2

            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"⏰ You still have {remaining_text} left for today.\n\n"
                    f"Skip today and these pile onto tomorrow — "
                    f"you could be facing ~{tomorrow_pile} instead of ~{total}. "
                    f"A few minutes now saves double the work tomorrow! 💪"
                ),
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning("nag_check failed for user %s: %s", user_id, e)


def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=BERLIN)
    scheduler.add_job(
        morning_trigger,
        CronTrigger(hour=8, minute=0, timezone=BERLIN),
        args=[bot],
        id="morning_trigger",
    )
    scheduler.add_job(
        nag_check,
        IntervalTrigger(hours=2),
        args=[bot],
        id="nag_check",
    )
    return scheduler
