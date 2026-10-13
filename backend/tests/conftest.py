from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.rules.public import load_snapshot

BACKEND = Path(__file__).resolve().parents[1]
CONFIG = BACKEND / "config"


@pytest.fixture(scope="session")
def snapshot():
    return load_snapshot(CONFIG)


@pytest.fixture(scope="session")
def rules(snapshot):
    return snapshot.calc
