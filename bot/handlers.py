import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

from bot import config
from bot import db
from bot import fsrs_service
from bot import queue_manager as qm
from bot import catchup as catchup_logic
from bot import scheduling


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
            "📚 /grammar — Study session, starting with fresh grammar\n"
            "▶️ /vocab — Study session, starting with fresh vocabulary\n"
            "📊 /stats — Your progress & next-session size\n"
            "🧩 /catchup — How your review backlog is handled\n"
            "🏆 /leaderboard — Top streak holders\n"
            "⚙️ /settings — Study direction & CEFR levels\n"
            "👨‍💻 /developer — About the developer\n\n"
            "🔁 *How it works*\n"
            "Grammar and vocabulary reviews share one queue, so /grammar and /vocab pull "
            "from the same due cards — they differ only in which *new* cards they add. "
            "Each session is capped at a manageable size, so even a big backlog comes back "
            "a little at a time instead of all at once. Cards are scheduled by session, "
            "not by the calendar — a card spaced “5 sessions” reappears after five more "
            "study sessions.\n\n"
            "⏰ *Daily rhythm*\n"
            "Every morning at 8:00 (Berlin time) you'll get your next-session count. "
            "Study on any given day to keep your 🔥 streak alive — clearing either grammar "
            "or vocab counts. Reminders come every 2 hours while cards are still waiting.\n\n"
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
    counter = settings["session_counter"]
    cefr_label = ", ".join(sorted(cefr))

    vocab_counts, grammar_counts, streak, preview = await asyncio.gather(
        db.get_card_counts_by_state(user_id, cefr_levels=cefr),
        db.get_grammar_card_counts_by_state(user_id, cefr_levels=cefr),
        db.get_streak(user_id),
        db.preview_next_session(user_id, counter, cefr_levels=cefr),
    )
    # The review queue is unified — tapping /vocab or /grammar both serve the same
    # capped due pool, differing only in which new cards get added. So the headline
    # is a single figure: capped due reviews + the new-card allowance (20 when the
    # backlog is small enough for new cards to resume). This matches what an open
    # actually shows, so /stats never disagrees with the session again.
    new_each = 20 if preview["allow_new"] else 0
    next_session = preview["total"] + new_each
    new_note = f" (incl. {new_each} new)" if new_each else ""

    text = (
        f"📊 Stats (levels: {cefr_label})\n"
        f"{_streak_line(streak)}\n"
        f"📅 Next session: {next_session} card{'s' if next_session != 1 else ''}"
        f"{new_note}\n"
        f"   ({preview['total']} review{'s' if preview['total'] != 1 else ''} due, "
        f"same pool for grammar & vocab)\n\n"
        f"🗂️ *Vocabulary* (deck totals)\n"
        f"New: {vocab_counts['New']}\n"
        f"Learning: {vocab_counts['Learning']}\n"
        f"Review: {vocab_counts['Review']}\n"
        f"Relearning: {vocab_counts['Relearning']}\n\n"
        f"📚 *Grammar* (deck totals)\n"
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
        "This bot was built by [Milad Bahari Qaragoz](https://qaragoz.vercel.app/).",
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


# ---------------------------------------------------------------------------
# Admin: user roster + silent removal (admin-only, not in the public menu)
# ---------------------------------------------------------------------------

def _is_admin(update: Update) -> bool:
    return (
        update.effective_user is not None
        and update.effective_user.id == config.AUTHORIZED_CHAT_ID
    )


def _admin_user_label(row: dict) -> str:
    return f"@{row['username']}" if row["username"] else f"User {row['user_id']}"


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: list every user, how they're doing, and a remove button each."""
    if not _is_admin(update):
        return
    rows = await db.get_admin_overview()
    if not rows:
        await update.message.reply_text("👥 No registered users yet.")
        return
    # Plain text (no Markdown): usernames often contain '_'.
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    lines = [f"👥 Users ({len(rows)}) — streak · due V/G · levels\n"]
    buttons = []
    for i, r in enumerate(rows):
        rank = medals.get(i, f"{i + 1}.")
        label = _admin_user_label(r)
        levels = ",".join(sorted(r["cefr_levels"]))
        lines.append(
            f"{rank} {label} — 🔥 {r['streak']}d · "
            f"due {r['vocab_due']}/{r['grammar_due']} · {levels}"
        )
        buttons.append([
            InlineKeyboardButton(f"🗑 {label}", callback_data=f"admin_rm:{r['user_id']}")
        ])
    await update.message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons)
    )


async def callback_admin_remove(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin tapped 🗑 on a user — ask for confirmation before deleting."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(update):
        return
    target_id = int(query.data.split(":")[1])
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Remove", callback_data=f"admin_rmok:{target_id}"),
        InlineKeyboardButton("↩️ Cancel", callback_data="admin_rmno"),
    ]])
    await query.edit_message_text(
        f"⚠️ Remove user {target_id}?\n\n"
        "This deletes their access and all their progress (both domains), "
        "silently — they are NOT notified. They can /start to request access again.",
        reply_markup=keyboard,
    )


async def callback_admin_remove_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin confirmed removal — delete the user silently."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(update):
        return
    target_id = int(query.data.split(":")[1])
    deleted = await db.remove_user(target_id)
    qm.drop_user(target_id)
    await query.edit_message_text(
        f"✅ Removed user {target_id} silently.\n"
        f"Deleted {deleted['vocab']} vocab + {deleted['grammar']} grammar progress docs.\n\n"
        "Run /admin to refresh the list."
    )


async def callback_admin_remove_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin backed out of a removal."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(update):
        return
    await query.edit_message_text("↩️ Cancelled. Run /admin to refresh the list.")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin-only: broadcast a message to every registered user. Shows a preview with
    a confirm step before anything is sent. Usage: /broadcast <message>.
    """
    if not _is_admin(update):
        return
    # Keep everything after the command verbatim (preserves newlines/formatting).
    parts = (update.message.text or "").split(maxsplit=1)
    message = parts[1].strip() if len(parts) > 1 else ""
    if not message:
        await update.message.reply_text(
            "📢 Usage: /broadcast <message>\n\n"
            "Sends your message to every registered user (after a confirm)."
        )
        return
    context.user_data["pending_broadcast"] = message
    recipients = len(await db.get_all_users())
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 Send to all", callback_data="broadcast_send"),
        InlineKeyboardButton("↩️ Cancel", callback_data="broadcast_cancel"),
    ]])
    await update.message.reply_text(
        f"📢 Preview — this will go to {recipients} user{'s' if recipients != 1 else ''}:\n\n"
        f"{message}",
        reply_markup=keyboard,
    )


async def callback_broadcast_send(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin confirmed — deliver the pending broadcast to every registered user."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(update):
        return
    message = context.user_data.pop("pending_broadcast", None)
    if not message:
        await query.edit_message_text("⚠️ Nothing to send — the broadcast expired. Try /broadcast again.")
        return
    text = f"📢 Message from the admin:\n\n{message}"
    users = await db.get_all_users()
    sent = failed = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u["user_id"], text=text)
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning("broadcast to %s failed: %s", u.get("user_id"), e)
    await query.edit_message_text(
        f"✅ Broadcast sent to {sent} user{'s' if sent != 1 else ''}"
        + (f" ({failed} failed)." if failed else ".")
    )


async def callback_broadcast_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin backed out of a broadcast."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(update):
        return
    context.user_data.pop("pending_broadcast", None)
    await query.edit_message_text("↩️ Broadcast cancelled — nothing was sent.")


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
    # Every session start advances the shared counter; the review pool is unified
    # across both domains, only the NEW cards are vocab-specific here.
    counter = await db.increment_session_counter(user_id)
    due_vocab, due_grammar = await asyncio.gather(
        db.get_due_cards(user_id, counter, cefr_levels=cefr),
        db.get_due_grammar_cards(user_id, counter, cefr_levels=cefr),
    )
    due = due_vocab + due_grammar
    # Pause new cards while a combined review backlog exists (see NEW_CARD_PAUSE_THRESHOLD).
    # The pause decision uses the TRUE (uncapped) backlog size.
    new = (
        await db.get_new_cards(user_id, 20, cefr_levels=cefr)
        if catchup_logic.new_cards_allowed(len(due), db.NEW_CARD_PAUSE_THRESHOLD)
        else []
    )
    # Serve at most DAILY_REVIEW_CAP due cards (oldest first); the rest stay due and
    # surface in later sessions, so a big backlog never lands as one wall.
    due = catchup_logic.cap_daily_due(due, db.DAILY_REVIEW_CAP)
    session = qm.get_session(user_id)
    session.build(due_cards=due, new_cards=new)
    card = session.pop_next()
    if card is None:
        await _on_domain_empty(context, chat_id, user_id, "vocab")
        return
    await _send_next_card(
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


async def _send_next_card(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    card: dict,
    study_direction: str = "DE->EN",
    progress: tuple[int, int] | None = None,
) -> None:
    """Render a card from the unified queue by its domain.

    The review pool mixes vocab and grammar, so each popped card is displayed with
    its own domain's front + grade buttons (grammar cards carry `_card_type`).
    """
    if card.get("_card_type") == "grammar":
        await _send_grammar_card_front(
            context, chat_id, card,
            study_direction=study_direction, progress=progress,
        )
    else:
        await _send_card_front(
            context, chat_id, card,
            study_direction=study_direction, progress=progress,
        )


# ---------------------------------------------------------------------------
# Grammar session helpers
# ---------------------------------------------------------------------------

async def _start_grammar_session(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    settings = await db.get_user_settings(user_id)
    cefr = settings["cefr_levels"]
    # Every session start advances the shared counter; the review pool is unified
    # across both domains, only the NEW cards are grammar-specific here.
    counter = await db.increment_session_counter(user_id)
    due_vocab, due_grammar = await asyncio.gather(
        db.get_due_cards(user_id, counter, cefr_levels=cefr),
        db.get_due_grammar_cards(user_id, counter, cefr_levels=cefr),
    )
    due = due_vocab + due_grammar
    # Pause new cards while a combined review backlog exists (see NEW_CARD_PAUSE_THRESHOLD).
    # The pause decision uses the TRUE (uncapped) backlog size.
    new = (
        await db.get_new_grammar_cards(user_id, 20, cefr_levels=cefr)
        if catchup_logic.new_cards_allowed(len(due), db.NEW_CARD_PAUSE_THRESHOLD)
        else []
    )
    # Serve at most DAILY_REVIEW_CAP due cards (oldest first); the rest stay due and
    # surface in later sessions, so a big backlog never lands as one wall.
    due = catchup_logic.cap_daily_due(due, db.DAILY_REVIEW_CAP)
    session = qm.get_session(user_id)
    session.build(due_cards=due, new_cards=new)
    card = session.pop_next()
    if card is None:
        await _on_domain_empty(context, chat_id, user_id, "grammar")
        return
    await _send_next_card(
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
    counter = settings["session_counter"]
    if domain == "vocab":
        other_due = await db.count_due_grammar_cards(user_id, counter, cefr_levels=cefr)
    else:
        other_due = await db.count_due_cards(user_id, counter, cefr_levels=cefr)
    # The queue is unified, so an empty session means nothing is waiting in either
    # domain and no new cards are due to be introduced right now.
    text = "🎉 Nothing to review right now — you're all caught up!"
    result = await db.record_session_cleared(user_id, domain, other_domain_due=other_due)
    if result["advanced"]:
        text += "\n\n" + _streak_line(result["streak"])
    await context.bot.send_message(chat_id=chat_id, text=text)


async def _on_vocab_session_cleared(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    settings = await db.get_user_settings(user_id)
    grammar_due = await db.count_due_grammar_cards(
        user_id, settings["session_counter"], cefr_levels=settings["cefr_levels"]
    )
    result = await db.record_session_cleared(
        user_id, "vocab", other_domain_due=grammar_due
    )
    if result["both_done"]:
        text = "🗂️ Session complete — you're all caught up! 🎉"
    else:
        text = (
            "🗂️ Session complete! 🎉\n\n"
            "Want more? Tap /grammar to keep going with fresh grammar cards."
        )
    if result["advanced"]:
        text += "\n\n" + _streak_line(result["streak"])
    await context.bot.send_message(chat_id=chat_id, text=text)


async def _on_grammar_session_cleared(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    settings = await db.get_user_settings(user_id)
    vocab_due = await db.count_due_cards(
        user_id, settings["session_counter"], cefr_levels=settings["cefr_levels"]
    )
    result = await db.record_session_cleared(
        user_id, "grammar", other_domain_due=vocab_due
    )
    if result["both_done"]:
        text = "📚 Grammatik fertig — you're all caught up! 🎉"
    else:
        text = (
            "📚 Grammatik fertig! Session complete. 🎉\n\n"
            "Want more? Tap /vocab to keep going with fresh vocabulary cards."
        )
    if result["advanced"]:
        text += "\n\n" + _streak_line(result["streak"])
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
    counter = await db.get_session_counter(user_id)
    update_fields["due_session"] = scheduling.due_session_for(
        counter, update_fields.pop("interval_sessions")
    )
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
    await _send_next_card(
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
    counter = await db.get_session_counter(user_id)
    update_fields["due_session"] = scheduling.due_session_for(
        counter, update_fields.pop("interval_sessions")
    )
    await db.update_grammar_card_after_review(user_id, card_id, update_fields, card)
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
        await _on_grammar_session_cleared(context, chat_id, user_id)
        return

    settings = await db.get_user_settings(user_id)
    await _send_next_card(
        context, chat_id, next_card,
        study_direction=settings["study_direction"],
        progress=session.progress(),
    )


async def cmd_catchup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Explain how the review backlog is paced. Spreading is now automatic — every
    session is capped at db.DAILY_REVIEW_CAP reviews, so a big pile always comes
    back a little at a time. This command just reassures the user and shows the
    size of their next session.
    """
    if not await _is_authorized(update):
        return
    user_id = update.effective_user.id
    settings = await db.get_user_settings(user_id)
    cefr = settings["cefr_levels"]
    counter = settings["session_counter"]
    preview = await db.preview_next_session(user_id, counter, cefr_levels=cefr)
    new_each = 20 if preview["allow_new"] else 0
    next_session = preview["total"] + new_each
    new_note = " (including new cards)" if new_each else ""
    await update.message.reply_text(
        "🧩 *Your backlog is on autopilot*\n\n"
        f"Reviews are served in bite-size sessions — up to {db.DAILY_REVIEW_CAP} at a "
        "time — so a large pile never lands all at once. Anything you don't reach "
        "simply waits for your next session.\n\n"
        f"Next session: *{next_session}* card{'s' if next_session != 1 else ''}"
        f"{new_note}.\n\n"
        "Just tap /grammar or /vocab whenever you're ready. 💪",
        parse_mode="Markdown",
    )


async def callback_spread_backlog(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Retired action. Backlog spreading is now automatic (every session is capped
    at db.DAILY_REVIEW_CAP reviews), so this no longer moves any cards. Kept only so
    the button on older messages still in a user's chat is handled gracefully."""
    query = update.callback_query
    await query.answer()
    if not await _is_authorized(update):
        return
    await query.edit_message_text(
        "🧩 Good news — backlog spreading is now automatic!\n\n"
        f"Every session is capped at {db.DAILY_REVIEW_CAP} reviews, so a big pile "
        "always comes back a little at a time. Nothing to do here — just tap "
        "/grammar or /vocab whenever you're ready. 💪"
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
