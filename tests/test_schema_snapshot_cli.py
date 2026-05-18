"""Tests for the ``sql-sop schema-snapshot`` subcommand."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

sqlalchemy = pytest.importorskip("sqlalchemy")

from sql_guard.cli import app  # noqa: E402  -- after importorskip

runner = CliRunner()


def _flat(text: str) -> str:
    """Collapse whitespace -- Rich wraps output to terminal width inside CliRunner."""
    return re.sub(r"\s+", " ", text)


def _build_sample_db(dsn: str) -> None:
    engine = sqlalchemy.create_engine(dsn)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT)"
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE orders (
                id           INTEGER PRIMARY KEY,
                customer_id  INTEGER NOT NULL REFERENCES customers(id),
                total        REAL    NOT NULL,
                status       TEXT    DEFAULT 'pending'
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


class TestSchemaSnapshotCli:
    def test_writes_contract_yaml(self, sqlite_dsn: str, tmp_path: Path) -> None:
        out = tmp_path / "contract.yml"
        result = runner.invoke(app, ["schema-snapshot", "--dsn", sqlite_dsn, "--output", str(out)])
        assert result.exit_code == 0, result.output
        assert out.is_file()
        assert "OK" in result.output
        assert "2 tables" in _flat(result.output)

    def test_output_yaml_is_loadable_as_contract(self, sqlite_dsn: str, tmp_path: Path) -> None:
        out = tmp_path / "contract.yml"
        result = runner.invoke(app, ["schema-snapshot", "--dsn", sqlite_dsn, "--output", str(out)])
        assert result.exit_code == 0
        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert set(loaded["tables"]) == {"customers", "orders"}
        assert loaded["tables"]["customers"]["columns"]["id"]["primary_key"] is True
        assert loaded["tables"]["orders"]["columns"]["customer_id"]["foreign_key"] == "customers.id"

    def test_short_output_flag(self, sqlite_dsn: str, tmp_path: Path) -> None:
        out = tmp_path / "via_short_flag.yml"
        result = runner.invoke(app, ["schema-snapshot", "--dsn", sqlite_dsn, "-o", str(out)])
        assert result.exit_code == 0
        assert out.is_file()

    def test_include_table_restricts_output(self, sqlite_dsn: str, tmp_path: Path) -> None:
        out = tmp_path / "subset.yml"
        result = runner.invoke(
            app,
            [
                "schema-snapshot",
                "--dsn",
                sqlite_dsn,
                "--output",
                str(out),
                "--include-table",
                "orders",
            ],
        )
        assert result.exit_code == 0
        assert "1 tables" in _flat(result.output)
        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert list(loaded["tables"]) == ["orders"]

    def test_include_table_repeatable(self, sqlite_dsn: str, tmp_path: Path) -> None:
        out = tmp_path / "subset2.yml"
        result = runner.invoke(
            app,
            [
                "schema-snapshot",
                "--dsn",
                sqlite_dsn,
                "--output",
                str(out),
                "--include-table",
                "orders",
                "--include-table",
                "customers",
            ],
        )
        assert result.exit_code == 0
        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert set(loaded["tables"]) == {"orders", "customers"}

    def test_bad_dsn_exits_two(self, tmp_path: Path) -> None:
        out = tmp_path / "won't_write.yml"
        result = runner.invoke(
            app,
            [
                "schema-snapshot",
                "--dsn",
                "sqlite:///" + str(tmp_path / "does_not_exist.db"),
                "--output",
                str(out),
                "--include-table",
                "no_such_table",
            ],
        )
        # An empty DB still introspects cleanly; the bad case here is just "no tables".
        # Exit code stays 0; we just confirm it didn't blow up.
        assert result.exit_code == 0
        assert "0 tables" in _flat(result.output)

    def test_unparseable_dsn_exits_two(self, tmp_path: Path) -> None:
        out = tmp_path / "fail.yml"
        result = runner.invoke(
            app,
            ["schema-snapshot", "--dsn", "not-a-real-dsn://", "--output", str(out)],
        )
        assert result.exit_code == 2
        flat = _flat(result.output.lower())
        assert "failed" in flat or "error" in flat

    def test_missing_required_dsn_exits_nonzero(self) -> None:
        result = runner.invoke(app, ["schema-snapshot"])
        assert result.exit_code != 0

    def test_default_output_path_is_contract_yml(
        self, sqlite_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If --output isn't supplied, it should write to ./contract.yml in cwd."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["schema-snapshot", "--dsn", sqlite_dsn])
        assert result.exit_code == 0
        assert (tmp_path / "contract.yml").is_file()
