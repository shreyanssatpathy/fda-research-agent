"""Parse-based validation for generated SQL.

CLAUDE.md requires all five of: SELECT-only, read-only user, table allowlist, row
limit, statement timeout. This module owns the first, third and fourth. `db.py`
owns the read-only connection; `execute()` there owns the timeout.

Validation is a parser, never a regex. A regex over SQL text cannot tell a table
from a table function, cannot see through comments or nested subqueries, and is
defeated by whitespace. Everything below walks the parsed AST.

The threat this actually defends against is not a stray DROP TABLE — a read-only
DuckDB connection already refuses writes. It is that a *pure SELECT* can read the
local filesystem:

    SELECT * FROM read_csv_auto('/etc/passwd')
    SELECT content FROM read_text('~/.aws/credentials')

Both succeed on a read-only connection. The table allowlist is what stops them,
and only if table functions are resolved rather than assumed to be table names.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from fda_agent.config import MAX_ROWS, TABLE_ALLOWLIST

DIALECT = "duckdb"

# Any of these anywhere in the tree rejects the query. A read-only connection
# already blocks most, but failing at validation gives a clear reason and keeps
# the guard meaningful if the connection is ever opened writable by mistake.
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.Command, exp.Pragma, exp.Set, exp.Use, exp.Grant, exp.Transaction,
    exp.Copy, exp.Attach, exp.Detach, exp.Export,
)


# Functions sqlglot does not model but which are safe: pure scalar/aggregate
# computation, no filesystem, no network, no catalog access.
#
# Added because rejecting every unrecognised function had a false-positive cost I
# had claimed was zero. That claim was verified against the FDA golden set, which
# never used one — the PitchBook set immediately produced `date_part('year', ...)`,
# valid SQL blocked as if it were an exfiltration attempt. An allowlist keeps the
# default-deny posture while letting real queries through; each entry is a
# deliberate, reviewable decision.
FUNCTION_ALLOWLIST = frozenset({
    "date_part",
    "date_trunc",
    "datediff",
    "date_diff",
    "epoch",
    "last_day",
    "monthname",
    "dayname",
    "list_aggregate",
    "regexp_extract",
    "string_split",
    "levenshtein",
    "median",
    "mode",
    "quantile_cont",
    "quantile_disc",
    "stddev_samp",
    "stddev_pop",
    "var_samp",
    "corr",
    "greatest",
    "least",
    "ifnull",
})


class SqlValidationError(Exception):
    """Raised when generated SQL is not safe to execute. Never caught silently."""


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str            # rewritten, safe to execute
    tables: frozenset   # allowlisted tables actually referenced
    limit_applied: bool # True if the guard added a LIMIT the model omitted


def _cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}


def _check_statement_shape(sql: str) -> exp.Expression:
    try:
        statements = sqlglot.parse(sql, dialect=DIALECT)
    except sqlglot.ParseError as err:
        raise SqlValidationError(f"could not parse SQL: {err}") from err

    statements = [s for s in statements if s is not None]
    if not statements:
        raise SqlValidationError("empty query")
    if len(statements) > 1:
        raise SqlValidationError(
            f"expected a single statement, got {len(statements)}; "
            "statement chaining is not permitted"
        )

    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)):
        raise SqlValidationError(
            f"only SELECT queries are permitted; got {type(tree).__name__.upper()}"
        )
    return tree


def _check_no_forbidden_nodes(tree: exp.Expression) -> None:
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SqlValidationError(
                f"{type(node).__name__.upper()} is not permitted in a generated query"
            )


def _check_functions(tree: exp.Expression) -> None:
    """Reject functions sqlglot does not recognise.

    sqlglot parses known SQL functions into typed nodes; dialect-specific and
    unknown ones fall through to Anonymous. Every filesystem-reaching DuckDB
    function (read_csv, read_text, read_parquet, glob, ...) lands here, and no
    query in the frozen golden set does — so this costs nothing legitimate.
    """
    for fn in tree.find_all(exp.Anonymous):
        if fn.name.lower() in FUNCTION_ALLOWLIST:
            continue
        raise SqlValidationError(
            f"function {fn.name!r} is not on the permitted list; "
            "unrecognised functions are rejected because DuckDB exposes "
            "filesystem readers that a read-only connection does not block"
        )


def _check_tables(tree: exp.Expression, allowlist: frozenset[str]) -> frozenset[str]:
    known = _cte_names(tree)
    referenced: set[str] = set()

    for table in tree.find_all(exp.Table):
        # A table function parses as a Table whose `this` is a function, not an
        # identifier. This is the check that stops read_csv_auto('/etc/passwd').
        if not isinstance(table.this, exp.Identifier):
            rendered = table.sql(dialect=DIALECT)
            raise SqlValidationError(
                f"table functions are not permitted: {rendered}"
            )

        name = table.name.lower()
        if name in known:      # CTE reference, resolved within the query
            continue
        if name not in allowlist:
            raise SqlValidationError(
                f"table {table.name!r} is not on the allowlist "
                f"({', '.join(sorted(allowlist))})"
            )
        referenced.add(name)

    if not referenced:
        raise SqlValidationError("query references no allowlisted table")
    return frozenset(referenced)


def _apply_limit(tree: exp.Expression, max_rows: int) -> tuple[exp.Expression, bool]:
    """Guarantee a bounded result set.

    An existing tighter LIMIT is respected; a larger or absent one is clamped, so
    the cap cannot be argued away by the model that wrote the query.
    """
    existing = tree.args.get("limit")
    if existing is not None:
        try:
            current = int(existing.expression.name)
        except (AttributeError, ValueError):
            raise SqlValidationError("LIMIT must be a literal integer")
        if current <= max_rows:
            return tree, False
    return tree.limit(max_rows), True


def validate(
    sql: str,
    *,
    allowlist: frozenset[str] = TABLE_ALLOWLIST,
    max_rows: int = MAX_ROWS,
) -> ValidatedQuery:
    """Validate and rewrite generated SQL, or raise SqlValidationError.

    The returned SQL is what gets executed — never the model's original string.
    """
    if not sql or not sql.strip():
        raise SqlValidationError("empty query")

    tree = _check_statement_shape(sql)
    _check_no_forbidden_nodes(tree)
    _check_functions(tree)
    tables = _check_tables(tree, allowlist)
    tree, limit_applied = _apply_limit(tree, max_rows)

    return ValidatedQuery(
        sql=tree.sql(dialect=DIALECT),
        tables=tables,
        limit_applied=limit_applied,
    )
