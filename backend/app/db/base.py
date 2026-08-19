"""SQLite persistence layer (SQLAlchemy 2.0).

One local database under <repo>/data holds everything the "intelligent" studio learns:
datasets and fields across every region, their cross-region similarity, per-user usage,
saved research sessions and prompts, and provenance. `init_db()` creates the schema on
first run; there is no separate migration step for the local single-file DB.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings

_settings = get_settings()

# check_same_thread=False: FastAPI serves requests from a threadpool; SQLite is fine with
# short-lived sessions per request.
engine = create_engine(
    f"sqlite:///{_settings.db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)
with engine.begin() as _conn:
    _conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    _conn.exec_driver_sql("PRAGMA busy_timeout=10000")
    _conn.exec_driver_sql("PRAGMA foreign_keys=ON")
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app.db import models  # noqa: F401  (register mappers)
    Base.metadata.create_all(engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """Additive local migration for existing ACE databases. This is intentionally idempotent: new
    columns are added without destroying accumulated research data. A full Alembic history can be
    introduced later, but startup must remain zero-touch for a local-first application."""
    from sqlalchemy import inspect, text
    wanted = {
        "sim_results": [("margin", "FLOAT"), ("drawdown", "FLOAT"), ("execution_key", "VARCHAR(128)"), ("variant_id", "INTEGER"), ("execution_config_json", "TEXT"), ("experiment_id", "INTEGER")],
        "alpha_variants": [("execution_key", "VARCHAR(128)")],
        "submission_records": [("sim_result_id", "INTEGER"), ("variant_id", "INTEGER"), ("execution_key", "VARCHAR(128)")],
    }
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, sqltype in cols:
                if name not in have:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sqltype}'))
        # Older builds had UNIQUE(field_id, region, delay), which prevents the same BRAIN
        # field id from being catalogued under multiple datasets. Rebuild once to preserve the
        # intended composite identity (field_id, dataset_id, region, delay).
        if insp.has_table("fields"):
            idx_rows = conn.exec_driver_sql("PRAGMA index_list(fields)").fetchall()
            old_field_unique = False
            for r in idx_rows:
                if len(r) < 3 or not bool(r[2]):
                    continue
                idx_name = str(r[1])
                cols = [str(x[2]) for x in conn.exec_driver_sql(f'PRAGMA index_info("{idx_name}")').fetchall()]
                if cols == ["field_id", "region", "delay"]:
                    old_field_unique = True
                    break
            if old_field_unique:
                conn.exec_driver_sql("ALTER TABLE fields RENAME TO fields_old")
                conn.exec_driver_sql("""CREATE TABLE fields (
                    id INTEGER PRIMARY KEY, field_id VARCHAR, dataset_id VARCHAR, region VARCHAR, delay INTEGER,
                    type VARCHAR DEFAULT 'MATRIX', prefix VARCHAR DEFAULT '', description TEXT DEFAULT '',
                    alpha_count INTEGER DEFAULT 0, is_virgin BOOLEAN DEFAULT 0, first_seen FLOAT, last_seen FLOAT,
                    CONSTRAINT uq_field_dataset_region_delay UNIQUE(field_id, dataset_id, region, delay)
                )""")
                conn.exec_driver_sql("""INSERT INTO fields
                    (id,field_id,dataset_id,region,delay,type,prefix,description,alpha_count,is_virgin,first_seen,last_seen)
                    SELECT id,field_id,dataset_id,region,delay,type,prefix,description,alpha_count,is_virgin,first_seen,last_seen
                    FROM fields_old""")
                conn.exec_driver_sql("DROP TABLE fields_old")

        # Older builds had UNIQUE(alpha_id), which prevents the same alpha from having
        # multiple independently-tested simulation configurations. Rebuild once to remove it.
        if insp.has_table("submission_records"):
            idx_rows = conn.exec_driver_sql("PRAGMA index_list(submission_records)").fetchall()
            unique_alpha = any((len(r) > 2 and bool(r[2]) and str(r[1]) == "sqlite_autoindex_submission_records_1") for r in idx_rows)
            if unique_alpha:
                conn.exec_driver_sql("ALTER TABLE submission_records RENAME TO submission_records_old")
                conn.exec_driver_sql("""CREATE TABLE submission_records (
                    id INTEGER PRIMARY KEY, created_at FLOAT, updated_at FLOAT, alpha_id VARCHAR,
                    sim_result_id INTEGER DEFAULT 0, variant_id INTEGER DEFAULT 0, execution_key VARCHAR DEFAULT '',
                    expression TEXT DEFAULT '', region VARCHAR DEFAULT '', delay INTEGER DEFAULT 1, universe VARCHAR DEFAULT '',
                    neutralization VARCHAR DEFAULT '', sharpe FLOAT, fitness FLOAT, turnover FLOAT, novelty FLOAT,
                    robustness FLOAT, prod_corr FLOAT, status VARCHAR DEFAULT 'queued', queued_for VARCHAR DEFAULT '',
                    submitted_at FLOAT, error TEXT DEFAULT '', notes TEXT DEFAULT ''
                )""")
                conn.exec_driver_sql("""INSERT INTO submission_records
                    (id,created_at,updated_at,alpha_id,expression,region,delay,universe,neutralization,sharpe,fitness,turnover,novelty,robustness,prod_corr,status,queued_for,submitted_at,error,notes)
                    SELECT id,created_at,updated_at,alpha_id,expression,region,delay,universe,neutralization,sharpe,fitness,turnover,novelty,robustness,prod_corr,status,queued_for,submitted_at,error,notes
                    FROM submission_records_old""")
                conn.exec_driver_sql("DROP TABLE submission_records_old")


def get_db():
    """FastAPI dependency: a request-scoped session that always closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
