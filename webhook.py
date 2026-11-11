"""FastAPI webhook entry point for a free Render Web Service."""
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from telegram import Update
from telegram.ext import Application

from bot import TOKEN, start, help_cmd, index_cmd, text_handler
from telegram.ext import CommandHandler, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jio-flexi-webhook")

telegram_app = Application.builder().token(TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start, block=False))
telegram_app.add_handler(CommandHandler("help", help_cmd, block=False))
telegram_app.add_handler(CommandHandler("index", index_cmd, block=False))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler, block=False))

webhook_secret = secrets.token_urlsafe(32)


@asynccontextmanager
async def lifespan(app: FastAPI):
    external_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not external_url:
        raise RuntimeError("RENDER_EXTERNAL_URL is missing. Deploy this app as a Render Web Service.")

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.bot.set_webhook(
        url=f"{external_url}/telegram-webhook",
        secret_token=webhook_secret,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )
    log.info("Telegram webhook configured at %s/telegram-webhook", external_url)
    try:
        yield
    finally:
        await telegram_app.bot.delete_webhook()
        await telegram_app.stop()
        await telegram_app.shutdown()


app = FastAPI(title="Jio Flexi Portfolio Tracker", lifespan=lifespan)


@app.get("/")
async def health():
    return {"status": "ok", "service": "Jio Flexi Portfolio Tracker"}


@app.post("/telegram-webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not x_telegram_bot_api_secret_token or not secrets.compare_digest(
        x_telegram_bot_api_secret_token, webhook_secret
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        payload = await request.json()
        update = Update.de_json(payload, telegram_app.bot)
        await telegram_app.process_update(update)
    except Exception:
        log.exception("Failed to process Telegram update")
        raise HTTPException(status_code=500, detail="Update processing failed")
    return {"ok": True}
