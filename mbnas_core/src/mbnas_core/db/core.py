"""SQLite connection setup and transactions.

Every connection uses WAL (readers never block the writer), ``synchronous=NORMAL``, ``foreign_keys=ON`` and a 10 s
``busy_timeout``. Connections run in autocommit mode (``isolation_level=None``) so transactions are explicit:
``with tx(conn): ...`` issues ``BEGIN IMMEDIATE`` (takes the write lock up front, so two writer processes serialise
instead of failing on lock upgrade), commits on success and rolls back on any exception. A nested ``tx`` becomes
a SAVEPOINT, so repository helpers compose inside a caller's transaction.
"""
from __future__ import annotations

import itertools
import os
import re
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources

from mbnas_core.config import Settings

SCHEMA_VERSION = "6"
# additive v1 -> v2 migration: (table, column, column definition); applied when the column is missing
MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("runs", "kind", "TEXT NOT NULL DEFAULT 'SEARCH' CHECK (kind IN ('SEARCH', 'FINAL_RETRAIN'))"),
    ("runs", "parent_run_id", "TEXT REFERENCES runs(id)"),
    ("runs", "finalists_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("checkpoints", "state", "TEXT NOT NULL DEFAULT 'PRESENT' CHECK (state IN ('PRESENT', 'PRUNED'))"),
    ("checkpoints", "pruned_at", "TEXT"),
    ("checkpoints", "prune_json", "TEXT"),
)
MIGRATION_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS runs_parent ON runs(parent_run_id)",
)
# v2 -> v3: events.type CHECK gains 'generation.updated' (contract 1.2.9). SQLite cannot alter a CHECK, so
# the table is rebuilt (copy every row into the new definition, verify, swap) when its stored DDL lacks a type.
EVENT_TYPES_REQUIRED: tuple[str, ...] = ("generation.updated",)
# v3 -> v4 (remote runner protocol): new tables only (no ``public_ids``). Their DDL
# lives once in schema.sql; :func:`migrate` creates a missing one (and its indexes) from there.
V4_TABLES: tuple[str, ...] = ("runners", "principals", "idempotent_responses", "artifact_locations")
# v4 -> v5: the configuration revision table and the per-database salt of the public opaque ids (device /
# worker ids of the console projection); both additive, created when missing.
V5_TABLES: tuple[str, ...] = ("config_revisions",)
# v5 -> v6: the retention prune round trip of runner-held checkpoints;
# additive, created when missing.
V6_TABLES: tuple[str, ...] = ("prune_requests",)
PUBLIC_ID_SALT_KEY = "public_id_salt"
BUSY_TIMEOUT_MS = 10_000
_savepoints = itertools.count(1)


def schema_sql() -> str:
    """Text of the bundled ``schema.sql``."""
    return resources.files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables/indexes/triggers if missing, then :func:`migrate` (idempotent, atomic)."""
    conn.executescript("BEGIN IMMEDIATE;\n" + schema_sql() + "\nCOMMIT;")
    migrate(conn)


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Forward migration of an older DB to :data:`SCHEMA_VERSION` (no data rewritten or dropped).

    v1 -> v2 adds columns; v2 -> v3 widens the ``events.type`` CHECK by rebuilding ``events`` with every row copied
    (:func:`_rebuild_events_if_needed`); v3 -> v4 creates the remote-runner tables :data:`V4_TABLES` (``table:<name>``);
    v4 -> v5 creates :data:`V5_TABLES` and the ``meta`` salt :data:`PUBLIC_ID_SALT_KEY` (``meta:<key>``); v5 -> v6 creates
    :data:`V6_TABLES` (``prune_requests``). Runs in one ``BEGIN IMMEDIATE`` transaction, so the API and the worker
    starting together serialise and a failure leaves the old DB untouched; returns what it changed
    (``table.column`` names, ``events.type:<type>``, ``table:<name>``).
    """
    added: list[str] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, column, ddl in MIGRATION_COLUMNS:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                added.append(f"{table}.{column}")
        for stmt in MIGRATION_INDEXES:
            conn.execute(stmt)
        if _rebuild_events_if_needed(conn):
            added.append("events.type:generation.updated")
        added.extend(_create_tables(conn, V4_TABLES))
        added.extend(_create_tables(conn, V5_TABLES))
        added.extend(_create_tables(conn, V6_TABLES))
        if conn.execute("SELECT 1 FROM meta WHERE key = ?", (PUBLIC_ID_SALT_KEY,)).fetchone() is None:
            conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (PUBLIC_ID_SALT_KEY, secrets.token_hex(32)))
            added.append(f"meta:{PUBLIC_ID_SALT_KEY}")
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (SCHEMA_VERSION,))
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return added


def _table_ddl(name: str) -> tuple[str, list[str]]:
    """The bundled ``CREATE TABLE IF NOT EXISTS <name> (...);`` statement and its ``CREATE INDEX`` statements."""
    text = schema_sql()
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {name} \(.*?\n\);", text, re.S)
    if m is None:
        raise RuntimeError(f"schema.sql has no {name} table")
    indexes = re.findall(rf"CREATE INDEX IF NOT EXISTS \w+ ON {name}\([^)]*\);", text)
    return m.group(0), indexes


def _create_tables(conn: sqlite3.Connection, names: tuple[str, ...]) -> list[str]:
    """Create the missing tables of ``names`` from schema.sql (inside the caller's transaction); ``table:<name>`` each."""
    made = []
    for name in names:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
        if exists is None:
            ddl, indexes = _table_ddl(name)
            conn.execute(ddl)
            for stmt in indexes:
                conn.execute(stmt)
            made.append(f"table:{name}")
    return made


def _create_v4_tables(conn: sqlite3.Connection) -> list[str]:
    """Create the missing v4 tables (inside the caller's transaction); returns ``table:<name>`` for each created."""
    return _create_tables(conn, V4_TABLES)


def public_id_salt(conn: sqlite3.Connection) -> str | None:
    """The per-database salt of the public opaque ids (``meta.public_id_salt``, created by :func:`migrate`)."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (PUBLIC_ID_SALT_KEY,)).fetchone()
    return None if row is None else str(row[0])


def _events_ddl() -> str:
    """The bundled ``CREATE TABLE IF NOT EXISTS events (...);`` statement (single source of truth: schema.sql)."""
    m = re.search(r"CREATE TABLE IF NOT EXISTS events \(.*?\n\);", schema_sql(), re.S)
    if m is None:
        raise RuntimeError("schema.sql has no events table")
    return m.group(0)


def _rebuild_events_if_needed(conn: sqlite3.Connection) -> bool:
    """Rebuild ``events`` when its stored CHECK lacks a required type; every row is copied and the count verified.

    Must run inside the caller's transaction (``migrate``). Nothing references ``events`` and it has no indexes or
    triggers of its own, so a copy + drop + rename keeps the table identical apart from the widened CHECK.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'events'").fetchone()
    if row is None or all(f"'{t}'" in row[0] for t in EVENT_TYPES_REQUIRED):
        return False
    cols = "run_id, seq, type, payload_json, created_at"
    conn.execute("DROP TABLE IF EXISTS events__v3")
    conn.execute(_events_ddl().replace("CREATE TABLE IF NOT EXISTS events (", "CREATE TABLE events__v3 (", 1))
    conn.execute(f"INSERT INTO events__v3 ({cols}) SELECT {cols} FROM events")
    old_n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    new_n = conn.execute("SELECT COUNT(*) FROM events__v3").fetchone()[0]
    if old_n != new_n:
        raise RuntimeError(f"events rebuild copied {new_n} of {old_n} rows")
    conn.execute("DROP TABLE events")
    conn.execute("ALTER TABLE events__v3 RENAME TO events")
    return True


def connect(settings: Settings | None = None, *, db_path: str | None = None, check_same_thread: bool = True,
            init: bool = True) -> sqlite3.Connection:
    """Open the server DB (``db_path`` or ``settings.db_path``) with the pragmas above; creates the parent dir."""
    path = db_path or (settings.db_path if settings is not None else None)
    if not path:
        raise ValueError("connect() needs settings or db_path")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=check_same_thread,
                           timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        conn.close()
        raise RuntimeError(f"could not enable WAL on {path} (journal_mode={mode})")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if init:
        init_schema(conn)
    return conn


@contextmanager
def tx(conn: sqlite3.Connection, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """Transaction (``BEGIN IMMEDIATE`` by default) or SAVEPOINT when already inside one."""
    if conn.in_transaction:
        name = f"sp_{next(_savepoints)}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
        return
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
