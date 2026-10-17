"""The CI supply-chain audit stays advisory, read-only and pinned.

The `supply-chain-audit` job runs zizmor and pip-audit as warnings. These tests
keep it from turning into a gate or gaining write, OIDC or code-scanning
permissions, keep the existing test jobs blocking as before, and keep the
release integrity guide reachable with commands that name this repository.
The workflow is read as text by indentation; tests use only the standard library.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
README = ROOT / "README.md"
DOCS_INDEX = ROOT / "docs" / "README.md"
INTEGRITY_DOC = ROOT / "docs" / "release-integrity.md"
AUDIT_JOB = "supply-chain-audit"
BLOCKING_JOBS = ("unittest", "stateful")
AUDIT_ACTIONS = {
    "actions/checkout",
    "actions/setup-python",
    "zizmorcore/zizmor-action",
    "pypa/gh-action-pip-audit",
}
# Gate commands that must stay in blocking jobs; the advisory job may not run them.
GATE_MARKERS = (
    "unittest",
    "scripts/evaluate_",
    "compileall",
    "git diff --check",
    "test_stateful_memory_lifecycle",
)
PINNED_USES = re.compile(r"uses: ([\w.-]+/[\w.-]+)@([0-9a-f]{40}) # v\d+(?:\.\d+)*")
IMMUTABLE_RELEASES_DOC = (
    "https://docs.github.com/en/code-security/supply-chain-security/"
    "understanding-your-software-supply-chain/immutable-releases"
)
VERIFY_COMMANDS = (
    "gh release verify TAG -R Technoetic/agentic-vault",
    "gh release verify-asset TAG agentic-vault-VERSION.zip -R Technoetic/agentic-vault",
)


def strip_comment(line: str) -> str:
    """Drop a trailing YAML comment; the workflow quotes no `#` characters."""
    return line.split(" #", 1)[0].rstrip()


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def block_after(lines: list[str], header: str) -> list[str]:
    """Return the non-blank lines nested under the exact ``header`` line."""
    start = lines.index(header)
    level = indent_of(header)
    nested = []
    for line in lines[start + 1:]:
        if not line.strip():
            continue
        if indent_of(line) <= level:
            break
        nested.append(line)
    return nested


def scalars(lines: list[str], level: int) -> dict[str, str]:
    """Return ``key: value`` pairs written at exactly ``level`` spaces."""
    pairs = {}
    for line in lines:
        if indent_of(line) != level:
            continue
        key, sep, value = strip_comment(line).strip().partition(":")
        if sep and value.strip():
            pairs[key] = value.strip()
    return pairs


def jobs(text: str) -> dict[str, list[str]]:
    """Split the top-level ``jobs:`` mapping into job id -> nested lines."""
    result: dict[str, list[str]] = {}
    current = None
    for line in block_after(text.splitlines(), "jobs:"):
        match = re.fullmatch(r"  ([A-Za-z0-9_-]+):", strip_comment(line))
        if match:
            current = match.group(1)
            result[current] = []
        elif current is not None:
            result[current].append(line)
    return result


def steps(job_lines: list[str]) -> list[list[str]]:
    """Split a job's ``steps:`` list into one line list per step."""
    result: list[list[str]] = []
    for line in block_after(job_lines, "    steps:"):
        if line.startswith("      - "):
            result.append([])
        if result:
            result[-1].append(line)
    return result


def step_using(job_lines: list[str], action: str) -> list[str]:
    matches = [step for step in steps(job_lines) if any(f"uses: {action}@" in line for line in step)]
    if len(matches) != 1:
        raise AssertionError(f"expected one step using {action}, found {len(matches)}")
    return matches[0]


def relative_links(text: str) -> list[str]:
    """Return Markdown link targets that point into the repository."""
    targets = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
            continue
        targets.append(unquote(target.split("#", 1)[0]))
    return targets


class SupplyChainAuditJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.jobs = jobs(cls.text)

    def audit(self) -> list[str]:
        self.assertIn(AUDIT_JOB, self.jobs)
        return self.jobs[AUDIT_JOB]

    def test_audit_job_is_advisory_and_bounded(self) -> None:
        job = scalars(self.audit(), 4)
        self.assertEqual(job.get("continue-on-error"), "true")
        self.assertEqual(job.get("runs-on"), "ubuntu-latest")
        self.assertEqual(job.get("timeout-minutes"), "10")

    def test_audit_job_reads_contents_only(self) -> None:
        audit = self.audit()
        self.assertEqual(scalars(block_after(audit, "    permissions:"), 6), {"contents": "read"})
        keys = {strip_comment(line).strip().partition(":")[0] for line in audit}
        for forbidden in ("id-token", "security-events", "attestations"):
            with self.subTest(permission=forbidden):
                self.assertNotIn(forbidden, keys)

    def test_audit_actions_are_pinned_to_full_commit_shas(self) -> None:
        used = set()
        for line in self.audit():
            if "uses:" not in line:
                continue
            match = PINNED_USES.search(line)
            with self.subTest(line=line.strip()):
                self.assertIsNotNone(match)
            if match:
                used.add(match.group(1))
        self.assertEqual(used, AUDIT_ACTIONS)

    def test_audits_continue_and_upload_nothing(self) -> None:
        audit = self.audit()
        zizmor = step_using(audit, "zizmorcore/zizmor-action")
        pip_audit = step_using(audit, "pypa/gh-action-pip-audit")
        for name, step in (("zizmor", zizmor), ("pip-audit", pip_audit)):
            with self.subTest(step=name):
                self.assertEqual(scalars(step, 8).get("continue-on-error"), "true")
        self.assertEqual(scalars(zizmor, 10).get("advanced-security"), "false")
        self.assertEqual(scalars(pip_audit, 10).get("inputs"), "requirements-dev.txt")
        checkout = step_using(audit, "actions/checkout")
        self.assertEqual(scalars(checkout, 10).get("persist-credentials"), "false")

    def test_gate_commands_stay_out_of_the_advisory_job(self) -> None:
        for line in self.audit():
            for marker in GATE_MARKERS:
                with self.subTest(marker=marker):
                    self.assertNotIn(marker, line)

    def test_existing_jobs_stay_blocking_under_read_only_workflow(self) -> None:
        lines = self.text.splitlines()
        self.assertEqual(scalars(block_after(lines, "permissions:"), 2), {"contents": "read"})
        for name in BLOCKING_JOBS:
            with self.subTest(job=name):
                self.assertIn(name, self.jobs)
                directives = {strip_comment(line).strip().partition(":")[0] for line in self.jobs[name]}
                self.assertNotIn("continue-on-error", directives)


class ReleaseIntegrityDocTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = INTEGRITY_DOC.read_text(encoding="utf-8")

    def test_doc_is_indexed_and_linked_from_readme(self) -> None:
        self.assertIn("](release-integrity.md)", DOCS_INDEX.read_text(encoding="utf-8"))
        self.assertIn("](docs/release-integrity.md)", README.read_text(encoding="utf-8"))

    def test_relative_links_resolve(self) -> None:
        targets = relative_links(self.doc)
        self.assertTrue(targets)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((INTEGRITY_DOC.parent / target).exists(), target)

    def test_consumer_commands_name_this_repository(self) -> None:
        lines = self.doc.splitlines()
        for command in VERIFY_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, lines)

    def test_cites_github_immutable_release_documentation(self) -> None:
        self.assertIn(IMMUTABLE_RELEASES_DOC, self.doc)


if __name__ == "__main__":
    unittest.main()
