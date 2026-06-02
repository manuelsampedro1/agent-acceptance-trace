# Closeout

Implemented the todo CLI with `add` and `list` commands. The add command accepts
a title and optional priority, then writes to `todos.json`. The list command
prints open todo items from the local JSON store.

Verification run:

- `make test`
- `make smoke`

Residual risk: the JSON store is local-only and has no locking.

