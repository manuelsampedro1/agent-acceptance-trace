# AGENTS.md

## Goal

Build a small, dependency-free Python CLI that traces task acceptance criteria
to concrete diff and evidence text before a coding-agent closeout is accepted.

## Constraints

- Keep the tool local-first and standard-library only.
- Prefer readable heuristics over hidden scoring.
- Do not claim semantic proof; report weak or missing evidence explicitly.
- Do not read secrets or environment files as evidence by default.
- Keep generated metadata, virtualenvs, and build outputs out of version control.

## Verification

Run these before committing:

```sh
make test
make lint
make build
make smoke
git diff --check
```

## Closeout

Report changed behavior, exact verification commands, and any remaining
limitations. Do not call a criterion covered unless the output shows evidence.

