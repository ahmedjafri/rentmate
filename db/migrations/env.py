import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

from db.models import Base  # <-- your SQLAlchemy models Base

# If you use a .env file, uncomment these two lines:
# from dotenv import load_dotenv
# load_dotenv()

# Alembic Config object
config = context.config

# Override sqlalchemy.url from environment if set
db_uri = os.getenv("DATABASE_URL")
if db_uri:
    config.set_main_option("sqlalchemy.url", db_uri)
else:
    # Fallback: use the SQLite path from db/session.py
    from db.session import DB_PATH
    config.set_main_option("sqlalchemy.url", f"sqlite:///{DB_PATH}")

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use your models' metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
