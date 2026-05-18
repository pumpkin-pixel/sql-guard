"""Tests for the ``sql-sop validate-contract`` subcommand."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sql_guard.cli import app

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_CONTRACT = FIXTURES / "contract_sample.yml"

runner = CliRunner()


def _flat(text: str) -> str:
    """Collapse all whitespace -- Rich wraps output to terminal width inside CliRunner."""
    return re.sub(r"\s+", " ", text)


class TestValidateContractCli:
    def test_valid_contract_exits_zero(self) -> None:
        result = runner.invoke(app, ["validate-contract", "--contract", str(SAMPLE_CONTRACT)])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output
        # contract_sample.yml has 2 tables (orders, customers).
        assert "2 tables" in _flat(result.output)

    def test_valid_contract_reports_column_count(self) -> None:
        result = runner.invoke(app, ["validate-contract", "--contract", str(SAMPLE_CONTRACT)])
        assert result.exit_code == 0
        # orders: 5 columns, customers: 3 columns -> 8 total
        assert "8 columns" in _flat(result.output)

    def test_valid_contract_reports_pk_and_fk_counts(self) -> None:
        result = runner.invoke(app, ["validate-contract", "--contract", str(SAMPLE_CONTRACT)])
        assert result.exit_code == 0
        # 2 PKs (one per table) and 1 FK (orders.customer_id -> customers.id)
        flat = _flat(result.output)
        assert "2 primary keys" in flat
        assert "1 foreign keys" in flat

    def test_missing_file_exits_two(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.yml"
        result = runner.invoke(app, ["validate-contract", "--contract", str(missing)])
        assert result.exit_code == 2
        assert "not found" in result.output.lower()

    def test_malformed_yaml_exits_two(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yml"
        bad.write_text("tables:\n  orders:\n    columns:\n      id: [unclosed\n", encoding="utf-8")
        result = runner.invoke(app, ["validate-contract", "--contract", str(bad)])
        assert result.exit_code == 2
        assert "invalid contract" in result.output.lower()

    def test_empty_contract_warns_but_exits_zero(self, tmp_path: Path) -> None:
        """A YAML with no tables is structurally valid but useless; warn, don't fail."""
        empty = tmp_path / "empty.yml"
        empty.write_text("tables: {}\n", encoding="utf-8")
        result = runner.invoke(app, ["validate-contract", "--contract", str(empty)])
        assert result.exit_code == 0
        assert "no tables" in result.output.lower()
        assert "no effect" in result.output.lower()

    def test_completely_empty_file_treated_as_empty_contract(self, tmp_path: Path) -> None:
        """``yaml.safe_load('')`` returns None; Contract.from_dict handles it without crashing."""
        empty = tmp_path / "blank.yml"
        empty.write_text("", encoding="utf-8")
        result = runner.invoke(app, ["validate-contract", "--contract", str(empty)])
        assert result.exit_code == 0
        assert "no tables" in result.output.lower()

    def test_missing_required_option_exits_nonzero(self) -> None:
        result = runner.invoke(app, ["validate-contract"])
        assert result.exit_code != 0

    @pytest.mark.parametrize(
        "shape",
        [
            "tables:\n  t:\n    columns:\n      c: int\n",  # shorthand string column type
            "tables:\n  t:\n    columns:\n      c: {type: int, not_null: true}\n",  # dict shape
        ],
    )
    def test_accepts_both_column_shapes(self, tmp_path: Path, shape: str) -> None:
        f = tmp_path / "c.yml"
        f.write_text(shape, encoding="utf-8")
        result = runner.invoke(app, ["validate-contract", "--contract", str(f)])
        assert result.exit_code == 0
        flat = _flat(result.output)
        assert "1 tables" in flat
        assert "1 columns" in flat
