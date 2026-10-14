"""סביבת alembic. כתובת המסד: sqlalchemy.url אם הוגדר (בבדיקות), אחרת DATABASE_URL."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context

import app.modules.cases.models  # noqa: F401  (רישום הטבלאות)
import app.modules.conversation.models  # noqa: F401
import app.modules.vehicles.models  # noqa: F401
from app.shared.db import Base, make_engine
from app.shared.settings import get_settings

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
