"""A recorded vision model for end-to-end tests: the answer for a file is read from <dir>/<sha256 of the file>.json.

Turned on only by PAYSLIP_FAKE_VISION_DIR (never set in a deployment); the server logs a warning when it is.
It lets a browser test drive the real server path (upload, validation, parsing, review, the conversation)
with fixed answers and no paid call. It is not a test of Gemini itself: the live check is `pytest -m eval`.

A file's JSON is the model's answer as text, or {"__error__": "<reason>"} to simulate a provider failure
(timeout / quota / unavailable / rejected / invalid_response). An unknown file is a "rejected" failure.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .extract import VisionError


class FixtureVision:
    def __init__(self, directory: str):
        self.directory = Path(directory)

    def extract(self, data: bytes, mime: str, prompt: str, timeout_s: float) -> str:
        path = self.directory / f"{hashlib.sha256(data).hexdigest()}.json"
        if not path.is_file():
            raise VisionError("rejected", "no recorded answer for this file")
        text = path.read_text(encoding="utf-8")
        try:
            recorded = json.loads(text)
        except ValueError:
            return text                       # a recorded answer that is not JSON (an invalid response)
        if isinstance(recorded, dict) and "__error__" in recorded:
            raise VisionError(str(recorded["__error__"]), "recorded failure")
        return text
