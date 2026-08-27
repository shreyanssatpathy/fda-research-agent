# Contract: SQL safety

CLAUDE.md requires five safeguards on every generated query. This records where
each is enforced and what it actually defends against.

| safeguard | enforced in | mechanism |
|---|---|---|
| SELECT-only | `sql_guard.validate` | AST shape check + forbidden node walk |
| Read-only user | `db.connect` | DuckDB opened `read_only=True` |
| Table allowlist | `sql_guard.validate` | every `exp.Table` resolved against `TABLE_ALLOWLIST` |
| Row limit | `sql_guard.validate` | `LIMIT` injected or clamped to `MAX_ROWS` |
| Statement timeout | `query.run` | watchdog thread calling `con.interrupt()` |

Validation is a parser, never a regex. `sqlglot` parses to an AST and every check
walks it. A regex cannot distinguish a table from a table function, cannot see
into subqueries or CTEs, and is defeated by comments and whitespace.

## The threat that matters

The obvious risk — a generated `DROP TABLE` — is already dead: the connection is
read-only. The real exposure is that **a pure SELECT can read the local
filesystem**, and read-only mode does not stop it:

```sql
SELECT * FROM read_csv_auto('/etc/passwd')
SELECT content FROM read_text('~/.aws/credentials')
```

Both were verified to succeed against a read-only connection to this database.
`tests/test_sql_guard.py::test_read_only_alone_does_not_block_file_reads` asserts
the vulnerability still exists, so the rationale for the guard cannot quietly rot.

Two checks close it:

1. **Table functions are rejected.** A table function parses as an `exp.Table`
   whose `this` is a function rather than an identifier. Treating it as a table
   name — which a regex or a naive allowlist does — lets it through.
2. **Unrecognised functions are rejected.** `sqlglot` parses known SQL functions
   into typed nodes; dialect-specific and unknown ones become `exp.Anonymous`.
   Every filesystem-reaching DuckDB function lands there. Verified that **none of
   the 27 answerable queries in the frozen golden set uses one**, so this
   restriction costs nothing legitimate.

The second check also covers file readers called outside `FROM`, e.g.
`SELECT read_text('/etc/passwd') FROM fda_510k`, which the table check alone
would miss.

## Execution

`query.run` is the only path that executes generated SQL. It executes the guard's
**rewritten** output, never the model's original string, so the row cap cannot be
bypassed by what was generated.

## Logging

Every execution appends one JSON line to `data/logs/queries.jsonl`: the question,
the generated SQL, the executed SQL, tables touched, outcome, row count, duration.
**Rejections are logged too, before the exception is raised** — a blocked attempt
is the most valuable record in the file, not the least. The log is append-only and
gitignored.

## Not yet covered

- Column-level allowlisting. Any column of `fda_510k` is currently readable, which
  is acceptable while the table holds only public FDA data. Revisit before any
  non-public table is added.
- Cost limits beyond the timeout. A query can be cheap to parse and expensive to
  run; the timeout bounds it in wall-clock but not in memory.
