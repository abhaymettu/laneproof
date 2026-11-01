# textkit

Small text utilities with a command line: `python3 -m textkit <command> ...`.

## Conventions

- Each feature lives in its own module under `textkit/` and is registered in `textkit/cli.py` with `@command`.
- Library functions raise `ValueError` on invalid input. The CLI prints `error: <message>` to stderr and exits 2.
- Tests use the standard library: `python3 -m unittest`.
- Every command has a row in the table below, and every change gets a bullet under `## Unreleased` in CHANGELOG.md.

## Commands

| command | what it does |
| --- | --- |
| `commands` | list every command |
| `count` | count the words in the text |
| `shout` | upper-case the text |
