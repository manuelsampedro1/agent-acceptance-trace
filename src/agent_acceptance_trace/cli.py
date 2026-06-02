from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SECTION_ALIASES = {
    "acceptance criteria",
    "acceptance",
    "criteria",
    "criterios",
    "criterios de aceptacion",
    "criterios aceptacion",
    "definition of done",
    "done",
}

STOPWORDS = {
    "about",
    "acceptance",
    "after",
    "also",
    "and",
    "before",
    "como",
    "con",
    "criteria",
    "debe",
    "deben",
    "del",
    "desde",
    "done",
    "each",
    "este",
    "esta",
    "esto",
    "for",
    "from",
    "have",
    "into",
    "las",
    "los",
    "must",
    "para",
    "por",
    "que",
    "should",
    "the",
    "this",
    "una",
    "uno",
    "with",
}

SECRETISH_NAMES = {
    ".env",
    ".env.local",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
}


@dataclass(frozen=True)
class Criterion:
    index: int
    text: str
    line: int
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceLine:
    source: str
    line: int
    text: str


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return ascii_text.lower()


def tokenize(text: str) -> tuple[str, ...]:
    words = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,}", normalize(text))
    seen: list[str] = []
    for word in words:
        word = word.strip("_-")
        if len(word) < 4 or word in STOPWORDS:
            continue
        if word not in seen:
            seen.append(word)
    return tuple(seen)


def heading_name(line: str) -> str | None:
    match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
    if not match:
        return None
    return normalize(match.group(1).strip(" #:-"))


def is_acceptance_heading(name: str) -> bool:
    return name in SECTION_ALIASES or any(alias in name for alias in SECTION_ALIASES)


def strip_list_marker(line: str) -> str:
    stripped = line.strip()
    stripped = re.sub(r"^[-*+]\s+\[[ xX]\]\s+", "", stripped)
    stripped = re.sub(r"^[-*+]\s+", "", stripped)
    stripped = re.sub(r"^\d+[.)]\s+", "", stripped)
    return stripped.strip()


def parse_acceptance_criteria(task_path: Path) -> list[Criterion]:
    if not task_path.exists():
        raise FileNotFoundError(f"task file not found: {task_path}")
    lines = task_path.read_text(encoding="utf-8").splitlines()
    in_section = False
    candidates: list[tuple[int, str]] = []

    for number, line in enumerate(lines, start=1):
        heading = heading_name(line)
        if heading is not None:
            if in_section:
                break
            in_section = is_acceptance_heading(heading)
            continue
        if not in_section:
            continue
        text = strip_list_marker(line)
        if text:
            candidates.append((number, text))

    criteria: list[Criterion] = []
    for index, (line_number, text) in enumerate(candidates, start=1):
        criteria.append(Criterion(index=index, text=text, line=line_number, tokens=tokenize(text)))
    return criteria


def path_is_secretish(path: Path) -> bool:
    lowered = {part.lower() for part in path.parts}
    return bool(lowered & SECRETISH_NAMES) or any(part.endswith(".pem") for part in lowered)


def read_evidence_files(paths: Iterable[Path]) -> list[EvidenceLine]:
    evidence: list[EvidenceLine] = []
    for path in paths:
        if path_is_secretish(path):
            raise ValueError(f"refusing to read secret-like evidence path: {path}")
        if not path.exists():
            raise FileNotFoundError(f"evidence file not found: {path}")
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if line.strip():
                evidence.append(EvidenceLine(str(path), number, line.strip()))
    return evidence


def run_git_diff(repo: Path, base: str | None) -> str:
    if not (repo / ".git").exists():
        return ""
    args = ["git", "diff", "--no-ext-diff", "--unified=0"]
    if base:
        args.append(base)
    try:
        result = subprocess.run(args, cwd=repo, text=True, capture_output=True, check=False)
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def read_diff(diff_path: Path | None, repo: Path, base: str | None) -> str:
    if diff_path:
        if not diff_path.exists():
            raise FileNotFoundError(f"diff file not found: {diff_path}")
        return diff_path.read_text(encoding="utf-8", errors="replace")
    return run_git_diff(repo, base)


def diff_evidence(diff_text: str, source: str) -> list[EvidenceLine]:
    evidence: list[EvidenceLine] = []
    for number, line in enumerate(diff_text.splitlines(), start=1):
        if not line.strip():
            continue
        evidence.append(EvidenceLine(source, number, line.rstrip()))
    return evidence


def changed_files(diff_text: str) -> list[str]:
    files: list[str] = []
    for line in diff_text.splitlines():
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if match:
            for value in match.groups():
                if value not in files:
                    files.append(value)
            continue
        match = re.match(r"^\+\+\+ b/(.+)$", line)
        if match and match.group(1) != "/dev/null" and match.group(1) not in files:
            files.append(match.group(1))
    return files


def line_hits(tokens: tuple[str, ...], line: EvidenceLine) -> set[str]:
    normalized = normalize(line.text)
    return {token for token in tokens if token in normalized}


def trace(criteria: list[Criterion], evidence: list[EvidenceLine], files: list[str]) -> dict:
    rows = []
    covered = partial = missing = 0
    for criterion in criteria:
        token_hits: set[str] = set()
        candidates: list[tuple[int, EvidenceLine]] = []
        for line in evidence:
            hits = line_hits(criterion.tokens, line)
            if not hits:
                continue
            token_hits.update(hits)
            candidates.append((len(hits), line))

        for path in files:
            hits = {token for token in criterion.tokens if token in normalize(path)}
            if not hits:
                continue
            token_hits.update(hits)
            candidates.append((len(hits), EvidenceLine("changed-files", 0, path)))

        ratio = len(token_hits) / max(len(criterion.tokens), 1)

        snippets: list[dict[str, object]] = []
        seen_snippets: set[tuple[str, int]] = set()
        for _, line in sorted(candidates, key=lambda item: (-item[0], item[1].source, item[1].line)):
            key = (line.source, line.line)
            if key in seen_snippets:
                continue
            seen_snippets.add(key)
            snippets.append({"source": line.source, "line": line.line, "text": line.text[:220]})
            if len(snippets) == 3:
                break

        if len(token_hits) >= 3 and ratio >= 0.45 and snippets:
            status = "covered"
            covered += 1
        elif token_hits and snippets:
            status = "partial"
            partial += 1
        else:
            status = "missing"
            missing += 1

        rows.append(
            {
                "index": criterion.index,
                "line": criterion.line,
                "criterion": criterion.text,
                "status": status,
                "matched_terms": sorted(token_hits),
                "missing_terms": [token for token in criterion.tokens if token not in token_hits],
                "evidence": snippets,
            }
        )

    total = len(criteria)
    score = round(((covered + partial * 0.5) / total) * 100, 1) if total else 0.0
    return {
        "score": score,
        "summary": {
            "total": total,
            "covered": covered,
            "partial": partial,
            "missing": missing,
            "changed_files": files,
        },
        "criteria": rows,
    }


def render_markdown(report: dict, task_path: Path) -> str:
    summary = report["summary"]
    lines = [
        "# Acceptance Trace",
        "",
        f"Task: `{task_path}`",
        f"Score: {report['score']}/100",
        f"Covered: {summary['covered']} | Partial: {summary['partial']} | Missing: {summary['missing']}",
        "",
    ]
    if summary["changed_files"]:
        lines.extend(["## Changed Files", ""])
        lines.extend(f"- `{path}`" for path in summary["changed_files"])
        lines.append("")

    lines.extend(["## Criteria", ""])
    for row in report["criteria"]:
        lines.append(f"### {row['index']}. {row['status'].upper()}")
        lines.append("")
        lines.append(row["criterion"])
        lines.append("")
        if row["matched_terms"]:
            lines.append("Matched terms: " + ", ".join(f"`{term}`" for term in row["matched_terms"]))
        if row["missing_terms"]:
            lines.append("Missing terms: " + ", ".join(f"`{term}`" for term in row["missing_terms"]))
        if row["evidence"]:
            lines.append("")
            lines.append("Evidence:")
            for item in row["evidence"]:
                location = f"{item['source']}:{item['line']}" if item["line"] else str(item["source"])
                lines.append(f"- `{location}` {item['text']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-acceptance-trace",
        description="Trace task acceptance criteria to diff and closeout evidence.",
    )
    parser.add_argument("task", type=Path, help="Markdown task contract containing an Acceptance Criteria section.")
    parser.add_argument("--diff", type=Path, help="Unified diff to use as implementation evidence.")
    parser.add_argument("--repo", type=Path, default=Path("."), help="Repository path for git diff fallback. Default: current directory.")
    parser.add_argument("--base", help="Optional git base ref when --diff is omitted, for example origin/main or HEAD~1.")
    parser.add_argument(
        "--evidence",
        type=Path,
        action="append",
        default=[],
        help="Evidence file such as a closeout, proof packet, command receipt, or CI summary. Can be repeated.",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Output format.")
    parser.add_argument("--min-covered", type=float, default=0.0, help="Minimum weighted coverage score required.")
    parser.add_argument("--strict", action="store_true", help="Fail if any criterion is partial or missing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        criteria = parse_acceptance_criteria(args.task)
        if not criteria:
            parser.error(f"no acceptance criteria found in {args.task}")
        diff_text = read_diff(args.diff, args.repo, args.base)
        evidence = read_evidence_files(args.evidence)
        evidence.extend(diff_evidence(diff_text, str(args.diff) if args.diff else "git diff"))
        report = trace(criteria, evidence, changed_files(diff_text))
    except (FileNotFoundError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report, args.task), end="")

    fails_threshold = report["score"] < args.min_covered
    fails_strict = args.strict and (report["summary"]["partial"] or report["summary"]["missing"])
    return 1 if fails_threshold or fails_strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
