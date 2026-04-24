from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bson import ObjectId

from bot import config
from bot import db
from bot import fsrs_service
from bot import queue_manager as qm


def _auth(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == config.AUTHORIZED_CHAT_ID


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _auth(update):
        return
    await update.message.reply_text(
        "Willkommen! Use /session to start studying or /stats to see your progress."
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _auth(update):
        return
    counts = await db.get_card_counts_by_state()
    text = (
        f"New: {counts['New']}\n"
        f"Learning: {counts['Learning']}\n"
        f"Review: {counts['Review']}\n"
        f"Relearning: {counts['Relearning']}"
    )
    await update.message.reply_text(text)


async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _auth(update):
        return
    await _start_session(context, chat_id=update.effective_chat.id)


async def callback_start_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not _auth(update):
        return
    await _start_session(context, chat_id=update.effective_chat.id)


async def _start_session(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    due = await db.get_due_cards()
    new = await db.get_new_cards(20) if len(due) <= 150 else []
    qm.session.build(due_cards=due, new_cards=new)
    card = qm.session.pop_next()
    if card is None:
        await context.bot.send_message(chat_id=chat_id, text="No cards due today!")
        return
    await _send_card_front(context, chat_id, card)


async def _send_card_front(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, card: dict
) -> None:
    card_id = str(card["_id"])
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Show Answer", callback_data=f"show_answer:{card_id}")]]
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🇩🇪 {card['word']}",
        reply_markup=keyboard,
    )


async def callback_show_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not _auth(update):
        return

    card_id_str = query.data.split(":", 1)[1]
    card_id = ObjectId(card_id_str)

    card = await db.get_card_by_id(card_id)
    if card is None:
        await query.edit_message_text("Card not found.")
        return

    intervals = fsrs_service.preview_intervals(card)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Again ({intervals[1]})",
                    callback_data=f"grade:{card_id_str}:1",
                ),
                InlineKeyboardButton(
                    f"Hard ({intervals[2]})",
                    callback_data=f"grade:{card_id_str}:2",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"Good ({intervals[3]})",
                    callback_data=f"grade:{card_id_str}:3",
                ),
                InlineKeyboardButton(
                    f"Easy ({intervals[4]})",
                    callback_data=f"grade:{card_id_str}:4",
                ),
            ],
        ]
    )
    await query.edit_message_text(
        text=f"🇩🇪 {card['word']}\n🇬🇧 {card['translation']}",
        reply_markup=keyboard,
    )


async def callback_grade(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not _auth(update):
        return

    _, card_id_str, rating_str = query.data.split(":")
    card_id = ObjectId(card_id_str)
    rating_int = int(rating_str)

    card = await db.get_card_by_id(card_id)
    if card is None:
        return

    update_fields, _ = fsrs_service.rate_card(card, rating_int)
    await db.update_card_after_review(card_id, update_fields)

    await query.edit_message_reply_markup(reply_markup=None)

    if rating_int == 1:
        updated_card = {**card, **update_fields}
        qm.session.add_to_again_pile(updated_card)

    next_card = qm.session.pop_next()
    chat_id = update.effective_chat.id

    if next_card is None:
        qm.session.check_and_set_kill_switch()
        await context.bot.send_message(
            chat_id=chat_id,
            text="Glückwunsch! All done for today. See you tomorrow! 🎉",
        )
        return

    await _send_card_front(context, chat_id, next_card)
