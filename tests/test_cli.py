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


if __name__ == "__main__":
    unittest.main()
