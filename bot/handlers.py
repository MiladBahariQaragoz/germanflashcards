import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

from bot import config
from bot import db
from bot import fsrs_service
from bot import queue_manager as qm


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def _is_authorized(update: Update) -> bool:
    """Return True if the user is the admin OR a registered user."""
    user = update.effective_user
    if user is None:
        return False
    if user.id == config.AUTHORIZED_CHAT_ID:
        return True
    return await db.is_registered_user(user.id)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    user = update.effective_user
    await db.register_user(user.id, user.username)
    await update.message.reply_text(
        "👋 Willkommen! This bot helps you learn German using spaced repetition (FSRS).\n\n"
        "📋 *Commands*\n"
        "📚 /grammar — Start your grammar session (sentence exercises)\n"
        "▶️ /vocab — Start your vocabulary session\n"
        "📊 /stats — Grammar + vocabulary progress\n"
        "⚙️ /settings — Study direction & CEFR levels\n\n"
        "⏰ *Daily routine*\n"
        "Every morning at 8:00 (Berlin time) you'll get a message with today's grammar and vocab counts. "
        "Complete both sessions for the best results! "
        "Reminders are sent every 2 hours if sessions are unfinished.\n\n"
        "Ready? Start with 📚 /grammar or ▶️ /vocab! 🚀",
        parse_mode="Markdown",
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    user_id = update.effective_user.id
    settings = await db.get_user_settings(user_id)
    cefr = settings["cefr_levels"]
    cefr_label = ", ".join(sorted(cefr))

    vocab_counts, grammar_counts = await asyncio.gather(
        db.get_card_counts_by_state(user_id, cefr_levels=cefr),
        db.get_grammar_card_counts_by_state(user_id, cefr_levels=cefr),
    )

    text = (
        f"📊 Stats (levels: {cefr_label})\n\n"
        f"🗂️ *Vocabulary*\n"
        f"New: {vocab_counts['New']}\n"
        f"Learning: {vocab_counts['Learning']}\n"
        f"Review: {vocab_counts['Review']}\n"
        f"Relearning: {vocab_counts['Relearning']}\n\n"
        f"📚 *Grammar*\n"
        f"New: {grammar_counts['New']}\n"
        f"Learning: {grammar_counts['Learning']}\n"
        f"Review: {grammar_counts['Review']}\n"
        f"Relearning: {grammar_counts['Relearning']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    await _start_session(
        context,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
    )


async def cmd_grammar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    await _start_grammar_session(
        context,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
    )


async def cmd_vocab(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias for /session — starts the vocabulary study session."""
    if not await _is_authorized(update):
        return
    await _start_session(
        context,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
    )


async def cmd_create_invite(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin-only: generate a one-time invite code (valid 48 h) and send it."""
    if update.effective_user is None or update.effective_user.id != config.AUTHORIZED_CHAT_ID:
        return
    code = await db.create_otp(ttl_hours=48)
    await update.message.reply_text(
        f"Invite code (valid 48 h) — send this to the new user:\n\n"
        f"`/login {code}`",
        parse_mode="Markdown",
    )


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow an unregistered user to register using an OTP invite code."""
    user = update.effective_user
    if user is None:
        return

    # Admin never needs to log in
    if user.id == config.AUTHORIZED_CHAT_ID:
        await update.message.reply_text("You're the admin — no login needed!")
        return

    # Already registered?
    if await db.is_registered_user(user.id):
        await update.message.reply_text(
            "You're already registered. Use /session to start."
        )
        return

    # Require exactly one argument: the OTP code
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: /login <invite_code>")
        return

    code = context.args[0]
    valid = await db.consume_otp(code)
    if not valid:
        await update.message.reply_text(
            "Invalid or expired invite code. Ask the admin for a new one."
        )
        return

    await db.register_user(user.id, user.username)
    await update.message.reply_text(
        "Welcome! You're now registered. 🎉\n\n"
        "Use /session to start studying or /settings to configure your preferences."
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    user_id = update.effective_user.id
    settings = await db.get_user_settings(user_id)
    keyboard = _settings_keyboard(settings)
    await update.message.reply_text("⚙️ Your settings:", reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    """Build the settings inline keyboard showing current state."""
    direction = settings["study_direction"]
    cefr = settings["cefr_levels"]

    direction_label = (
        "Direction: 🇩🇪→🇬🇧 (tap to flip)"
        if direction == "DE->EN"
        else "Direction: 🇬🇧→🇩🇪 (tap to flip)"
    )

    def cefr_btn(level: str) -> InlineKeyboardButton:
        active = level in cefr
        label = f"{'✅' if active else '◻'} {level}"
        return InlineKeyboardButton(label, callback_data=f"settings_cefr:{level}")

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(direction_label, callback_data="settings_direction")],
        [cefr_btn("A1"), cefr_btn("A2"), cefr_btn("B1"), cefr_btn("B2")],
    ])


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

async def _start_session(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    settings = await db.get_user_settings(user_id)
    cefr = settings["cefr_levels"]
    due = await db.get_due_cards(user_id, cefr_levels=cefr)
    new = (
        await db.get_new_cards(user_id, 20, cefr_levels=cefr)
        if len(due) <= 150
        else []
    )
    qm.get_session(user_id).build(due_cards=due, new_cards=new)
    card = qm.get_session(user_id).pop_next()
    if card is None:
        await context.bot.send_message(chat_id=chat_id, text="No cards due today!")
        return
    await _send_card_front(
        context, chat_id, card, study_direction=settings["study_direction"]
    )


async def _send_card_front(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    card: dict,
    study_direction: str = "DE->EN",
) -> None:
    card_id = card["_id"]
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Show Answer", callback_data=f"show_answer:{card_id}")]]
    )
    cefr = card.get("cefr_level", "?")
    if study_direction == "DE->EN":
        front_text = f"[{cefr}] 🇩🇪 {card['word']}"
        if card.get("german_sentence"):
            front_text += f"\n📝 {card['german_sentence']}"
    else:
        front_text = f"[{cefr}] 🇬🇧 {card.get('translation', '')}"
        if card.get("english_translation"):
            front_text += f"\n📝 {card['english_translation']}"
    await context.bot.send_message(
        chat_id=chat_id, text=front_text, reply_markup=keyboard
    )


# ---------------------------------------------------------------------------
# Grammar session helpers
# ---------------------------------------------------------------------------

async def _start_grammar_session(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    settings = await db.get_user_settings(user_id)
    cefr = settings["cefr_levels"]
    due = await db.get_due_grammar_cards(user_id, cefr_levels=cefr)
    new = (
        await db.get_new_grammar_cards(user_id, 20, cefr_levels=cefr)
        if len(due) <= 150
        else []
    )
    qm.get_grammar_session(user_id).build(due_cards=due, new_cards=new)
    card = qm.get_grammar_session(user_id).pop_next()
    if card is None:
        await context.bot.send_message(chat_id=chat_id, text="No grammar cards due today! 🎉")
        return
    await _send_grammar_card_front(
        context, chat_id, card, study_direction=settings["study_direction"]
    )


async def _send_grammar_card_front(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    card: dict,
    study_direction: str = "DE->EN",
) -> None:
    card_id = card["_id"]
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Show Answer", callback_data=f"show_grammar:{card_id}")]]
    )
    cefr = card.get("cefr_level", "?")
    if study_direction == "DE->EN":
        front_text = f"📚 [{cefr}] 🇩🇪 {card.get('german_sentence', card['word'])}"
    else:
        front_text = f"📚 [{cefr}] 🇬🇧 {card.get('english_translation', card.get('translation', ''))}"
    await context.bot.send_message(
        chat_id=chat_id, text=front_text, reply_markup=keyboard
    )


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------

async def callback_start_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return
    await _start_session(
        context,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
    )


async def callback_show_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return

    user_id = update.effective_user.id
    card_id = query.data.split(":", 1)[1]

    card = await db.get_card_by_id(user_id, card_id)
    if card is None:
        await query.edit_message_text("Card not found.")
        return

    settings = await db.get_user_settings(user_id)
    intervals = fsrs_service.preview_intervals(card)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Again ({intervals[1]})", callback_data=f"grade:{card_id}:1"
                ),
                InlineKeyboardButton(
                    f"Hard ({intervals[2]})", callback_data=f"grade:{card_id}:2"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"Good ({intervals[3]})", callback_data=f"grade:{card_id}:3"
                ),
                InlineKeyboardButton(
                    f"Easy ({intervals[4]})", callback_data=f"grade:{card_id}:4"
                ),
            ],
        ]
    )
    cefr = card.get("cefr_level", "?")
    if settings["study_direction"] == "DE->EN":
        back_text = f"[{cefr}] 🇩🇪 {card['word']}\n🇬🇧 {card['translation']}"
        if card.get("german_sentence"):
            back_text += f"\n\n📝 {card['german_sentence']}"
        if card.get("english_translation"):
            back_text += f"\n🇬🇧 {card['english_translation']}"
    else:
        back_text = f"[{cefr}] 🇬🇧 {card.get('translation', '')}\n🇩🇪 {card['word']}"
        if card.get("english_translation"):
            back_text += f"\n\n📝 {card['english_translation']}"
        if card.get("german_sentence"):
            back_text += f"\n🇩🇪 {card['german_sentence']}"

    await query.edit_message_text(text=back_text, reply_markup=keyboard)


async def callback_grade(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return

    user_id = update.effective_user.id
    _, card_id, rating_str = query.data.split(":")
    rating_int = int(rating_str)

    card = await db.get_card_by_id(user_id, card_id)
    if card is None:
        return

    update_fields, _ = fsrs_service.rate_card(card, rating_int)
    await db.update_card_after_review(user_id, card_id, update_fields, card)
    await query.edit_message_reply_markup(reply_markup=None)

    if rating_int == 1:
        updated_card = {**card, **update_fields}
        qm.get_session(user_id).add_to_again_pile(updated_card)

    next_card = qm.get_session(user_id).pop_next()
    chat_id = update.effective_chat.id

    if next_card is None:
        qm.get_session(user_id).check_and_set_kill_switch()
        await context.bot.send_message(
            chat_id=chat_id,
            text="Glückwunsch! All done for today. See you tomorrow! 🎉",
        )
        return

    settings = await db.get_user_settings(user_id)
    await _send_card_front(
        context, chat_id, next_card, study_direction=settings["study_direction"]
    )


async def callback_start_grammar_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return
    await _start_grammar_session(
        context,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
    )


async def callback_show_grammar_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return

    user_id = update.effective_user.id
    card_id = query.data.split(":", 1)[1]

    card = await db.get_grammar_card_by_id(user_id, card_id)
    if card is None:
        await query.edit_message_text("Grammar card not found.")
        return

    settings = await db.get_user_settings(user_id)
    intervals = fsrs_service.preview_intervals(card)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Again ({intervals[1]})", callback_data=f"grade_grammar:{card_id}:1"
                ),
                InlineKeyboardButton(
                    f"Hard ({intervals[2]})", callback_data=f"grade_grammar:{card_id}:2"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"Good ({intervals[3]})", callback_data=f"grade_grammar:{card_id}:3"
                ),
                InlineKeyboardButton(
                    f"Easy ({intervals[4]})", callback_data=f"grade_grammar:{card_id}:4"
                ),
            ],
        ]
    )
    cefr = card.get("cefr_level", "?")
    if settings["study_direction"] == "DE->EN":
        back_text = (
            f"📚 [{cefr}] 🇩🇪 {card.get('german_sentence', card['word'])}\n"
            f"🇬🇧 {card.get('english_translation', card.get('translation', ''))}\n\n"
            f"💡 {card['word']} — {card['translation']}"
        )
    else:
        back_text = (
            f"📚 [{cefr}] 🇬🇧 {card.get('english_translation', card.get('translation', ''))}\n"
            f"🇩🇪 {card.get('german_sentence', card['word'])}\n\n"
            f"💡 {card['word']} — {card['translation']}"
        )
    await query.edit_message_text(text=back_text, reply_markup=keyboard)


async def callback_grade_grammar(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return

    user_id = update.effective_user.id
    _, card_id, rating_str = query.data.split(":", 2)
    rating_int = int(rating_str)

    card = await db.get_grammar_card_by_id(user_id, card_id)
    if card is None:
        return

    update_fields, _ = fsrs_service.rate_card(card, rating_int)
    await db.update_grammar_card_after_review(user_id, card_id, update_fields, card)
    await query.edit_message_reply_markup(reply_markup=None)

    if rating_int == 1:
        updated_card = {**card, **update_fields}
        qm.get_grammar_session(user_id).add_to_again_pile(updated_card)

    next_card = qm.get_grammar_session(user_id).pop_next()
    chat_id = update.effective_chat.id

    if next_card is None:
        qm.get_grammar_session(user_id).check_and_set_kill_switch()
        await context.bot.send_message(
            chat_id=chat_id,
            text="📚 Grammatik fertig! Grammar session complete. 🎉\n\nDon't forget your vocabulary session — /vocab",
        )
        return

    settings = await db.get_user_settings(user_id)
    await _send_grammar_card_front(
        context, chat_id, next_card, study_direction=settings["study_direction"]
    )


async def callback_settings_direction(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return
    user_id = update.effective_user.id
    settings = await db.get_user_settings(user_id)
    new_direction = "EN->DE" if settings["study_direction"] == "DE->EN" else "DE->EN"
    await db.update_user_settings(user_id, {"study_direction": new_direction})
    settings["study_direction"] = new_direction
    await query.edit_message_reply_markup(reply_markup=_settings_keyboard(settings))


async def callback_settings_cefr(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not await _is_authorized(update):
        await query.answer()
        return
    user_id = update.effective_user.id
    level = query.data.split(":")[1]  # "A1" | "A2" | "B1" | "B2"
    settings = await db.get_user_settings(user_id)
    levels = list(settings["cefr_levels"])
    if level in levels:
        if len(levels) == 1:
            await query.answer(
                "You must keep at least one CEFR level active.", show_alert=True
            )
            return
        levels.remove(level)
    else:
        levels.append(level)
    await db.update_user_settings(user_id, {"cefr_levels": levels})
    settings["cefr_levels"] = levels
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=_settings_keyboard(settings))
