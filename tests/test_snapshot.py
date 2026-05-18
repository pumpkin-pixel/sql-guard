"""Tests for sql_guard.snapshot -- live introspection against sqlite.

These cover the introspect() and write_snapshot() helpers without
spinning up SQL Server. sqlite is enough to exercise every SQLAlchemy
inspector path the snapshot relies on: primary keys, NOT NULL, defaults,
foreign keys, schema filters, and the include-tables allowlist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

sqlalchemy = pytest.importorskip("sqlalchemy")

from sql_guard.snapshot import (  # noqa: E402  -- after importorskip
    introspect,
    write_snapshot,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_sample_db(dsn: str) -> None:
    """Build a small schema covering every column-shape branch in snapshot.py."""
    engine = sqlalchemy.create_engine(dsn)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE customers (
                id      INTEGER PRIMARY KEY,
                name    TEXT NOT NULL,
                email   TEXT
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE orders (
                id           INTEGER PRIMARY KEY,
                customer_id  INTEGER NOT NULL REFERENCES customers(id),
                total        REAL    NOT NULL,
                status       TEXT    DEFAULT 'pending',
                created_at   TEXT    NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE audit_log (
                id   INTEGER PRIMARY KEY,
                note TEXT
            )
            """
        )
    engine.dispose()


@pytest.fixture
def sqlite_dsn(tmp_path: Path) -> str:
    db_path = tmp_path / "snapshot.db"
    dsn = f"sqlite:///{db_path}"
    _build_sample_db(dsn)
    return dsn


# ---------------------------------------------------------------------------
# introspect()
# ---------------------------------------------------------------------------


class TestIntrospect:
    def test_returns_all_tables(self, sqlite_dsn: str) -> None:
        snapshot = introspect(sqlite_dsn)
        assert set(snapshot["tables"]) == {"customers", "orders", "audit_log"}

    def test_marks_primary_key(self, sqlite_dsn: str) -> None:
        snapshot = introspect(sqlite_dsn)
        assert snapshot["tables"]["customers"]["columns"]["id"]["primary_key"] is True

    def test_marks_not_null(self, sqlite_dsn: str) -> None:
        snapshot = introspect(sqlite_dsn)
        assert snapshot["tables"]["customers"]["columns"]["name"]["not_null"] is True

    def test_nullable_columns_have_no_not_null_flag(self, sqlite_dsn: str) -> None:
        snapshot = introspect(sqlite_dsn)
        email_col = snapshot["tables"]["customers"]["columns"]["email"]
        assert "not_null" not in email_col

    def test_marks_default(self, sqlite_dsn: str) -> None:
        snapshot = introspect(sqlite_dsn)
        status = snapshot["tables"]["orders"]["columns"]["status"]
        assert status["has_default"] is True

    def test_marks_foreign_key(self, sqlite_dsn: str) -> None:
        snapshot = introspect(sqlite_dsn)
        customer_id = snapshot["tables"]["orders"]["columns"]["customer_id"]
        assert customer_id["foreign_key"] == "customers.id"

    def test_include_tables_filter(self, sqlite_dsn: str) -> None:
        snapshot = introspect(sqlite_dsn, include_tables=["orders"])
        assert list(snapshot["tables"]) == ["orders"]

    def test_include_tables_case_insensitive(self, sqlite_dsn: str) -> None:
        snapshot = introspect(sqlite_dsn, include_tables=["ORDERS", "CUSTOMERS"])
        assert set(snapshot["tables"]) == {"orders", "customers"}

    def test_include_tables_unknown_returns_empty(self, sqlite_dsn: str) -> None:
        snapshot = introspect(sqlite_dsn, include_tables=["does_not_exist"])
        assert snapshot["tables"] == {}

    def test_column_type_is_a_string(self, sqlite_dsn: str) -> None:
        """The contract format wants types as bare strings, not SA Type objects."""
        snapshot = introspect(sqlite_dsn)
        for table in snapshot["tables"].values():
            for col in table["columns"].values():
                assert isinstance(col["type"], str)


# ---------------------------------------------------------------------------
# write_snapshot()
# ---------------------------------------------------------------------------


class TestWriteSnapshot:
    def test_writes_valid_yaml(self, tmp_path: Path) -> None:
        snap: dict[str, Any] = {
            "tables": {"t": {"columns": {"id": {"type": "INTEGER", "primary_key": True}}}}
        }
        out = tmp_path / "contract.yml"
        write_snapshot(snap, out)
        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert loaded == snap

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "subdir" / "contract.yml"
        write_snapshot({"tables": {}}, out)
        assert out.is_file()

    def test_output_is_sorted_for_stable_diffs(self, tmp_path: Path) -> None:
        """Re-snapshotting the same DB should produce byte-identical YAML."""
        snap = {
            "tables": {
                "z_table": {"columns": {"b": {"type": "INT"}, "a": {"type": "INT"}}},
                "a_table": {"columns": {"y": {"type": "INT"}, "x": {"type": "INT"}}},
            }
        }
        out_a = tmp_path / "a.yml"
        out_b = tmp_path / "b.yml"
        write_snapshot(snap, out_a)
        write_snapshot(snap, out_b)
        assert out_a.read_bytes() == out_b.read_bytes()
        # And keys are alphabetised, not insertion-ordered.
        text = out_a.read_text()
        assert "a_table" in text.split("z_table")[0]
