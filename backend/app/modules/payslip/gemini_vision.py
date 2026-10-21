"""מתאם Gemini vision לחילוץ תלוש: POST .../models/{model}:generateContent עם inlineData.

נפרד ממתאם השיחה (conversation/llm/gemini.py): שם טקסט+כלים, כאן קובץ (base64) + תשובת JSON במבנה קבוע
(responseSchema). הקובץ נשלח inline ל-Google ולא נשמר בשרת. בשגיאה נרשמים רק הסיבה וקוד ה-HTTP —
אף פעם לא תוכן התלוש, התשובה או המפתח.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

from .extract import RESPONSE_SCHEMA, VisionError

log = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiVisionError(VisionError):
    """הספק לא זמין / החזיר תשובה לא תקינה. reason: timeout / quota / unavailable / rejected /
    invalid_response / not_configured — each has its own message to the employee."""


class GeminiVisionAdapter:
    """מודל ראייה לחילוץ בלבד. temperature 0 ו-responseSchema כדי לקבל JSON במבנה קבוע."""

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
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json",
                                 "responseSchema": RESPONSE_SCHEMA},
        }
        try:
            r = self.client.post(self.url, json=body, headers=self.headers,
                                 timeout=min(self.timeout_s, timeout_s) if timeout_s else self.timeout_s)
            r.raise_for_status()
            payload = r.json()
        except httpx.TimeoutException as exc:
            raise GeminiVisionError("timeout") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise GeminiVisionError(_reason(status, exc.response), f"HTTP {status} {_provider_message(exc.response)}") \
                from exc
        except httpx.HTTPError as exc:
            raise GeminiVisionError("unavailable", type(exc).__name__) from exc
        except ValueError as exc:
            raise GeminiVisionError("invalid_response", "the response is not JSON") from exc
        try:
            candidate = payload["candidates"][0]
            parts = candidate["content"].get("parts", [])
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            # no candidate: the request was blocked (promptFeedback.blockReason)
            block = (payload.get("promptFeedback") or {}).get("blockReason", "") if isinstance(payload, dict) else ""
            raise GeminiVisionError("rejected", f"no candidate {block}".strip()) from exc
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if not text:
            raise GeminiVisionError("invalid_response", f"empty answer ({candidate.get('finishReason', '')})")
        return text


def _reason(status: int, response: httpx.Response) -> str:
    if status == 429:
        return "quota"
    if status >= 500:
        return "unavailable"
    message = _provider_message(response)
    if status in (401, 403) or "API key" in message or "PERMISSION_DENIED" in message:
        return "not_configured"         # a key / project problem: trying again will not help
    return "rejected"


def _provider_message(response: httpx.Response) -> str:
    """Google's {"error": {"status", "message"}} describes the request (bad key, quota, size), never the file."""
    try:
        error = response.json()["error"]
        return f"{error.get('status', '')} {str(error.get('message', ''))[:200]}".strip()
    except Exception:
        return ""
