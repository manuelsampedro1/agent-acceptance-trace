# agent-acceptance-trace

Trace a coding-agent task's acceptance criteria to concrete implementation and
closeout evidence before accepting the final answer.

The tool is intentionally small: it parses a Markdown task contract, reads a
unified diff plus optional evidence files, then produces a criterion-by-criterion
matrix with `covered`, `partial`, or `missing` status. It does not pretend to
understand the whole task semantically; it makes weak evidence visible.

Structured `agent-proof-packet.v1` files can be passed separately with
`--proof-packet`. They are only used as evidence after the packet is complete,
has passing checks, has changed-file evidence, has no missing evidence, and
matches the provided diff.

## Why

Coding agents often close with a confident summary. Reviewers still need a
simple way to ask: which acceptance criteria are actually supported by changed
files, commands, receipts, proof packets, or closeout text?

Use this before:

- accepting a coding-agent closeout,
- converting a final answer into a PR comment,
- importing proof into an agent run ledger,
- asking another agent to continue a half-finished task.

## Install

```sh
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -e .
```

## Usage

```sh
agent-acceptance-trace examples/task-contract.md \
  --diff examples/sample.diff \
  --evidence examples/closeout.md \
  --min-covered 80
```

Use a verified proof packet as structured acceptance evidence:

```sh
agent-acceptance-trace examples/task-contract.md \
  --diff examples/sample.diff \
  --evidence examples/closeout.md \
  --proof-packet examples/proof-packet.json \
  --min-covered 80
```

JSON output for automation:

```sh
agent-acceptance-trace examples/task-contract.md \
  --diff examples/sample.diff \
  --evidence examples/closeout.md \
  --format json \
  --min-covered 80
```

If `--diff` is omitted, the tool tries to read `git diff --unified=0` from
`--repo`:

```sh
agent-acceptance-trace TASK_CONTRACT.md --repo . --base origin/main
```

Use `--strict` to fail on any partial or missing criterion.

## Task Format

The task file should contain an acceptance section:

```md
## Acceptance Criteria

- The CLI can add todo items with a title and optional priority.
- The CLI can list open todo items from the local JSON store.
- The implementation includes tests and a documented smoke command.
```

Spanish headings such as `## Criterios de aceptacion` are also recognized.

## Output

Markdown output includes:

- score and covered/partial/missing counts,
- changed files from the diff,
- proof-packet status when `--proof-packet` is used,
- matched and missing terms per criterion,
- evidence snippets with source and line numbers.

JSON output contains the same matrix for CI gates or ledger import.

## Limits

- This is a traceability heuristic, not a semantic theorem prover.
- Keyword evidence can be too weak for complex product or security requirements.
- Proof packets are treated as evidence only after verdict, checks, missing
  evidence, and changed-file alignment pass.
- Secret-like evidence paths such as `.env`, `.npmrc`, private keys, and PEM files
  are refused by default.
- Reviewers should treat `partial` as unresolved until stronger evidence exists.

## Verify

```sh
make test
make lint
make build
make smoke
```
