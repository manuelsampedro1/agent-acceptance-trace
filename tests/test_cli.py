from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from agent_acceptance_trace.cli import parse_acceptance_criteria, trace


ROOT = Path(__file__).resolve().parents[1]


def write_proof_packet(
    path: Path,
    *,
    verdict: str = "complete",
    changed_files: list[str] | None = None,
    checks: list[dict[str, str]] | None = None,
) -> None:
    changed_files = changed_files or ["src/app.py"]
    checks = checks or [{"name": "implementation tests", "status": "pass", "detail": "make smoke command"}]
    payload = {
        "schema_version": "agent-proof-packet.v1",
        "title": "Acceptance proof",
        "verdict": verdict,
        "changed_files": [
            {"path": file_path, "status": "modified", "additions": 1, "deletions": 0}
            for file_path in changed_files
        ],
        "checks": checks,
        "risks": [],
        "decisions": [],
        "evidence_files": [],
        "command_receipts": [],
        "open_questions": [],
        "missing_evidence": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class AcceptanceTraceTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "agent_acceptance_trace", *args],
            cwd=ROOT,
            env={"PYTHONPATH": str(ROOT / "src")},
            text=True,
            capture_output=True,
            check=False,
        )

    def test_parses_acceptance_criteria_section(self) -> None:
        criteria = parse_acceptance_criteria(ROOT / "examples" / "task-contract.md")
        self.assertEqual(len(criteria), 3)
        self.assertIn("add todo items", criteria[0].text)
        self.assertIn("priority", criteria[0].tokens)

    def test_markdown_trace_covers_fixture(self) -> None:
        result = self.run_cli(
            "examples/task-contract.md",
            "--diff",
            "examples/sample.diff",
            "--evidence",
            "examples/closeout.md",
            "--min-covered",
            "80",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Score:", result.stdout)
        self.assertIn("COVERED", result.stdout)
        self.assertIn("todo_cli.py", result.stdout)

    def test_json_output_contains_summary(self) -> None:
        result = self.run_cli(
            "examples/task-contract.md",
            "--diff",
            "examples/sample.diff",
            "--evidence",
            "examples/closeout.md",
            "--format",
            "json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["summary"]["total"], 3)
        self.assertGreaterEqual(payload["score"], 80)

    def test_threshold_fails_on_missing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.md"
            task.write_text(
                textwrap.dedent(
                    """
                    # Task

                    ## Acceptance Criteria

                    - The CLI exports CSV reports for billing invoices.
                    - The CLI sends Slack notifications after publish.
                    """
                ).strip(),
                encoding="utf-8",
            )
            result = self.run_cli(str(task), "--min-covered", "100")
            self.assertEqual(result.returncode, 1)
            self.assertIn("MISSING", result.stdout)

    def test_refuses_secret_like_evidence_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.md"
            task.write_text("# Task\n\n## Acceptance Criteria\n\n- Evidence exists.\n", encoding="utf-8")
            secret = Path(tmp) / ".env"
            secret.write_text("TOKEN=value\n", encoding="utf-8")
            result = self.run_cli(str(task), "--evidence", str(secret))
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing to read", result.stderr)

    def test_trace_marks_partial_when_only_path_evidence_exists(self) -> None:
        criteria = parse_acceptance_criteria(ROOT / "examples" / "task-contract.md")
        report = trace(criteria[:1], [], ["todo_cli.py"])
        self.assertEqual(report["criteria"][0]["status"], "partial")

    def test_proof_packet_can_support_acceptance_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.md"
            task.write_text(
                textwrap.dedent(
                    """
                    # Task

                    ## Acceptance Criteria

                    - The implementation includes tests and a smoke command.
                    """
                ).strip(),
                encoding="utf-8",
            )
            diff = Path(tmp) / "change.diff"
            diff.write_text(
                textwrap.dedent(
                    """
                    diff --git a/src/app.py b/src/app.py
                    --- a/src/app.py
                    +++ b/src/app.py
                    @@ -1 +1 @@
                    -old
                    +new
                    """
                ).strip(),
                encoding="utf-8",
            )
            packet = Path(tmp) / "proof-packet.json"
            write_proof_packet(packet)

            result = self.run_cli(str(task), "--diff", str(diff), "--proof-packet", str(packet), "--min-covered", "100")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Proof Packets", result.stdout)
        self.assertIn("COVERED", result.stdout)

    def test_incomplete_proof_packet_fails_even_when_text_evidence_covers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.md"
            task.write_text(
                textwrap.dedent(
                    """
                    # Task

                    ## Acceptance Criteria

                    - The CLI exports billing invoice CSV reports.
                    """
                ).strip(),
                encoding="utf-8",
            )
            diff = Path(tmp) / "change.diff"
            diff.write_text(
                textwrap.dedent(
                    """
                    diff --git a/src/app.py b/src/app.py
                    --- a/src/app.py
                    +++ b/src/app.py
                    @@ -1 +1 @@
                    -old
                    +new
                    """
                ).strip(),
                encoding="utf-8",
            )
            closeout = Path(tmp) / "closeout.md"
            closeout.write_text("The CLI exports billing invoice CSV reports.\n", encoding="utf-8")
            packet = Path(tmp) / "proof-packet.json"
            write_proof_packet(packet, verdict="needs-review")

            result = self.run_cli(
                str(task),
                "--diff",
                str(diff),
                "--evidence",
                str(closeout),
                "--proof-packet",
                str(packet),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("proof_packet_incomplete", result.stdout)

    def test_proof_packet_must_match_diff_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.md"
            task.write_text("# Task\n\n## Acceptance Criteria\n\n- Tests pass.\n", encoding="utf-8")
            diff = Path(tmp) / "change.diff"
            diff.write_text(
                textwrap.dedent(
                    """
                    diff --git a/src/app.py b/src/app.py
                    --- a/src/app.py
                    +++ b/src/app.py
                    @@ -1 +1 @@
                    -old
                    +new
                    diff --git a/tests/test_app.py b/tests/test_app.py
                    --- a/tests/test_app.py
                    +++ b/tests/test_app.py
                    @@ -1 +1 @@
                    -old
                    +new
                    """
                ).strip(),
                encoding="utf-8",
            )
            packet = Path(tmp) / "proof-packet.json"
            write_proof_packet(packet, changed_files=["src/app.py"])

            result = self.run_cli(str(task), "--diff", str(diff), "--proof-packet", str(packet))

        self.assertEqual(result.returncode, 1)
        self.assertIn("proof_packet_diff_mismatch", result.stdout)

    def test_json_output_contains_proof_packet_status(self) -> None:
        result = self.run_cli(
            "examples/task-contract.md",
            "--diff",
            "examples/sample.diff",
            "--evidence",
            "examples/closeout.md",
            "--proof-packet",
            "examples/proof-packet.json",
            "--format",
            "json",
            "--min-covered",
            "80",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["proof_packets"][0]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
