"""SQLAlchemy engine and transaction lifecycle helpers."""

from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from config import get_settings
from database.base import Base


def build_engine(database_url: str, *, shared_memory: bool = False) -> Engine:
    """Build an engine suitable for PostgreSQL or SQLite."""
    options: dict = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if shared_memory:
            options["poolclass"] = StaticPool

    engine = create_engine(database_url, **options)

    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@lru_cache(maxsize=4)
def _cached_engine(database_url: str) -> Engine:
    return build_engine(database_url)


def get_engine() -> Engine:
    return _cached_engine(get_settings().sqlalchemy_database_url())


def create_schema(engine: Engine | None = None) -> None:
    """Create tables for tests/local bootstrapping; deployments use Alembic."""
    Base.metadata.create_all(engine or get_engine())


@contextmanager
def session_scope(engine: Engine | None = None) -> Generator[Session, None, None]:
    """Commit successful work and roll back failed work."""
    session_factory = sessionmaker(
        bind=engine or get_engine(),
        autoflush=False,
        expire_on_commit=False,
    )
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that supplies one transaction per request."""
    with session_scope() as session:
        yield session
