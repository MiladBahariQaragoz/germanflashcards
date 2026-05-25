import logging
from telegram import BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler

from bot.config import BOT_TOKEN
from bot.handlers import (
    cmd_start,
    cmd_session,
    cmd_stats,
    cmd_create_invite,
    cmd_login,
    cmd_settings,
    callback_start_session,
    callback_show_answer,
    callback_grade,
    callback_settings_direction,
    callback_settings_cefr,
)
from bot.scheduler import setup_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("session", cmd_session))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("create_invite", cmd_create_invite))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("settings", cmd_settings))

    # Session callbacks
    app.add_handler(
        CallbackQueryHandler(callback_start_session, pattern="^start_session$")
    )
    app.add_handler(
        CallbackQueryHandler(callback_show_answer, pattern="^show_answer:")
    )
    app.add_handler(CallbackQueryHandler(callback_grade, pattern="^grade:"))

    # Settings callbacks
    app.add_handler(
        CallbackQueryHandler(
            callback_settings_direction, pattern="^settings_direction$"
        )
    )
    app.add_handler(
        CallbackQueryHandler(callback_settings_cefr, pattern="^settings_cefr:")
    )

    # Register bot command menu (shown in Telegram's "/" menu)
    import asyncio
    async def set_commands():
        await app.bot.set_my_commands([
            BotCommand("session", "Start a study session"),
            BotCommand("stats", "Show your card counts"),
            BotCommand("settings", "Change study direction & CEFR levels"),
            BotCommand("create_invite", "Generate an invite code (admin only)"),
            BotCommand("login", "Register with an invite code"),
            BotCommand("start", "Welcome message"),
        ])
    asyncio.get_event_loop().run_until_complete(set_commands())

    scheduler = setup_scheduler(app.bot)
    scheduler.start()

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
