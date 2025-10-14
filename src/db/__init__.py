import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


def _default_database_url() -> str:
    """Build the default SQLite URL under ./data/payments.db."""
    db_dir = Path(__file__).resolve().parents[1] / "data"
    return f"sqlite:///{db_dir / 'payments.db'}"


def _prepare_sqlite_path(db_url: str) -> str:
    """Ensure the backing folder exists for SQLite URLs."""
    if db_url.startswith("sqlite:///"):
        raw_path = db_url.replace("sqlite:///", "", 1)
        db_path = Path(raw_path)
        if not db_path.parent.exists():
            try:
                db_path.parent.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                # Fallback to in-memory SQLite if filesystem is read-only.
                return "sqlite://"
    return db_url


_ENGINE = None
_SESSION_FACTORY: Optional[sessionmaker] = None


def get_engine(database_url: Optional[str] = None):
    """Create (or reuse) the SQLAlchemy engine for the application."""
    global _ENGINE
    if _ENGINE is not None and database_url is None:
        return _ENGINE

    db_url = database_url or os.getenv("DATABASE_URL") or _default_database_url()
    db_url = _prepare_sqlite_path(db_url)
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, future=True, echo=False, connect_args=connect_args)

    if database_url is None:
        _ENGINE = engine

    return engine


def get_session_factory(engine=None):
    global _SESSION_FACTORY
    if _SESSION_FACTORY is not None and engine is None:
        return _SESSION_FACTORY

    engine = engine or get_engine()
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )

    if engine is _ENGINE:
        _SESSION_FACTORY = session_factory

    return session_factory


def get_session(engine=None):
    factory = get_session_factory(engine=engine)
    return factory()


def init_db(engine=None):
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    _ensure_additional_columns(engine)
    return engine


@contextmanager
def session_scope(engine=None):
    session = get_session(engine=engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ensure_additional_columns(engine):
    """Add newly introduced columns when running against existing SQLite DBs."""
    inspector = inspect(engine)
    if "transactions" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("transactions")}
    desired_columns = {
        "customer_email": "TEXT",
        "country": "TEXT",
        "city": "TEXT",
    }

    missing = {col: col_type for col, col_type in desired_columns.items() if col not in existing_columns}
    if not missing:
        return

    dialect = engine.dialect.name

    with engine.begin() as conn:
        for column, column_type in missing.items():
            if dialect != "sqlite":
                if column == "customer_email":
                    column_type = "VARCHAR(255)"
                else:
                    column_type = "VARCHAR(128)"
            conn.execute(
                text(f"ALTER TABLE transactions ADD COLUMN {column} {column_type}")
            )
