import asyncio
import logging
from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
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
    cmd_create_invite,
    cmd_login,
    cmd_settings,
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
    app.add_handler(CommandHandler("create_invite", cmd_create_invite))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("grammar", cmd_grammar))
    app.add_handler(CommandHandler("vocab", cmd_vocab))

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

    # Bot command menus — admin sees /create_invite, regular users don't
    async def set_commands():
        user_commands = [
            BotCommand("grammar", "📚 Start grammar session"),
            BotCommand("vocab", "▶️ Start vocabulary session"),
            BotCommand("stats", "📊 Grammar + vocabulary progress"),
            BotCommand("leaderboard", "🏆 Top streak holders"),
            BotCommand("settings", "⚙️ Study direction & CEFR levels"),
            BotCommand("developer", "👨‍💻 About the developer"),
            BotCommand("login", "🔑 Register with an invite code"),
            BotCommand("start", "ℹ️ About this bot"),
        ]
        admin_commands = user_commands + [
            BotCommand("create_invite", "➕ Generate an invite code"),
        ]
        # Default menu for all users
        await app.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
        # Override for admin chat — adds /create_invite
        await app.bot.set_my_commands(
            admin_commands, scope=BotCommandScopeChat(chat_id=AUTHORIZED_CHAT_ID)
        )

    asyncio.get_event_loop().run_until_complete(set_commands())

    scheduler = setup_scheduler(app.bot)
    scheduler.start()

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
