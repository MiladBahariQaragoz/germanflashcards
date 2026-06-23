import asyncio
import logging
from telegram import BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler

from bot.config import BOT_TOKEN, AUTHORIZED_CHAT_ID
from bot.handlers import (
    cmd_start,
    cmd_session,
    cmd_vocab,
    cmd_grammar,
    cmd_stats,
    cmd_leaderboard,
    cmd_developer,
    cmd_settings,
    cmd_admin,
    callback_request_access,
    callback_approve_user,
    callback_deny_user,
    callback_admin_remove,
    callback_admin_remove_confirm,
    callback_admin_remove_cancel,
    callback_start_session,
    callback_start_grammar_session,
    callback_show_answer,
    callback_show_grammar_answer,
    callback_grade,
    callback_grade_grammar,
    callback_settings_direction,
    callback_settings_cefr,
    callback_spread_backlog,
)
from bot import db
from bot.version import get_version
from bot.scheduler import setup_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# httpx logs every request at INFO with the full Telegram API URL — and that URL
# embeds the bot token. Quiet it (and httpcore) so the token never lands in logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("session", cmd_session))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("developer", cmd_developer))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("grammar", cmd_grammar))
    app.add_handler(CommandHandler("vocab", cmd_vocab))
    app.add_handler(CommandHandler("admin", cmd_admin))  # admin-only, hidden from menu

    # Access request flow (new-user onboarding, admin-approved)
    app.add_handler(
        CallbackQueryHandler(callback_request_access, pattern="^request_access$")
    )
    app.add_handler(
        CallbackQueryHandler(callback_approve_user, pattern="^approve_user:")
    )
    app.add_handler(
        CallbackQueryHandler(callback_deny_user, pattern="^deny_user:")
    )

    # Admin user-removal flow (tap 🗑 in /admin → confirm → silent delete)
    app.add_handler(
        CallbackQueryHandler(callback_admin_remove, pattern="^admin_rm:")
    )
    app.add_handler(
        CallbackQueryHandler(callback_admin_remove_confirm, pattern="^admin_rmok:")
    )
    app.add_handler(
        CallbackQueryHandler(callback_admin_remove_cancel, pattern="^admin_rmno$")
    )

    # Session callbacks
    app.add_handler(
        CallbackQueryHandler(callback_start_session, pattern="^start_session$")
    )
    app.add_handler(
        CallbackQueryHandler(callback_start_grammar_session, pattern="^start_grammar_session$")
    )
    app.add_handler(
        CallbackQueryHandler(callback_show_answer, pattern="^show_answer:")
    )
    app.add_handler(
        CallbackQueryHandler(callback_show_grammar_answer, pattern="^show_grammar:")
    )
    app.add_handler(CallbackQueryHandler(callback_grade, pattern="^grade:"))
    app.add_handler(CallbackQueryHandler(callback_grade_grammar, pattern="^grade_grammar:"))
    app.add_handler(
        CallbackQueryHandler(callback_spread_backlog, pattern="^spread_backlog$")
    )

    # Settings callbacks
    app.add_handler(
        CallbackQueryHandler(callback_settings_direction, pattern="^settings_direction$")
    )
    app.add_handler(
        CallbackQueryHandler(callback_settings_cefr, pattern="^settings_cefr:")
    )

    # Bot command menu shown to everyone. /admin is intentionally omitted — it's
    # admin-only (guarded in the handler) and stays hidden from the menu.
    # Ping the admin once per new deploy. Deploy = git pull + restart, so we compare
    # the current commit against the last-announced one (stored in Firestore) and
    # only message when it changed — plain crash restarts (same commit) stay quiet.
    async def announce_deploy():
        version = get_version()
        try:
            last = await db.get_last_deploy_version()
            if version == last or version == "unknown":
                return
            await app.bot.send_message(
                chat_id=AUTHORIZED_CHAT_ID,
                text=f"🚀 New version deployed & running:\n`{version}`",
                parse_mode="Markdown",
            )
            await db.set_last_deploy_version(version)
        except Exception as e:
            logging.getLogger(__name__).warning("deploy announce failed: %s", e)

    async def set_commands():
        commands = [
            BotCommand("grammar", "📚 Start grammar session"),
            BotCommand("vocab", "▶️ Start vocabulary session"),
            BotCommand("stats", "📊 Grammar + vocabulary progress"),
            BotCommand("leaderboard", "🏆 Top streak holders"),
            BotCommand("settings", "⚙️ Study direction & CEFR levels"),
            BotCommand("developer", "👨‍💻 About the developer"),
            BotCommand("start", "ℹ️ About / request access"),
        ]
        await app.bot.set_my_commands(commands)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(set_commands())
    loop.run_until_complete(announce_deploy())

    scheduler = setup_scheduler(app.bot)
    scheduler.start()

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
