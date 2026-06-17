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
    user = update.effective_user
    if user is None:
        return
    # Registered users (and the admin) get the command help; everyone else sees
    # the intro + a "Request access" button (invite-only, admin-approved).
    if await _is_authorized(update):
        await db.register_user(user.id, user.username)
        await update.message.reply_text(
            "👋 Willkommen! This bot helps you learn German using spaced repetition (FSRS).\n\n"
            "📋 *Commands*\n"
            "📚 /grammar — Start your grammar session (sentence exercises)\n"
            "▶️ /vocab — Start your vocabulary session\n"
            "📊 /stats — Grammar + vocabulary progress\n"
            "🏆 /leaderboard — Top streak holders\n"
            "⚙️ /settings — Study direction & CEFR levels\n"
            "👨‍💻 /developer — About the developer\n\n"
            "⏰ *Daily routine*\n"
            "Every morning at 8:00 (Berlin time) you'll get a message with today's grammar and vocab counts. "
            "Complete both sessions for the best results! "
            "Reminders are sent every 2 hours if sessions are unfinished.\n\n"
            "Ready? Start with 📚 /grammar or ▶️ /vocab! 🚀",
            parse_mode="Markdown",
        )
        return

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔑 Request access", callback_data="request_access")]]
    )
    await update.message.reply_text(
        "👋 *Welcome!*\n\n"
        "This bot helps you learn German with spaced repetition (FSRS) — daily "
        "grammar and vocabulary flashcards that adapt to your memory, with streaks "
        "and a leaderboard to keep you motivated.\n\n"
        "👨‍💻 Built by [Milad Bahari Qaragoz](https://qaragoz.vercel.app/)\n\n"
        "🔒 Access is invite-only. Tap below to request access — the admin will "
        "review it and you'll be notified.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorized(update):
        return
    user_id = update.effective_user.id
    settings = await db.get_user_settings(user_id)
    cefr = settings["cefr_levels"]
    cefr_label = ", ".join(sorted(cefr))

    vocab_counts, grammar_counts, streak = await asyncio.gather(
        db.get_card_counts_by_state(user_id, cefr_levels=cefr),
        db.get_grammar_card_counts_by_state(user_id, cefr_levels=cefr),
        db.get_streak(user_id),
    )

    text = (
        f"📊 Stats (levels: {cefr_label})\n"
        f"{_streak_line(streak)}\n\n"
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


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Public streak leaderboard — top active streak holders and their streaks."""
    if not await _is_authorized(update):
        return
    entries = await db.get_leaderboard(limit=10)
    if not entries:
        await update.message.reply_text(
            "🏆 No active streaks yet — be the first!\n"
            "Clear both your grammar and vocab sessions today to get on the board."
        )
        return
    # Plain text (no parse_mode): Telegram usernames often contain '_' which would
    # break Markdown formatting.
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    lines = ["🏆 Streak Leaderboard\n"]
    for i, e in enumerate(entries):
        name = f"@{e['username']}" if e["username"] else f"User {e['user_id']}"
        rank = medals.get(i, f"{i + 1}.")
        days = "day" if e["streak"] == 1 else "days"
        lines.append(f"{rank} {name} — 🔥 {e['streak']} {days}")
    await update.message.reply_text("\n".join(lines))


async def cmd_developer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show developer credit with a link to the portfolio. Open to everyone."""
    await update.message.reply_text(
        "👨‍💻 *Developer*\n\n"
        "This bot was built by [Milad Bahari Qaragoz](https://qaragoz.vercel.app/).\n\n"
        "🔗 Portfolio: https://qaragoz.vercel.app/",
        parse_mode="Markdown",
    )


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


# ---------------------------------------------------------------------------
# Access request flow (new-user onboarding, admin-approved)
# ---------------------------------------------------------------------------

async def callback_request_access(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A new user tapped 'Request access' — record it and notify the admin."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if user is None:
        return

    status = await db.create_access_request(user.id, user.username, user.full_name)
    if status == "registered":
        await query.edit_message_text(
            "✅ You already have access! Tap /grammar or /vocab to start. 🚀"
        )
        return
    if status == "pending":
        await query.edit_message_text(
            "⏳ Your request is already pending — the admin will review it soon."
        )
        return

    # New request — alert the admin with Approve / Deny buttons.
    name = user.full_name or "(no name)"
    handle = f"@{user.username}" if user.username else "(no username)"
    admin_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_user:{user.id}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"deny_user:{user.id}"),
        ]
    ])
    await context.bot.send_message(
        chat_id=config.AUTHORIZED_CHAT_ID,
        text=(
            f"🔔 New access request\n\n"
            f"Name: {name}\n"
            f"Username: {handle}\n"
            f"ID: {user.id}\n\n"
            f"Grant access?"
        ),
        reply_markup=admin_keyboard,
    )
    await query.edit_message_text(
        "✅ Request sent! The admin will review it and you'll be notified. ⏳"
    )


async def callback_approve_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin-only: approve a pending access request."""
    query = update.callback_query
    await query.answer()
    if update.effective_user is None or update.effective_user.id != config.AUTHORIZED_CHAT_ID:
        return
    target_id = int(query.data.split(":")[1])
    ok = await db.approve_access_request(target_id)
    if not ok:
        await query.edit_message_text("⚠️ That request no longer exists.")
        return
    await query.edit_message_text(f"{query.message.text}\n\n✅ Approved.")
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "🎉 You've been granted access!\n\n"
                "Tap /start to see the commands, then 📚 /grammar or ▶️ /vocab to begin."
            ),
        )
    except Exception as e:
        logger.warning("could not notify approved user %s: %s", target_id, e)


async def callback_deny_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin-only: deny a pending access request."""
    query = update.callback_query
    await query.answer()
    if update.effective_user is None or update.effective_user.id != config.AUTHORIZED_CHAT_ID:
        return
    target_id = int(query.data.split(":")[1])
    ok = await db.deny_access_request(target_id)
    if not ok:
        await query.edit_message_text("⚠️ That request no longer exists.")
        return
    await query.edit_message_text(f"{query.message.text}\n\n❌ Denied.")
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="Sorry — your access request was not approved.",
        )
    except Exception as e:
        logger.warning("could not notify denied user %s: %s", target_id, e)


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

def _progress_bar(done: int, total: int, width: int = 10) -> str:
    """Render a text completion bar, e.g. '████░░░░░░ 4/10 (40%)'. Empty if no total."""
    if total <= 0:
        return ""
    filled = max(0, min(width, round(width * done / total)))
    bar = "█" * filled + "░" * (width - filled)
    pct = round(100 * done / total)
    return f"{bar} {done}/{total} ({pct}%)"


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
    session = qm.get_session(user_id)
    session.build(due_cards=due, new_cards=new)
    card = session.pop_next()
    if card is None:
        await _on_domain_empty(context, chat_id, user_id, "vocab")
        return
    await _send_card_front(
        context, chat_id, card,
        study_direction=settings["study_direction"],
        progress=session.progress(),
    )


async def _send_card_front(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    card: dict,
    study_direction: str = "DE->EN",
    progress: tuple[int, int] | None = None,
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
    if progress is not None:
        bar = _progress_bar(*progress)
        if bar:
            front_text = f"{bar}\n\n{front_text}"
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
    session = qm.get_grammar_session(user_id)
    session.build(due_cards=due, new_cards=new)
    card = session.pop_next()
    if card is None:
        await _on_domain_empty(context, chat_id, user_id, "grammar")
        return
    await _send_grammar_card_front(
        context, chat_id, card,
        study_direction=settings["study_direction"],
        progress=session.progress(),
    )


async def _send_grammar_card_front(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    card: dict,
    study_direction: str = "DE->EN",
    progress: tuple[int, int] | None = None,
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
    if progress is not None:
        bar = _progress_bar(*progress)
        if bar:
            front_text = f"{bar}\n\n{front_text}"
    await context.bot.send_message(
        chat_id=chat_id, text=front_text, reply_markup=keyboard
    )


# ---------------------------------------------------------------------------
# Session completion hooks (streak logic is layered in here)
# ---------------------------------------------------------------------------

def _streak_line(streak: int) -> str:
    return f"🔥 Streak: {streak} day{'s' if streak != 1 else ''}!"


async def _on_domain_empty(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, domain: str
) -> None:
    """A session had nothing due. Still record it satisfied so the streak can complete."""
    session = qm.get_session(user_id) if domain == "vocab" else qm.get_grammar_session(user_id)
    session.reset()
    session.kill_switch = True
    settings = await db.get_user_settings(user_id)
    cefr = settings["cefr_levels"]
    if domain == "vocab":
        other_due = await db.count_due_grammar_cards(user_id, cefr_levels=cefr)
        text = "No vocab cards due today! 🎉"
    else:
        other_due = await db.count_due_cards(user_id, cefr_levels=cefr)
        text = "No grammar cards due today! 🎉"
    result = await db.record_session_cleared(user_id, domain, other_domain_due=other_due)
    if result["advanced"]:
        text += "\n\n" + _streak_line(result["streak"])
    await context.bot.send_message(chat_id=chat_id, text=text)


async def _on_vocab_session_cleared(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    settings = await db.get_user_settings(user_id)
    grammar_due = await db.count_due_grammar_cards(
        user_id, cefr_levels=settings["cefr_levels"]
    )
    result = await db.record_session_cleared(
        user_id, "vocab", other_domain_due=grammar_due
    )
    if result["both_done"]:
        text = (
            "🗂️ Vocabulary done — both sessions complete for today! 🎉\n\n"
            + _streak_line(result["streak"])
        )
    else:
        text = (
            "🗂️ Vocabulary session complete! 🎉\n\n"
            "One more to go — don't forget your grammar session: /grammar"
        )
    await context.bot.send_message(chat_id=chat_id, text=text)


async def _on_grammar_session_cleared(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    settings = await db.get_user_settings(user_id)
    vocab_due = await db.count_due_cards(
        user_id, cefr_levels=settings["cefr_levels"]
    )
    result = await db.record_session_cleared(
        user_id, "grammar", other_domain_due=vocab_due
    )
    if result["both_done"]:
        text = (
            "📚 Grammatik fertig — both sessions complete for today! 🎉\n\n"
            + _streak_line(result["streak"])
        )
    else:
        text = (
            "📚 Grammatik fertig! Grammar session complete. 🎉\n\n"
            "Don't forget your vocabulary session — /vocab"
        )
    await context.bot.send_message(chat_id=chat_id, text=text)


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

    session = qm.get_session(user_id)
    if rating_int == 1:
        updated_card = {**card, **update_fields}
        session.add_to_again_pile(updated_card)
    else:
        session.mark_reviewed()

    next_card = session.pop_next()
    chat_id = update.effective_chat.id

    if next_card is None:
        session.check_and_set_kill_switch()
        await _on_vocab_session_cleared(context, chat_id, user_id)
        return

    settings = await db.get_user_settings(user_id)
    await _send_card_front(
        context, chat_id, next_card,
        study_direction=settings["study_direction"],
        progress=session.progress(),
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

    session = qm.get_grammar_session(user_id)
    if rating_int == 1:
        updated_card = {**card, **update_fields}
        session.add_to_again_pile(updated_card)
    else:
        session.mark_reviewed()

    next_card = session.pop_next()
    chat_id = update.effective_chat.id

    if next_card is None:
        session.check_and_set_kill_switch()
        await _on_grammar_session_cleared(context, chat_id, user_id)
        return

    settings = await db.get_user_settings(user_id)
    await _send_grammar_card_front(
        context, chat_id, next_card,
        study_direction=settings["study_direction"],
        progress=session.progress(),
    )


async def callback_spread_backlog(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Spread the user's due backlog into daily installments (~CATCHUP_PER_DAY/day)."""
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return
    user_id = update.effective_user.id
    settings = await db.get_user_settings(user_id)
    moved = await db.spread_backlog(
        user_id, settings["cefr_levels"], per_day=db.CATCHUP_PER_DAY
    )
    total = moved["vocab"] + moved["grammar"]
    if total == 0:
        await query.edit_message_text(
            "Your backlog already fits in a single day — nothing to spread. 👍"
        )
        return
    await query.edit_message_text(
        f"✅ Backlog spread into daily installments (~{db.CATCHUP_PER_DAY}/day).\n\n"
        f"Moved {total} card{'s' if total != 1 else ''} to upcoming days "
        f"({moved['vocab']} vocab, {moved['grammar']} grammar).\n\n"
        f"Today is manageable now — tap /grammar or /vocab to begin! 💪"
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
