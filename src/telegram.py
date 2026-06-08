"""Envoi du rapport sur Telegram via la Bot API (messages texte + photos)."""

from __future__ import annotations

import time

import requests

API_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


def _post(token: str, method: str, payload: dict, *, max_retries: int = 4) -> dict:
    """POST vers l'API Telegram avec gestion du rate-limit (429 retry_after)."""
    url = f"{API_BASE}/bot{token}/{method}"
    for attempt in range(max_retries + 1):
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            retry_after = 1
            try:
                retry_after = int(resp.json().get("parameters", {}).get("retry_after", 1))
            except Exception:
                pass
            time.sleep(retry_after + 1)
            continue
        raise TelegramError(f"Échec {method} ({resp.status_code}) : {resp.text}")
    raise TelegramError(f"Échec {method} : rate-limit persistant après {max_retries} essais")


def send_message(token: str, chat_id: str, text: str, *, parse_mode: str = "HTML") -> None:
    _post(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        },
    )


def send_photo(
    token: str, chat_id: str, photo: str, caption: str, *, parse_mode: str = "HTML"
) -> None:
    _post(
        token,
        "sendPhoto",
        {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": parse_mode,
        },
    )


def send_document(
    token: str, chat_id: str, file_path: str, *, caption: str | None = None
) -> None:
    """Envoie un fichier (ex: rapport .xlsx) en pièce jointe."""
    url = f"{API_BASE}/bot{token}/sendDocument"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"
    for attempt in range(5):
        with open(file_path, "rb") as fh:
            resp = requests.post(url, data=data, files={"document": fh}, timeout=60)
        if resp.status_code == 200:
            return
        if resp.status_code == 429:
            retry_after = 1
            try:
                retry_after = int(resp.json().get("parameters", {}).get("retry_after", 1))
            except Exception:
                pass
            time.sleep(retry_after + 1)
            continue
        raise TelegramError(f"Échec sendDocument ({resp.status_code}) : {resp.text}")
    raise TelegramError("Échec sendDocument : rate-limit persistant")


def send_report(token: str, chat_id: str, chunks: list[str]) -> None:
    """Envoie le rapport texte découpé en plusieurs messages si nécessaire."""
    for chunk in chunks:
        send_message(token, chat_id, chunk)
