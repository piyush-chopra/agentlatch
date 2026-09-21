"""Small SQL compatibility boundary; coordinator transactions serialize all managed writers."""

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Cursor:
    def __init__(self, cursor, lastrowid=None):
        self.cursor = cursor
        self.lastrowid = lastrowid
        self.rowcount = cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        return Row(row) if row is not None else None

    def fetchall(self):
        return [Row(r) for r in self.cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class PostgresConnection:
    def __init__(self, connection):
        self.raw = connection

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        returning = None
        for table, column in [
            ("lease_sequence", "token"),
            ("attempts", "id"),
            ("events", "sequence"),
        ]:
            if re.match(rf"INSERT INTO {table}\b", sql, re.I):
                returning = column
                sql += f" RETURNING {column}"
                break
        cursor = self.raw.execute(sql, params)
        lastrowid = cursor.fetchone()[returning] if returning else None
        return Cursor(cursor, lastrowid)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()


class Storage:
    def __init__(self, path):
        self.path = str(path)
        self.postgres = self.path.startswith(("postgresql://", "postgres://"))
        if not self.postgres:
            if self.path == ":memory:":
                raise ValueError("Use a file-backed SQLite database")
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self):
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(
                self.path, autocommit=True, row_factory=dict_row, connect_timeout=5
            ) as raw:
                raw.execute("SET statement_timeout = '10s'")
                raw.execute("SET lock_timeout = '10s'")
                yield PostgresConnection(raw)
        else:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA synchronous=FULL")
            try:
                yield db
            finally:
                db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN" if self.postgres else "BEGIN IMMEDIATE")
            try:
                if self.postgres:
                    # Shared by ALL mutations, including queue claims and schema setup.
                    db.execute("SELECT pg_advisory_xact_lock(714092831)")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def initialize(self, ddl):
        if not self.postgres:
            with self.connection() as db:
                db.execute("PRAGMA journal_mode=WAL")
        ddl = (
            ddl.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
            if self.postgres
            else ddl
        )
        if self.postgres:
            ddl = re.sub(r"\bREAL\b", "DOUBLE PRECISION", ddl)
        with self.transaction() as db:
            for statement in ddl.split(";"):
                if statement.strip():
                    db.execute(statement)

    @staticmethod
    def now(db):
        if isinstance(db, PostgresConnection):
            return float(
                db.execute("SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS now").fetchone()[0]
            )
        import time

        return time.time()
