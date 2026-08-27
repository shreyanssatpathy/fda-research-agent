# data/

Nothing in this directory is tracked by git except this file. See `.gitignore`.

## Layout

```
data/
  raw/          source files exactly as received — append-only, never edited
    fda/
    pitchbook/
  interim/      intermediate build products, safe to delete and regenerate
  processed/    query-ready database files / load-ready tables
```

## Rules

- `raw/` is append-only. Once a file lands, it is not edited or overwritten.
  Corrections arrive as new files, not as edits to old ones.
- Everything in `interim/` and `processed/` must be reproducible from `raw/`
  by running code in this repo. If it isn't, it doesn't belong here.
- No file from any of these directories is ever committed, published, or
  included in a derived artifact that leaves the repo.
