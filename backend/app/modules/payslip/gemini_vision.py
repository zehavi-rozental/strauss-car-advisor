"""מתאם Gemini vision לחילוץ תלוש: POST .../models/{model}:generateContent עם inlineData.

נפרד ממתאם השיחה (conversation/llm/gemini.py): שם טקסט+כלים, כאן קובץ (base64) + בקשת JSON.
הקובץ נשלח inline ל-Google ולא נשמר בשרת. בלוג נרשם רק סוג השגיאה — אף פעם לא תוכן התלוש.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiVisionError(Exception):
    """הספק לא זמין / החזיר תשובה לא תקינה. ה-extract עוטף אותה למילוי ידני."""


class GeminiVisionAdapter:
    """מודל ראייה לחילוץ בלבד. temperature נמוך ו-responseMimeType=application/json כדי לקבל JSON יציב."""

    def __init__(self, model: str, api_key: str, base_url: str = "", timeout_s: float = 30.0,
                 client: Optional[httpx.Client] = None):
        self.model = model
        self.url = f"{(base_url or BASE_URL).rstrip('/')}/models/{model}:generateContent"
        self.headers = {"x-goog-api-key": api_key}
        self.timeout_s = timeout_s
        self.client = client or httpx.Client(timeout=timeout_s)

    def extract(self, data: bytes, mime: str, prompt: str, timeout_s: float) -> str:
        body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}},
                ],
            }],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
        }
        try:
            r = self.client.post(self.url, json=body, headers=self.headers,
                                 timeout=min(self.timeout_s, timeout_s) if timeout_s else self.timeout_s)
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"].get("parts", [])
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise GeminiVisionError(type(exc).__name__) from exc
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))
