import logging

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
    """Reset all queues and send each registered user their daily card count."""
    qm.reset_all_sessions()

    all_users = await db.get_all_users()
    # Always include admin — they may not have a users doc yet
    user_ids = {u["user_id"] for u in all_users}
    user_ids.add(config.AUTHORIZED_CHAT_ID)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Start Session", callback_data="start_session")]]
    )

    for user_id in user_ids:
        try:
            settings = await db.get_user_settings(user_id)
            cefr = settings["cefr_levels"]
            due_count = await db.count_due_cards(user_id, cefr_levels=cefr)
            display_count = due_count + (20 if due_count <= 150 else 0)
            await bot.send_message(
                chat_id=user_id,
                text=f"Guten Morgen! You have {display_count} cards due today.",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning("morning_trigger failed for user %s: %s", user_id, e)


async def nag_check(bot) -> None:
    """Remind every user who hasn't finished their session today."""
    all_users = await db.get_all_users()
    user_ids = {u["user_id"] for u in all_users}
    user_ids.add(config.AUTHORIZED_CHAT_ID)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Start Session", callback_data="start_session")]]
    )

    for user_id in user_ids:
        try:
            session = qm.get_session(user_id)
            if session.kill_switch:
                continue

            settings = await db.get_user_settings(user_id)
            cefr = settings["cefr_levels"]

            if session.active:
                remaining = session.remaining_count()
            else:
                due_count = await db.count_due_cards(user_id, cefr_levels=cefr)
                remaining = due_count + (20 if due_count <= 150 else 0)

            if remaining == 0:
                continue

            tomorrow_pile = remaining * 2
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"⏰ You still have {remaining} card{'s' if remaining != 1 else ''} left for today.\n\n"
                    f"Skip today and these cards pile onto tomorrow — "
                    f"you could be facing ~{tomorrow_pile} instead of ~{remaining}. "
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
