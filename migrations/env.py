"""Alembic env. Async SQLAlchemy."""
from alembic import context

config = context.config


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # async engine + connection wiring
    ...


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
