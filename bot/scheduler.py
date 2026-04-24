from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pytz import timezone as pytz_timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot import config
from bot import db
from bot import queue_manager as qm

BERLIN = pytz_timezone("Europe/Berlin")


async def morning_trigger(bot) -> None:
    qm.session.reset()
    due_count = await db.count_due_cards()
    display_count = due_count + (20 if due_count <= 150 else 0)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Start Session", callback_data="start_session")]]
    )
    await bot.send_message(
        chat_id=config.AUTHORIZED_CHAT_ID,
        text=f"Guten Morgen! You have {display_count} cards due today.",
        reply_markup=keyboard,
    )


async def nag_check(bot) -> None:
    if qm.session.kill_switch:
        return
    if qm.session.active:
        remaining = qm.session.remaining_count()
    else:
        due_count = await db.count_due_cards()
        remaining = due_count + (20 if due_count <= 150 else 0)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Start Session", callback_data="start_session")]]
    )
    await bot.send_message(
        chat_id=config.AUTHORIZED_CHAT_ID,
        text=f"Friendly reminder: {remaining} cards remaining today.",
        reply_markup=keyboard,
    )


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
