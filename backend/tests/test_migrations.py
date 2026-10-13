"""המיגרציות יוצרות בדיוק את מה שהמודלים מגדירים (בלי סטייה)."""
from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

import app.modules.conversation.models  # noqa: F401
import app.modules.vehicles.models  # noqa: F401
from app.shared.db import Base, make_engine

from .conftest import BACKEND


def test_upgrade_head_matches_models(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    engine = make_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []

    command.downgrade(cfg, "base")
