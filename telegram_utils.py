"""Tiny Telegram helper functions with a no-key console fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{TOKEN}/{method}"


async def send_telegram_text(chat_id: str | None, text: str) -> None:
    """Send a Telegram text message or print it when Telegram is not configured."""
    if not TOKEN or not chat_id:
        print(f"[TELEGRAM TEXT fallback] chat={chat_id}: {text}")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(_api("sendMessage"), data={"chat_id": chat_id, "text": text})
    except Exception as exc:
        print(f"Telegram text failed: {exc}")


async def send_telegram_photo(chat_id: str | None, image_path: str | None, caption: str, buttons=None) -> None:
    """Send a photo with optional inline URL buttons; fall back to text safely."""
    if not TOKEN or not chat_id or not image_path or not Path(image_path).exists():
        print(f"[TELEGRAM PHOTO fallback] chat={chat_id}: {caption} buttons={buttons}")
        return
    data = {"chat_id": chat_id, "caption": caption}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": [buttons]})
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            with open(image_path, "rb") as photo:
                await client.post(_api("sendPhoto"), data=data, files={"photo": photo})
    except Exception as exc:
        print(f"Telegram photo failed: {exc}")
