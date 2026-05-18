"""Tests for C006 column-type-mismatch-on-insert."""

from __future__ import annotations

from pathlib import Path

import pytest

from sql_guard.contracts import Contract
from sql_guard.rules.contracts import ColumnTypeMismatchOnInsert

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_CONTRACT = FIXTURES / "contract_sample.yml"


@pytest.fixture(scope="module")
def contract() -> Contract:
    return Contract.from_file(SAMPLE_CONTRACT)


@pytest.fixture
def rule(contract: Contract) -> ColumnTypeMismatchOnInsert:
    return ColumnTypeMismatchOnInsert(contract=contract)


# ---------------------------------------------------------------------------
# Mismatch cases
# ---------------------------------------------------------------------------


class TestNumericColumns:
    def test_flags_string_into_decimal(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES ('not a number');"
        finding = rule.check_statement(sql, 1, "t.sql")
        assert finding is not None
        assert finding.rule_id == "C006"
        assert "total" in finding.message
        assert "string" in finding.message
        assert "decimal" in finding.message
        assert finding.severity == "error"

    def test_flags_string_into_bigint(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO customers (id, name) VALUES ('one', 'Alice');"
        finding = rule.check_statement(sql, 1, "t.sql")
        assert finding is not None
        assert "id" in finding.message

    def test_passes_numeric_into_decimal(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES (99.99);"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_passes_integer_into_decimal(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES (100);"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_passes_negative_number(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES (-5.5);"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_passes_scientific_notation(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES (1.5e3);"
        assert rule.check_statement(sql, 1, "t.sql") is None


class TestStringColumns:
    def test_flags_number_into_varchar(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO customers (name) VALUES (42);"
        finding = rule.check_statement(sql, 1, "t.sql")
        assert finding is not None
        assert "name" in finding.message
        assert "number" in finding.message

    def test_passes_string_into_varchar(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO customers (name) VALUES ('Alice');"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_passes_unicode_prefix_string(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO customers (name) VALUES (N'Müller');"
        assert rule.check_statement(sql, 1, "t.sql") is None


# ---------------------------------------------------------------------------
# Lenient cases -- C006 must not fire on these
# ---------------------------------------------------------------------------


class TestLenientCases:
    def test_null_is_never_a_mismatch(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES (NULL);"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_lowercase_null(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES (null);"
        assert rule.check_statement(sql, 1, "t.sql") is None

    @pytest.mark.parametrize("placeholder", ["?", "%s", ":total", "@total", "$1"])
    def test_parameter_placeholders_are_skipped(
        self, rule: ColumnTypeMismatchOnInsert, placeholder: str
    ) -> None:
        sql = f"INSERT INTO orders (total) VALUES ({placeholder});"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_function_calls_are_skipped(self, rule: ColumnTypeMismatchOnInsert) -> None:
        # Can't know GETDATE() returns a datetime without a catalog -- defer to the DB.
        sql = "INSERT INTO orders (created_at) VALUES (GETDATE());"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_bare_identifiers_are_skipped(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES (some_var);"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_expressions_are_skipped(self, rule: ColumnTypeMismatchOnInsert) -> None:
        # Static type inference for arbitrary expressions is out of scope.
        sql = "INSERT INTO orders (total) VALUES (1 + 2);"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_unknown_column_skipped(self, rule: ColumnTypeMismatchOnInsert) -> None:
        # Unknown column is C001's territory, not C006's.
        sql = "INSERT INTO orders (no_such_col) VALUES ('foo');"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_table_not_in_contract_skipped(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO unknown_table (col) VALUES (42);"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_arity_mismatch_skipped(self, rule: ColumnTypeMismatchOnInsert) -> None:
        # Structurally broken INSERT -- the DB will reject it. C006 stays quiet.
        sql = "INSERT INTO orders (total, status) VALUES (99.99);"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_no_values_clause_skipped(self, rule: ColumnTypeMismatchOnInsert) -> None:
        # INSERT ... SELECT has no VALUES tuples to check.
        sql = "INSERT INTO orders (total) SELECT amount FROM staging;"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_no_contract_means_silent(self) -> None:
        bare = ColumnTypeMismatchOnInsert(contract=None)
        sql = "INSERT INTO orders (total) VALUES ('not a number');"
        assert bare.check_statement(sql, 1, "t.sql") is None


# ---------------------------------------------------------------------------
# Multi-row INSERT
# ---------------------------------------------------------------------------


class TestMultiRowInsert:
    def test_flags_mismatch_in_later_tuple(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES (99.99), ('oops'), (50);"
        finding = rule.check_statement(sql, 1, "t.sql")
        assert finding is not None
        assert "string" in finding.message

    def test_passes_when_every_tuple_is_clean(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES (99.99), (100), (-5.5);"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_commas_inside_string_literal_dont_split_the_tuple(
        self, rule: ColumnTypeMismatchOnInsert
    ) -> None:
        sql = "INSERT INTO customers (id, name) VALUES (1, 'Smith, John'), (2, 'Doe, Jane');"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_escaped_quote_inside_string_literal(self, rule: ColumnTypeMismatchOnInsert) -> None:
        # SQL doubles quotes to escape: O''Brien
        sql = "INSERT INTO customers (id, name) VALUES (1, 'O''Brien');"
        assert rule.check_statement(sql, 1, "t.sql") is None

    def test_function_call_with_commas_is_one_value(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (total) VALUES (COALESCE(amount, 0, fallback));"
        # Single function-call value, no mismatch flagged.
        assert rule.check_statement(sql, 1, "t.sql") is None


# ---------------------------------------------------------------------------
# Quoting around column names
# ---------------------------------------------------------------------------


class TestQuotedColumnNames:
    @pytest.mark.parametrize("quoted", ["[total]", "`total`", '"total"'])
    def test_quoted_column_still_resolves(
        self, rule: ColumnTypeMismatchOnInsert, quoted: str
    ) -> None:
        sql = f"INSERT INTO orders ({quoted}) VALUES ('not a number');"
        finding = rule.check_statement(sql, 1, "t.sql")
        assert finding is not None
        assert "total" in finding.message

    def test_column_name_case_insensitive(self, rule: ColumnTypeMismatchOnInsert) -> None:
        sql = "INSERT INTO orders (TOTAL) VALUES ('nope');"
        assert rule.check_statement(sql, 1, "t.sql") is not None


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_c006_in_contract_rule_classes(self) -> None:
        from sql_guard.rules.contracts import CONTRACT_RULE_CLASSES

        assert ColumnTypeMismatchOnInsert in CONTRACT_RULE_CLASSES

    def test_build_contract_rules_includes_c006(self) -> None:
        from sql_guard.rules.contracts import build_contract_rules

        c = Contract.from_dict({"tables": {"t": {"columns": {"x": "int"}}}})
        rules = build_contract_rules(c)
        ids = {r.id for r in rules}
        assert "C006" in ids

    def test_c006_exported_from_rules_package(self) -> None:
        from sql_guard.rules import ColumnTypeMismatchOnInsert as Exported

        assert Exported is ColumnTypeMismatchOnInsert
