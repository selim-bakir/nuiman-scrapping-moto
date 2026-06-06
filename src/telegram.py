"""Envoi du rapport sur Telegram via la Bot API."""

from __future__ import annotations

import requests

API_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


def send_message(token: str, chat_id: str, text: str, *, parse_mode: str = "HTML") -> None:
    """Envoie un message texte. Lève ``TelegramError`` en cas d'échec."""
    url = f"{API_BASE}/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise TelegramError(
            f"Échec envoi Telegram ({resp.status_code}) : {resp.text}"
        )


def send_report(token: str, chat_id: str, chunks: list[str]) -> None:
    """Envoie le rapport découpé en plusieurs messages si nécessaire."""
    for chunk in chunks:
        send_message(token, chat_id, chunk)
