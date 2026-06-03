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


@dataclass(frozen=True)
class ProofPacketAudit:
    path: str
    status: str
    verdict: str
    changed_files: tuple[str, ...]
    checks: tuple[str, ...]
    issues: tuple[dict[str, str], ...]


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


def normalize_proof_path(path: str) -> str:
    normalized = path.strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def proof_issue(severity: str, code: str, message: str, evidence: str, recommendation: str) -> dict[str, str]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "evidence": evidence,
        "recommendation": recommendation,
    }


def audit_proof_packet(path: Path, diff_files: list[str]) -> ProofPacketAudit:
    issues: list[dict[str, str]] = []
    verdict = ""
    packet_files: list[str] = []
    checks: list[str] = []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        return ProofPacketAudit(
            str(path),
            "fail",
            "",
            (),
            (),
            (
                proof_issue(
                    "high",
                    "proof_packet_unreadable",
                    f"Proof packet could not be read: {error}",
                    str(path),
                    "Pass a readable agent-proof-packet.v1 JSON file.",
                ),
            ),
        )
    except json.JSONDecodeError as error:
        return ProofPacketAudit(
            str(path),
            "fail",
            "",
            (),
            (),
            (
                proof_issue(
                    "high",
                    "proof_packet_invalid_json",
                    f"Proof packet is not valid JSON: {error}",
                    str(path),
                    "Regenerate the proof packet as valid JSON.",
                ),
            ),
        )

    if payload.get("schema_version") != "agent-proof-packet.v1":
        issues.append(
            proof_issue(
                "high",
                "proof_packet_wrong_schema",
                "Proof packet schema_version is not agent-proof-packet.v1.",
                str(path),
                "Use an agent-proof-packet.v1 JSON proof packet.",
            )
        )

    verdict = str(payload.get("verdict", "")).strip()
    if verdict != "complete":
        issues.append(
            proof_issue(
                "high",
                "proof_packet_incomplete",
                f"Proof packet verdict is {verdict or 'missing'}, not complete.",
                str(path),
                "Resolve missing evidence before using the packet as acceptance evidence.",
            )
        )

    raw_changed_files = payload.get("changed_files")
    if not isinstance(raw_changed_files, list) or not raw_changed_files:
        issues.append(
            proof_issue(
                "high",
                "proof_packet_missing_changed_files",
                "Proof packet has no changed-file evidence.",
                str(path),
                "Regenerate the packet from the actual task diff.",
            )
        )
    else:
        for item in raw_changed_files:
            if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"].strip():
                packet_files.append(normalize_proof_path(item["path"]))
            else:
                issues.append(
                    proof_issue(
                        "high",
                        "proof_packet_invalid_changed_file",
                        "Proof packet contains an invalid changed_files entry.",
                        str(path),
                        "Keep changed_files entries shaped as objects with a path.",
                    )
                )

    raw_checks = payload.get("checks")
    check_statuses: list[str] = []
    if not isinstance(raw_checks, list) or not raw_checks:
        issues.append(
            proof_issue(
                "high",
                "proof_packet_missing_checks",
                "Proof packet has no checks.",
                str(path),
                "Include at least one passing check before using packet evidence.",
            )
        )
    else:
        for item in raw_checks:
            if not isinstance(item, dict):
                issues.append(
                    proof_issue(
                        "high",
                        "proof_packet_invalid_check",
                        "Proof packet contains an invalid check entry.",
                        str(path),
                        "Keep checks shaped as JSON objects.",
                    )
                )
                continue
            name = str(item.get("name", "")).strip()
            status = str(item.get("status", "")).strip()
            detail = str(item.get("detail", "")).strip()
            if not name or not status:
                issues.append(
                    proof_issue(
                        "high",
                        "proof_packet_invalid_check",
                        "Proof packet contains a nameless or statusless check.",
                        str(path),
                        "Keep checks shaped as objects with name and status.",
                    )
                )
                continue
            checks.append(f"{status}: {name}" + (f" - {detail}" if detail else ""))
            check_statuses.append(status)

    if check_statuses and any(status == "fail" for status in check_statuses):
        issues.append(
            proof_issue(
                "high",
                "proof_packet_failing_checks",
                "Proof packet includes failing checks.",
                str(path),
                "Do not use failing packet checks as acceptance evidence.",
            )
        )
    if not any(status == "pass" for status in check_statuses):
        issues.append(
            proof_issue(
                "high",
                "proof_packet_no_passing_checks",
                "Proof packet has no passing checks.",
                str(path),
                "Add passing verification evidence before using the packet.",
            )
        )

    missing_evidence = payload.get("missing_evidence")
    if isinstance(missing_evidence, list) and missing_evidence:
        issues.append(
            proof_issue(
                "high",
                "proof_packet_missing_evidence",
                "Proof packet still has missing evidence.",
                ", ".join(str(item) for item in missing_evidence[:5]),
                "Resolve missing evidence before tracing acceptance criteria.",
            )
        )

    open_questions = payload.get("open_questions")
    if isinstance(open_questions, list) and open_questions:
        issues.append(
            proof_issue(
                "medium",
                "proof_packet_open_questions",
                "Proof packet still has open questions.",
                ", ".join(str(item) for item in open_questions[:5]),
                "Carry open questions into the trace review instead of treating the task as fully covered.",
            )
        )

    diff_file_set = set(diff_files)
    packet_file_set = set(packet_files)
    if diff_file_set and packet_file_set and diff_file_set != packet_file_set:
        issues.append(
            proof_issue(
                "high",
                "proof_packet_diff_mismatch",
                "Proof packet changed files do not match the provided diff.",
                f"diff={sorted(diff_file_set)} packet={sorted(packet_file_set)}",
                "Regenerate the packet from the exact task diff before tracing acceptance.",
            )
        )

    status = "fail" if any(issue["severity"] == "high" for issue in issues) else "pass"
    return ProofPacketAudit(str(path), status, verdict, tuple(packet_files), tuple(checks), tuple(issues))


def proof_packet_evidence(packets: list[ProofPacketAudit]) -> list[EvidenceLine]:
    evidence: list[EvidenceLine] = []
    for packet in packets:
        if packet.status != "pass" or packet.verdict != "complete":
            continue
        source = f"{packet.path}:proof-packet"
        evidence.append(EvidenceLine(source, 0, f"Proof packet verdict {packet.verdict}."))
        for file_path in packet.changed_files:
            evidence.append(EvidenceLine(source, 0, f"Changed file {file_path}."))
        for check in packet.checks:
            evidence.append(EvidenceLine(source, 0, f"Check {check}."))
    return evidence


def proof_packets_to_json(packets: list[ProofPacketAudit]) -> list[dict[str, object]]:
    return [
        {
            "path": packet.path,
            "status": packet.status,
            "verdict": packet.verdict,
            "changed_files": list(packet.changed_files),
            "checks": list(packet.checks),
            "issues": list(packet.issues),
        }
        for packet in packets
    ]


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

    if report.get("proof_packets"):
        lines.extend(["## Proof Packets", ""])
        for packet in report["proof_packets"]:
            lines.append(
                f"- `{packet['status']}` `{packet['path']}`: verdict `{packet['verdict'] or 'missing'}`, "
                f"{len(packet['changed_files'])} files, {len(packet['checks'])} checks"
            )
            for issue in packet["issues"]:
                lines.append(f"  - `{issue['severity']}` `{issue['code']}`: {issue['message']}")
                lines.append(f"    Evidence: {issue['evidence']}")
                lines.append(f"    Next: {issue['recommendation']}")
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
    parser.add_argument(
        "--proof-packet",
        type=Path,
        action="append",
        default=[],
        help="agent-proof-packet.v1 JSON file to verify against the diff and use as structured evidence. Can be repeated.",
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
        files = changed_files(diff_text)
        proof_packets = [audit_proof_packet(path, files) for path in args.proof_packet]
        evidence = read_evidence_files(args.evidence)
        evidence.extend(diff_evidence(diff_text, str(args.diff) if args.diff else "git diff"))
        evidence.extend(proof_packet_evidence(proof_packets))
        report = trace(criteria, evidence, files)
        report["proof_packets"] = proof_packets_to_json(proof_packets)
    except (FileNotFoundError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report, args.task), end="")

    fails_threshold = report["score"] < args.min_covered
    fails_strict = args.strict and (report["summary"]["partial"] or report["summary"]["missing"])
    fails_proof_packet = any(
        issue["severity"] == "high"
        for packet in report.get("proof_packets", [])
        for issue in packet["issues"]
    )
    return 1 if fails_threshold or fails_strict or fails_proof_packet else 0


if __name__ == "__main__":
    raise SystemExit(main())
