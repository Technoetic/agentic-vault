"""The CI supply-chain audit stays advisory, read-only, pinned and hash-checked.

The `supply-chain-audit` job runs zizmor and pip-audit as warnings. These tests
keep it from turning into a gate or gaining write, OIDC or code-scanning
permissions, keep the existing test jobs blocking as before, and keep the
release integrity guide reachable with commands that name this repository.
pip-audit and its dependencies come from `ci/requirements-audit.txt` in pip's
hash-checking mode (wheels only), and pip-audit reads the exact pins of
`requirements-dev.txt` without installing them, so the job installs nothing
without a hash. The workflow is read as text by indentation; tests use only
the standard library.
"""
from __future__ import annotations

import datetime as dt
import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
README = ROOT / "README.md"
DOCS_INDEX = ROOT / "docs" / "README.md"
INTEGRITY_DOC = ROOT / "docs" / "release-integrity.md"
DEV_REQUIREMENTS = ROOT / "requirements-dev.txt"
AUDIT_REQUIREMENTS_IN = ROOT / "ci" / "requirements-audit.in"
AUDIT_REQUIREMENTS = ROOT / "ci" / "requirements-audit.txt"
AUDIT_JOB = "supply-chain-audit"
BLOCKING_JOBS = ("unittest", "stateful")
AUDIT_ACTIONS = {
    "actions/checkout",
    "actions/setup-python",
    "zizmorcore/zizmor-action",
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
# Release cool-down (cut 2026-10-17): pin releases published at least 14 days earlier.
# zizmor-action v0.7.0 and v0.7.1 were both published 2026-10-15, so the job stays on
# v0.6.4 (published 2026-09-09; lightweight tag -> commit below, resolved with gh api).
# v0.6.4 runs zizmor from a digest-pinned image listed in its support/versions file;
# 1.30.1 (published 2026-09-09) is the newest version listed there.
# pip-audit no longer comes from pypa/gh-action-pip-audit v1.1.0, whose pinned code
# installed pip-audit from PyPI without hashes; the cool-down for the hashed file is
# its pip-compile --uploaded-prior-to cutoff (see the requirements tests below).
COOL_DOWN_PINS = {
    "zizmorcore/zizmor-action": ("cc914d7f3750a2d13d75c7f184a1060aa0e9d482", "v0.6.4"),
}
ZIZMOR_VERSION = "'1.30.1'"
INSTALL_STEP_ID = "pip-audit-install"
AUDIT_STEP_IDS = ("zizmor", INSTALL_STEP_ID, "pip-audit")
# pip "Secure installs": hash-checking mode and no source distributions.
HASHED_INSTALL = "python -m pip install --require-hashes --only-binary :all: -r ci/requirements-audit.txt"
# --no-deps reads the exact pins; --disable-pip stops pip-audit from creating a
# temporary venv in which it would run pip without hashes.
PIN_ONLY_AUDIT = "python -m pip_audit --no-deps --disable-pip --progress-spinner off -r requirements-dev.txt"
AUDIT_GATE = "${{ steps.pip-audit-install.outcome == 'success' }}"
# The v0.24.0 warning lines stay verbatim; the install outcome only adds one more.
REPORT_WARNINGS_V0240 = (
    'echo "::warning title=zizmor (advisory)::findings or tool error; see the zizmor step log"',
    'echo "::warning title=pip-audit (advisory)::findings or tool error; see the pip-audit step log"',
)
# Commands of the blocking jobs at v0.24.0 (d2b9315). Each must still run, unchanged and
# in this order; steps may be added around them. Change a gate here deliberately.
BLOCKING_RUNS_V0240 = {
    "unittest": (
        "python -m unittest discover -s tests -v",
        "python scripts/evaluate_recall.py",
        "python scripts/evaluate_recall.py --fixture tests/fixtures/recall_advanced --compare"
        " --max-no-answer-false-positive-rate 0 --max-forbidden-exposure-rate 0 --max-stale-top-1-rate 0",
        "python scripts/evaluate_memory_workflows.py --backend deterministic",
        "python scripts/evaluate_korean_memory.py",
        "python scripts/evaluate_tool_safety.py --backend scripted-control",
        "python scripts/evaluate_recall.py --fixture tests/fixtures/recall_hard --compare --expand-query"
        " --query-mapping 00-meta/query-map.txt --top-k 3 --max-tokens 1500"
        " --max-no-answer-false-positive-rate 0.25 --max-stale-top-1-rate 0",
        "python scripts/evaluate_recall.py --fixture tests/fixtures/recall_hard --compare --expand-query"
        " --query-mapping 00-meta/query-map.txt --top-k 5 --max-tokens 1500"
        " --max-no-answer-false-positive-rate 0.25 --max-stale-top-1-rate 0",
        "python -m compileall -q hooks skills/agentic-vault/scripts scripts",
        "git diff --check 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD",
    ),
    "stateful": (
        "python -m pip install -r requirements-dev.txt",
        "python tests/test_stateful_memory_lifecycle.py",
    ),
}
EXACT_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][0-9A-Za-z.+!-]*)")
SHA256_OPTION = re.compile(r"--hash=sha256:[0-9a-f]{64}")
ISO_UTC = r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
COMPILE_FLAGS = (
    "--allow-unsafe",
    "--generate-hashes",
    "--strip-extras",
    "--output-file=ci/requirements-audit.txt",
    "ci/requirements-audit.in",
)
COOL_DOWN_DAYS = 14
IMMUTABLE_RELEASES_DOC = (
    "https://docs.github.com/en/code-security/supply-chain-security/"
    "understanding-your-software-supply-chain/immutable-releases"
)
PIP_SECURE_INSTALLS_DOC = "https://pip.pypa.io/en/stable/topics/secure-installs/"
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


def step_with_id(job_lines: list[str], step_id: str) -> list[str]:
    matches = [step for step in steps(job_lines) if scalars(step, 8).get("id") == step_id]
    if len(matches) != 1:
        raise AssertionError(f"expected one step with id {step_id}, found {len(matches)}")
    return matches[0]


def run_lines(step: list[str]) -> list[str]:
    """Return the shell lines of a step's ``run:`` (inline value or literal block)."""
    for line in step:
        if indent_of(line) != 8 or not line.strip().startswith("run:"):
            continue
        value = strip_comment(line).strip()[len("run:"):].strip()
        if value == "|":
            return [nested.strip() for nested in block_after(step, line)]
        return [value]
    return []


def requirement_lines(text: str) -> list[str]:
    """Return a requirements file's logical lines: comments dropped, continuations joined."""
    logical: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1].strip() + " "
            continue
        logical.append((pending + line).strip())
        pending = ""
    if pending.strip():
        logical.append(pending.strip())
    return logical


def utc(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def option_names(command: str) -> set[str]:
    """Return the ``--option`` names of a command line, with ``=value`` dropped."""
    return {token.split("=", 1)[0] for token in command.split() if token.startswith("--")}


def header_comments(text: str) -> list[str]:
    """Return the comment lines before the first requirement."""
    header = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        header.append(line)
    return header


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
        audit_steps = [("zizmor", zizmor)] + [(step_id, step_with_id(audit, step_id))
                                              for step_id in AUDIT_STEP_IDS[1:]]
        for name, step in audit_steps:
            with self.subTest(step=name):
                self.assertEqual(scalars(step, 8).get("continue-on-error"), "true")
        self.assertEqual(scalars(zizmor, 10).get("advanced-security"), "false")
        checkout = step_using(audit, "actions/checkout")
        self.assertEqual(scalars(checkout, 10).get("persist-credentials"), "false")

    def test_audit_actions_respect_the_release_cool_down(self) -> None:
        audit = self.audit()
        for action, (sha, tag) in COOL_DOWN_PINS.items():
            with self.subTest(action=action):
                step = step_using(audit, action)
                uses = [strip_comment(line).strip() for line in step if "uses:" in line]
                self.assertEqual(uses, [f"uses: {action}@{sha}"])
                self.assertTrue(any(line.strip() == f"uses: {action}@{sha} # {tag}" for line in step))
        zizmor = step_using(audit, "zizmorcore/zizmor-action")
        self.assertEqual(scalars(zizmor, 10).get("version"), ZIZMOR_VERSION)

    def test_pip_audit_installs_from_hash_checked_wheels_only(self) -> None:
        audit = self.audit()
        install = step_with_id(audit, INSTALL_STEP_ID)
        self.assertEqual(run_lines(install), [HASHED_INSTALL])
        installs = [line for step in steps(audit) for line in run_lines(step) if "pip install" in line]
        self.assertEqual(installs, [HASHED_INSTALL], "every install in the audit job is hash-checked")
        # The hashed file resolves dependency markers for the job's Python 3.12.
        python = step_using(audit, "actions/setup-python")
        self.assertEqual(scalars(python, 10).get("python-version"), "'3.12'")

    def test_pip_audit_reads_pins_without_installing(self) -> None:
        step = step_with_id(self.audit(), "pip-audit")
        self.assertEqual(run_lines(step), [PIN_ONLY_AUDIT])
        self.assertEqual(scalars(step, 8).get("if"), AUDIT_GATE)
        self.assertNotIn("uses", scalars(step, 8))

    def test_step_outcomes_surface_as_warning_annotations(self) -> None:
        audit = self.audit()
        self.assertEqual(scalars(step_using(audit, "zizmorcore/zizmor-action"), 8).get("id"), "zizmor")
        for step_id in AUDIT_STEP_IDS:
            with self.subTest(step=step_id):
                step_with_id(audit, step_id)
        reporters = []
        for step in steps(audit):
            headers = {strip_comment(line).strip(): line for line in step if indent_of(line) == 8}
            if "env:" not in headers or "run: |" not in headers:
                continue
            env = scalars(block_after(step, headers["env:"]), 10)
            run = "\n".join(line.strip() for line in block_after(step, headers["run: |"]))
            outcome_vars = {step_id: [name for name, value in env.items()
                                      if value == f"${{{{ steps.{step_id}.outcome }}}}"]
                            for step_id in AUDIT_STEP_IDS}
            if all(outcome_vars.values()):
                reporters.append((outcome_vars, run))
        self.assertEqual(len(reporters), 1, "one step must read every audit outcome from env")
        outcome_vars, run = reporters[0]
        self.assertIn("::warning", run)
        for step_id, names in outcome_vars.items():
            with self.subTest(outcome=step_id):
                self.assertTrue(any(f"${{{name}}}" in run for name in names))
        self.assertNotIn("exit 1", run)

    def test_report_keeps_the_v0240_warning_lines(self) -> None:
        report = [line for step in steps(self.audit()) for line in run_lines(step)]
        for warning in REPORT_WARNINGS_V0240:
            with self.subTest(warning=warning):
                self.assertEqual(report.count(warning), 1)

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

    def test_blocking_jobs_keep_their_v0240_commands(self) -> None:
        for name, expected in BLOCKING_RUNS_V0240.items():
            commands = [line for step in steps(self.jobs[name]) for line in run_lines(step)]
            position = 0
            for command in expected:
                with self.subTest(job=name, command=command[:60]):
                    self.assertIn(command, commands[position:])
                    position = commands.index(command, position) + 1

    def test_audit_tooling_stays_out_of_blocking_jobs(self) -> None:
        for name in BLOCKING_JOBS:
            for line in self.jobs[name]:
                for marker in ("ci/requirements-audit", "pip_audit", "pip-audit"):
                    with self.subTest(job=name, marker=marker):
                        self.assertNotIn(marker, line)


class AuditRequirementsTests(unittest.TestCase):
    """`ci/requirements-audit.txt` is complete, exactly pinned and fully hashed."""

    def setUp(self) -> None:
        self.source = AUDIT_REQUIREMENTS_IN.read_text(encoding="utf-8")
        self.text = AUDIT_REQUIREMENTS.read_text(encoding="utf-8")

    def test_files_are_ascii(self) -> None:
        # pip decodes requirement files with the locale codec when no BOM is present.
        for path, text in ((AUDIT_REQUIREMENTS_IN, self.source), (AUDIT_REQUIREMENTS, self.text)):
            with self.subTest(path=path.name):
                self.assertTrue(text.isascii())

    def test_source_pins_one_pip_audit_release(self) -> None:
        lines = requirement_lines(self.source)
        self.assertEqual(len(lines), 1)
        match = EXACT_PIN.fullmatch(lines[0])
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "pip-audit")

    def test_every_requirement_is_pinned_and_hashed(self) -> None:
        names = []
        for line in requirement_lines(self.text):
            with self.subTest(requirement=line.split(" ", 1)[0]):
                self.assertFalse(line.startswith("-"), "no options such as index URLs or -e")
                pin, *options = line.split()
                match = EXACT_PIN.fullmatch(pin)
                self.assertIsNotNone(match)
                self.assertTrue(options)
                for option in options:
                    self.assertIsNotNone(SHA256_OPTION.fullmatch(option), option)
                names.append(re.sub(r"[-_.]+", "-", match.group(1)).lower())
        self.assertIn("pip-audit", names)
        self.assertEqual(len(names), len(set(names)))

    def test_compiled_pin_matches_the_source(self) -> None:
        source = requirement_lines(self.source)[0]
        pins = [line.split()[0] for line in requirement_lines(self.text)]
        self.assertIn(source, pins)

    def test_header_records_the_command_tools_and_cool_down(self) -> None:
        header = header_comments(self.text)
        commands = [line for line in header if "pip-compile " in line and "--generate-hashes" in line]
        self.assertEqual(len(commands), 1)
        for flag in COMPILE_FLAGS:
            with self.subTest(flag=flag):
                self.assertIn(flag, commands[0].split())
        self.assertNotIn("--no-index", commands[0])
        tools = [line for line in header if line.startswith("# Tool versions:")]
        self.assertEqual(len(tools), 1)
        self.assertRegex(tools[0], r"\bpip \d+\.\d+(\.\d+)?")
        self.assertRegex(tools[0], r"\bpip-tools \d+\.\d+(\.\d+)?")
        joined = "\n".join(header)
        cutoff = re.search(r"--uploaded-prior-to=" + ISO_UTC, commands[0])
        generated = re.search(r"Generated " + ISO_UTC, joined)
        self.assertIsNotNone(cutoff)
        self.assertIsNotNone(generated)
        age = utc(generated.group(1)) - utc(cutoff.group(1))
        self.assertGreaterEqual(age, dt.timedelta(days=COOL_DOWN_DAYS))

    def test_dev_requirements_pin_exact_versions(self) -> None:
        # pip-audit --no-deps --disable-pip refuses unpinned lines and audits only these pins.
        lines = requirement_lines(DEV_REQUIREMENTS.read_text(encoding="utf-8"))
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(line=line):
                self.assertIsNotNone(EXACT_PIN.fullmatch(line))


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

    def test_documents_the_hashed_audit_install_and_refresh(self) -> None:
        for literal in (PIP_SECURE_INSTALLS_DOC, HASHED_INSTALL, PIN_ONLY_AUDIT, "--uploaded-prior-to",
                        "ci/requirements-audit.in", "git diff -- ci/", f"{COOL_DOWN_DAYS}일"):
            with self.subTest(literal=literal):
                self.assertIn(literal, self.doc)

    def test_refresh_command_matches_the_recorded_command(self) -> None:
        # The documented refresh adds only --upgrade to the options the header records.
        documented = [line.strip() for line in self.doc.splitlines() if "pip-compile --upgrade " in line]
        self.assertEqual(len(documented), 1)
        header = header_comments(AUDIT_REQUIREMENTS.read_text(encoding="utf-8"))
        recorded = [line for line in header if "pip-compile " in line and "--generate-hashes" in line]
        self.assertEqual(len(recorded), 1)
        self.assertEqual(option_names(documented[0]) - {"--upgrade"}, option_names(recorded[0]))
        for operand in ("ci/requirements-audit.in", "ci/requirements-audit.txt"):
            with self.subTest(operand=operand):
                self.assertIn(operand, documented[0])


if __name__ == "__main__":
    unittest.main()
