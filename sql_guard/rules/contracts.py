"""Contract rules (C001-...) -- require a loaded data contract to fire.

These rules are only added to the active rule list when ``--contract`` is
passed (or ``contract:`` is set in ``.sql-guard.yml``). Without a contract
they are silent. The contract structure is defined in ``sql_guard.contracts``.
"""

from __future__ import annotations

import re

from sql_guard.contracts import Contract
from sql_guard.rules.base import Finding, Rule


class ContractRule(Rule):
    """Base class for contract-aware rules.

    Subclasses receive a Contract instance at construction time. When the
    contract is None, the rule no-ops, which keeps the registry shape stable
    even when ``--contract`` is not provided.
    """

    def __init__(self, contract: Contract | None = None) -> None:
        self.contract = contract


class ColumnNotInContract(ContractRule):
    """C001: Column referenced in SQL is not declared in the contract for the table.

    Walks ``table.column`` and ``alias.column`` references and looks each one
    up against the contract. A miss is a finding. ``SELECT *`` cannot be
    checked column-by-column at this layer, so this rule complements W001
    rather than replacing it.
    """

    id = "C001"
    name = "column-not-in-contract"
    severity = "warning"
    description = "Column reference not declared in the contract for that table"
    multiline = True

    # alias.column or table.column references in the body of a statement.
    _qualified_ref = re.compile(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b")
    # FROM/JOIN clauses, with an optional alias (with or without AS).
    _from_table = re.compile(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)(?:\s+(?:AS\s+)?([A-Za-z_][\w]*))?",
        re.IGNORECASE,
    )

    def check_statement(self, statement: str, start_line: int, file: str) -> Finding | None:
        if not self.contract:
            return None

        # Build alias -> table-name map. Bare table references map to themselves.
        aliases: dict[str, str] = {}
        for m in self._from_table.finditer(statement):
            table_name = m.group(1).lower()
            alias = (m.group(2) or m.group(1)).lower()
            aliases[alias] = table_name

        if not aliases:
            return None

        # First miss wins. The reporter is happier with one finding per
        # statement than with a scatter of duplicates.
        for m in self._qualified_ref.finditer(statement):
            ref_alias = m.group(1).lower()
            ref_col = m.group(2).lower()
            if ref_alias not in aliases:
                continue
            table_name = aliases[ref_alias]
            table = self.contract.get_table(table_name)
            if table is None:
                continue
            if ref_col not in table.columns:
                return Finding(
                    rule_id=self.id,
                    severity=self.severity,
                    file=file,
                    line=start_line,
                    message=(
                        f"Column '{ref_col}' is not declared in the contract "
                        f"for table '{table_name}'"
                    ),
                    suggestion=(
                        f"Either add '{ref_col}' to the contract or correct the column name"
                    ),
                )
        return None


class TableNotInContract(ContractRule):
    """C002: Statement references a table that has no entry in the contract.

    Useful when a contract is expected to cover the whole schema. Disable
    this rule if you only want to lint a subset of tables and intentionally
    leave others out.
    """

    id = "C002"
    name = "table-not-in-contract"
    severity = "warning"
    description = "Statement references a table not declared in the contract"
    multiline = True

    _from_table = re.compile(
        r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][\w]*)",
        re.IGNORECASE,
    )

    def check_statement(self, statement: str, start_line: int, file: str) -> Finding | None:
        if not self.contract:
            return None
        for m in self._from_table.finditer(statement):
            table_name = m.group(1)
            if self.contract.get_table(table_name) is None:
                return Finding(
                    rule_id=self.id,
                    severity=self.severity,
                    file=file,
                    line=start_line,
                    message=f"Table '{table_name}' is not declared in the contract",
                    suggestion=(
                        "Either add the table to the contract or disable C002 "
                        "for partial-coverage contracts"
                    ),
                )
        return None


class NotNullViolation(ContractRule):
    """C003: INSERT omits a NOT NULL column declared in the contract.

    Only triggers on parenthesised column lists, e.g.
    ``INSERT INTO orders (id, customer_id) VALUES (...)``. Bare
    ``INSERT INTO orders VALUES (...)`` already triggers E005
    (insert-without-columns) which is the more general fix.
    """

    id = "C003"
    name = "not-null-violation"
    severity = "error"
    description = "INSERT omits a column declared NOT NULL in the contract"
    multiline = True

    _insert = re.compile(
        r"\bINSERT\s+INTO\s+([A-Za-z_][\w]*)\s*\(([^)]+)\)",
        re.IGNORECASE,
    )

    def check_statement(self, statement: str, start_line: int, file: str) -> Finding | None:
        if not self.contract:
            return None
        m = self._insert.search(statement)
        if not m:
            return None

        table_name = m.group(1)
        table = self.contract.get_table(table_name)
        if table is None:
            return None

        listed_cols = {c.strip().strip('[]`"').lower() for c in m.group(2).split(",")}
        missing = [c for c in table.required_columns if c not in listed_cols]

        if missing:
            return Finding(
                rule_id=self.id,
                severity=self.severity,
                file=file,
                line=start_line,
                message=(
                    f"INSERT into '{table_name}' is missing NOT NULL "
                    f"columns: {', '.join(sorted(missing))}"
                ),
                suggestion=(
                    "Include every NOT NULL column in the INSERT column "
                    "list and the VALUES tuple, or add a default in the contract"
                ),
            )
        return None


class PrimaryKeyMissingOnInsert(ContractRule):
    """C004: INSERT into a contract table omits its primary key (and no default).

    Tracks PK columns separately from generic NOT NULL because the failure
    mode is different: a missing PK becomes a constraint violation at write
    time, not a NULL coalescing surprise.
    """

    id = "C004"
    name = "primary-key-missing-on-insert"
    severity = "error"
    description = "INSERT into a contract table omits the primary key with no default"
    multiline = True

    _insert = re.compile(
        r"\bINSERT\s+INTO\s+([A-Za-z_][\w]*)\s*\(([^)]+)\)",
        re.IGNORECASE,
    )

    def check_statement(self, statement: str, start_line: int, file: str) -> Finding | None:
        if not self.contract:
            return None
        m = self._insert.search(statement)
        if not m:
            return None

        table_name = m.group(1)
        table = self.contract.get_table(table_name)
        if table is None or not table.primary_keys:
            return None

        listed_cols = {c.strip().strip('[]`"').lower() for c in m.group(2).split(",")}
        missing_pks = [
            pk
            for pk in table.primary_keys
            if pk not in listed_cols and not table.columns[pk].has_default
        ]

        if missing_pks:
            return Finding(
                rule_id=self.id,
                severity=self.severity,
                file=file,
                line=start_line,
                message=(
                    f"INSERT into '{table_name}' is missing primary key "
                    f"column(s): {', '.join(sorted(missing_pks))}"
                ),
                suggestion=(
                    "Either include the primary key in the INSERT or "
                    "declare has_default: true in the contract"
                ),
            )
        return None


class UnmappedForeignKey(ContractRule):
    """C005: JOIN predicate uses columns the contract has no foreign key for.

    Catches accidental cross-table joins where the contract declares no
    relationship between the two columns. Walks every ``alias.col =
    alias.col`` equality inside a ``JOIN ... ON`` clause and checks
    whether either side has a ``foreign_key: other_table.col`` pointing
    at the other side. Equalities involving tables not in the contract
    are skipped (C002 owns that case).
    """

    id = "C005"
    name = "unmapped-fk"
    severity = "warning"
    description = "JOIN ... ON uses columns with no FK relationship in the contract"
    multiline = True

    _from_table = re.compile(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)(?:\s+(?:AS\s+)?([A-Za-z_][\w]*))?",
        re.IGNORECASE,
    )
    _join_on_block = re.compile(
        r"\bJOIN\s+[A-Za-z_][\w]*(?:\s+(?:AS\s+)?[A-Za-z_][\w]*)?\s+ON\s+(.+?)"
        r"(?=\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bJOIN\b|;|$)",
        re.IGNORECASE | re.DOTALL,
    )
    _equality = re.compile(
        r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*=\s*([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b"
    )

    def _fk_resolves(
        self,
        source_table_name: str,
        source_col: str,
        target_table_name: str,
        target_col: str,
    ) -> bool:
        if self.contract is None:
            return False
        source_table = self.contract.get_table(source_table_name)
        if source_table is None:
            return False
        col = source_table.columns.get(source_col)
        if col is None or not col.foreign_key:
            return False
        ref_table, _, ref_col = col.foreign_key.partition(".")
        return (
            ref_table.lower() == target_table_name.lower() and ref_col.lower() == target_col.lower()
        )

    def check_statement(self, statement: str, start_line: int, file: str) -> Finding | None:
        if not self.contract:
            return None

        # alias -> table for every FROM/JOIN target.
        aliases: dict[str, str] = {}
        for m in self._from_table.finditer(statement):
            table_name = m.group(1).lower()
            alias = (m.group(2) or m.group(1)).lower()
            aliases[alias] = table_name

        if not aliases:
            return None

        for join_match in self._join_on_block.finditer(statement):
            on_body = join_match.group(1)
            for eq in self._equality.finditer(on_body):
                left_alias, left_col = eq.group(1).lower(), eq.group(2).lower()
                right_alias, right_col = eq.group(3).lower(), eq.group(4).lower()

                left_table = aliases.get(left_alias)
                right_table = aliases.get(right_alias)
                if left_table is None or right_table is None:
                    continue

                # Both tables must be in the contract -- C002 owns the
                # "table not in contract" case.
                if (
                    self.contract.get_table(left_table) is None
                    or self.contract.get_table(right_table) is None
                ):
                    continue

                # Either column may declare the FK; check both directions.
                if self._fk_resolves(left_table, left_col, right_table, right_col):
                    continue
                if self._fk_resolves(right_table, right_col, left_table, left_col):
                    continue

                return Finding(
                    rule_id=self.id,
                    severity=self.severity,
                    file=file,
                    line=start_line,
                    message=(
                        f"JOIN on '{left_alias}.{left_col} = "
                        f"{right_alias}.{right_col}' has no foreign-key "
                        f"declaration in the contract"
                    ),
                    suggestion=(
                        "Add a foreign_key: <table>.<column> entry to the "
                        "owning column in the contract, or correct the JOIN"
                    ),
                )
        return None


class ColumnTypeMismatchOnInsert(ContractRule):
    """C006: INSERT writes a value whose literal kind disagrees with the contract type.

    Coarse static typing only — buckets columns and value literals into
    numeric / string / date / boolean and flags mismatches between the two
    sides. Values it cannot statically classify (parameters, function
    calls, bare identifiers, expressions, NULL) are skipped, because a
    coarse linter shouldn't flag what a parser would need to disprove.

    Catches the common copy-paste class of bug:
    ``INSERT INTO orders (total) VALUES ('not a number')``
    where ``total`` is decimal.
    """

    id = "C006"
    name = "column-type-mismatch-on-insert"
    severity = "error"
    description = "INSERT value's literal kind disagrees with the contract column type"
    multiline = True

    # Capture an INSERT INTO t (cols) VALUES (...) tail.
    _insert_with_values = re.compile(
        r"\bINSERT\s+INTO\s+([A-Za-z_][\w]*)\s*\(([^)]+)\)\s*VALUES\s*(.+?)"
        r"(?=;|$)",
        re.IGNORECASE | re.DOTALL,
    )

    _NUMERIC_TYPES = {
        "int",
        "integer",
        "bigint",
        "smallint",
        "tinyint",
        "decimal",
        "numeric",
        "float",
        "real",
        "double",
        "money",
        "smallmoney",
    }
    _STRING_TYPES = {
        "char",
        "varchar",
        "nvarchar",
        "nchar",
        "text",
        "ntext",
        "string",
        "clob",
        "varchar2",
    }
    _DATE_TYPES = {
        "date",
        "datetime",
        "datetime2",
        "smalldatetime",
        "timestamp",
        "time",
        "datetimeoffset",
    }
    _BOOLEAN_TYPES = {"bit", "bool", "boolean"}

    @classmethod
    def _bucket(cls, type_str: str) -> str:
        """Coarse-bucket a contract type string. Returns 'unknown' for unmapped types."""
        base = type_str.lower().split("(", 1)[0].strip()
        if base in cls._NUMERIC_TYPES:
            return "numeric"
        if base in cls._STRING_TYPES:
            return "string"
        if base in cls._DATE_TYPES:
            return "date"
        if base in cls._BOOLEAN_TYPES:
            return "boolean"
        return "unknown"

    _re_number = re.compile(r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$")
    _re_string = re.compile(r"^[NBnb]?'.*'$|^\".*\"$", re.DOTALL)
    _re_function = re.compile(r"^[A-Za-z_]\w*\s*\(")
    _re_named_param = re.compile(r"^[@:$]\w+$|^\$\d+$")

    @classmethod
    def _classify(cls, raw: str) -> str:
        """Classify a value literal. Returns one of:
        number, string, boolean, null, parameter, function, other.
        """
        token = raw.strip()
        if not token:
            return "other"
        upper = token.upper()
        if upper == "NULL":
            return "null"
        if token in {"?", "%s"} or cls._re_named_param.match(token):
            return "parameter"
        if cls._re_string.match(token):
            return "string"
        if cls._re_number.match(token):
            return "number"
        if upper in {"TRUE", "FALSE"}:
            return "boolean"
        if cls._re_function.match(token):
            return "function"
        return "other"

    @staticmethod
    def _split_tuples(values_tail: str) -> list[str]:
        """Pull out each ``(...)`` tuple body from a VALUES tail.

        Handles multi-row INSERT and string literals containing ``)``.
        """
        tuples: list[str] = []
        depth = 0
        buf: list[str] = []
        i = 0
        in_string: str | None = None
        capturing = False
        while i < len(values_tail):
            ch = values_tail[i]
            if in_string:
                buf.append(ch)
                # Doubled quote = escaped quote inside the string.
                if ch == in_string and values_tail[i : i + 2] == in_string * 2:
                    buf.append(values_tail[i + 1])
                    i += 2
                    continue
                if ch == in_string:
                    in_string = None
                i += 1
                continue
            if ch in ("'", '"'):
                if capturing:
                    buf.append(ch)
                in_string = ch
                i += 1
                continue
            if ch == "(":
                if depth == 0:
                    capturing = True
                    buf = []
                else:
                    buf.append(ch)
                depth += 1
                i += 1
                continue
            if ch == ")":
                depth -= 1
                if depth == 0 and capturing:
                    tuples.append("".join(buf))
                    capturing = False
                else:
                    buf.append(ch)
                i += 1
                continue
            if capturing:
                buf.append(ch)
            i += 1
        return tuples

    @staticmethod
    def _split_values(tuple_body: str) -> list[str]:
        """Comma-split a values tuple while respecting parens and strings."""
        parts: list[str] = []
        depth = 0
        buf: list[str] = []
        in_string: str | None = None
        i = 0
        while i < len(tuple_body):
            ch = tuple_body[i]
            if in_string:
                buf.append(ch)
                if ch == in_string and tuple_body[i : i + 2] == in_string * 2:
                    buf.append(tuple_body[i + 1])
                    i += 2
                    continue
                if ch == in_string:
                    in_string = None
                i += 1
                continue
            if ch in ("'", '"'):
                in_string = ch
                buf.append(ch)
                i += 1
                continue
            if ch == "(":
                depth += 1
                buf.append(ch)
                i += 1
                continue
            if ch == ")":
                depth -= 1
                buf.append(ch)
                i += 1
                continue
            if ch == "," and depth == 0:
                parts.append("".join(buf))
                buf = []
                i += 1
                continue
            buf.append(ch)
            i += 1
        if buf:
            parts.append("".join(buf))
        return parts

    @classmethod
    def _mismatch(cls, col_bucket: str, value_kind: str, raw_value: str) -> bool:
        """Return True if the value's literal kind disagrees with the column bucket."""
        # Lenient cases: NULL, parameter, function call, bare expression, or unknown column type.
        if value_kind in {"null", "parameter", "function", "other"}:
            return False
        if col_bucket == "unknown":
            return False

        if col_bucket == "numeric":
            return value_kind == "string"
        if col_bucket == "string":
            return value_kind == "number"
        if col_bucket == "date":
            # Dates are usually written as string literals; only flag clearly-wrong shapes.
            return value_kind in {"number", "boolean"}
        if col_bucket == "boolean":
            if value_kind == "string":
                return raw_value.strip().strip("'\"").lower() not in {
                    "true",
                    "false",
                    "0",
                    "1",
                }
            if value_kind == "number":
                return raw_value.strip() not in {"0", "1"}
        return False

    def check_statement(self, statement: str, start_line: int, file: str) -> Finding | None:
        if not self.contract:
            return None
        m = self._insert_with_values.search(statement)
        if not m:
            return None

        table_name = m.group(1)
        table = self.contract.get_table(table_name)
        if table is None:
            return None

        col_names = [c.strip().strip('[]`"').lower() for c in m.group(2).split(",")]
        tuples = self._split_tuples(m.group(3))
        if not tuples:
            return None

        for tuple_body in tuples:
            raw_values = self._split_values(tuple_body)
            if len(raw_values) != len(col_names):
                # Column / value arity mismatch is a SQL error that the database will catch.
                # Don't piggyback type checks on top of a structurally broken INSERT.
                continue
            for col_name, raw_value in zip(col_names, raw_values):
                col = table.columns.get(col_name)
                if col is None:
                    continue  # C001 owns the unknown-column case.
                col_bucket = self._bucket(col.type)
                value_kind = self._classify(raw_value)
                if self._mismatch(col_bucket, value_kind, raw_value):
                    return Finding(
                        rule_id=self.id,
                        severity=self.severity,
                        file=file,
                        line=start_line,
                        message=(
                            f"INSERT into '{table_name}.{col_name}' writes a "
                            f"{value_kind} literal but the contract declares "
                            f"type '{col.type}' ({col_bucket})"
                        ),
                        suggestion=(
                            f"Pass a {col_bucket} value, a parameter, or fix the "
                            f"contract type for '{col_name}'"
                        ),
                    )
        return None


CONTRACT_RULE_CLASSES: list[type[ContractRule]] = [
    ColumnNotInContract,
    TableNotInContract,
    NotNullViolation,
    PrimaryKeyMissingOnInsert,
    UnmappedForeignKey,
    ColumnTypeMismatchOnInsert,
]


def build_contract_rules(contract: Contract | None) -> list[ContractRule]:
    """Instantiate every contract rule with the given (or empty) contract."""
    if contract is None:
        return []
    return [rule_class(contract=contract) for rule_class in CONTRACT_RULE_CLASSES]
