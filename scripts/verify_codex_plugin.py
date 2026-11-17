#!/usr/bin/env python3
"""Check native Codex installation/discovery in a disposable home; no model turn."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "agentic-vault@agentic-vault-local"
WORKSPACE_PREFIX = "agentic-vault-codex-"
WORKSPACE_RANDOM_LENGTH = 8  # the characters tempfile adds to the prefix
PACKAGE_FOLDER = "agentic-vault"
HOME_FOLDER = "codex-home"
COPY_IGNORE = shutil.ignore_patterns(".git", ".worktrees", "worktrees", ".superpowers", "__pycache__", "*.pyc")


# --- long-path helpers (keep this block identical in the standalone tools that carry it) ---
PLAIN_PATH_LIMIT = 240  # the margin below Windows MAX_PATH (260) that vault_evidence.fs_path uses


def _extended(path) -> Path:
    """The Windows extended-length form of an absolute path; any other path, and any other system, unchanged."""
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return Path(path)
    if re.match(r"^[A-Za-z]:\\", text):
        return Path("\\\\?\\" + text)
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path(path)


def _plain(path) -> Path:
    """The path without the extended-length prefix, for messages, reports and comparisons.

    Only a drive or UNC path loses the prefix; another extended-length path (a volume GUID or a
    container mapping, which Path.resolve() can return) has no plain form and is kept as it is.
    """
    text = str(path)
    if os.name == "nt" and text.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + text[8:])
    if os.name == "nt" and re.match(r"^\\\\\?\\[A-Za-z]:\\", text):
        return Path(text[4:])
    return Path(path)


def _workspace(root, reserve: int) -> Path:
    """root, in extended-length form when root plus `reserve` more characters reaches the plain limit."""
    return _extended(root) if len(str(root)) + reserve >= PLAIN_PATH_LIMIT else Path(root)
# --- end of long-path helpers ---


# The extended-length prefix of a drive or UNC path inside a message: as it is, or with every backslash
# doubled once or more (an OSError quotes its file name with repr, and a shutil.Error quotes such
# messages again). Another extended-length path has no plain form and is left as it is.
EXTENDED_PREFIX = re.compile(r"(\\+)\1\?\1(?:(UNC\1)|(?=[A-Za-z]:))")


def _plain_text(text: str) -> str:
    """text with the paths it names in their plain form (no extended-length prefix)."""
    if os.name != "nt":
        return text
    return EXTENDED_PREFIX.sub(lambda found: found.group(1) * 2 if found.group(2) else "", text)


def _fitted(path) -> Path:
    """path in the form its own length calls for: plain below the plain limit, extended-length from there on."""
    return _workspace(_plain(path), 0)


def _answer(path) -> Path:
    """A path the host reported, in the form it is read in.

    An extended-length spelling is used as given, so its names stay literal (Windows does not drop a
    trailing dot or space there); a plain spelling takes the form its own length calls for. A plain
    spelling that would take the extended-length form (on Windows, a drive or UNC path that reaches the
    switch) is normalized first, so that its '.' and '..' collapse as they do when a plain path is
    resolved (Python 3.10 keeps them in an extended-length path); the result then takes the form its own
    length calls for.
    """
    text = str(path)
    if os.name == "nt" and text.startswith("\\\\?\\"):
        return Path(text)
    fitted = _fitted(path)
    if str(fitted) != str(Path(path)):
        return _fitted(os.path.normpath(text))
    return fitted


def _resolved(path) -> Path:
    """Where path leads, for comparisons with the workspace paths; resolved in the form _answer gives it.

    A plain answer comes back in plain form, also when it would take the extended-length form, so that the
    tool normalized it and may have read it in that form (it can lead to a shorter folder through '..' or
    a link). An extended-length answer is shown in plain form from the switch on; below
    it, it is compared as it always was: it stays extended and so does not match a plain workspace path.
    """
    answer = _answer(path)
    resolved = answer.resolve()
    shown = _plain(resolved)
    changed_by_the_tool = str(answer) != str(Path(path))
    return shown if changed_by_the_tool or len(str(shown)) >= PLAIN_PATH_LIMIT else resolved


def _longest_copied_path(source: Path) -> int:
    """The characters of the longest path, relative to source, that copytree with COPY_IGNORE creates."""
    start = len(os.path.join(source, ""))
    longest = 0
    for current, folders, files in os.walk(source, followlinks=True):  # copytree copies what a link leads to
        ignored = COPY_IGNORE(current, folders + files)
        folders[:] = [name for name in folders if name not in ignored]
        for name in folders + files:
            if name not in ignored:
                longest = max(longest, len(os.path.join(current, name)) - start)
    return longest


def _declared_version(source: Path) -> str:
    """The version in the plugin manifest of source; "" when unreadable, which verify() reports where it always did."""
    try:
        return str(json.loads((source / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _workspace_reserve(source: Path) -> int:
    """An upper bound of the characters that follow the workspace root in a path created below it.

    The deepest entries are the files of the plugin in the cache of the host,
    <root>/codex-home/plugins/cache/<marketplace>/<plugin>/<version>/<path>, where <path> is a path
    of the copied tree; the copy the marketplace is added from, <root>/agentic-vault/<path>, is
    shorter. The names come from PLUGIN_ID and the manifest and the longest <path> is measured on
    the tree about to be copied, so the bound is as tight as the layout allows and a workspace
    below an ordinary temporary folder keeps the plain form.
    """
    plugin, _, marketplace = PLUGIN_ID.partition("@")
    cache = os.path.join(HOME_FOLDER, "plugins", "cache", marketplace, plugin, _declared_version(source))
    return len(os.sep) + max(len(PACKAGE_FOLDER), len(cache)) + len(os.sep) + _longest_copied_path(source)


def _workspace_parent(reserve: int) -> str | None:
    """The `dir` tempfile creates the workspace in: None, its default, unless the workspace needs the extended form.

    The workspace is <temporary folder>/<WORKSPACE_PREFIX><random name>, and `reserve` is what is
    created below it. Created in an extended-length parent the workspace carries that form itself,
    so what is written below it, and its removal by tempfile, go through that form too.
    """
    parent = Path(tempfile.gettempdir()).resolve()
    name = len(os.sep) + len(WORKSPACE_PREFIX) + WORKSPACE_RANDOM_LENGTH
    chosen = _workspace(parent, name + reserve)
    return None if chosen == parent else str(chosen)


def _shown(error: BaseException) -> str:
    """The message of error; a file name an OSError carries in extended-length form is shown in plain form."""
    if isinstance(error, OSError):
        for name in ("filename", "filename2"):
            value = getattr(error, name, None)
            if isinstance(value, (str, Path)):
                plain = str(_plain(value))
                if plain != str(value) and str(_extended(plain)) == str(value):  # value is the extended form of plain
                    setattr(error, name, type(value)(plain))
    return str(error)


def resolve_codex_executable(codex: str) -> str:
    executable = Path(shutil.which(codex) or codex).resolve()
    if os.name != "nt" or executable.suffix.lower() not in {".cmd", ".bat"}:
        return str(executable)

    targets = {
        "amd64": ("x64", "x86_64"), "x86_64": ("x64", "x86_64"),
        "arm64": ("arm64", "aarch64"), "aarch64": ("arm64", "aarch64"),
    }
    target = targets.get(platform.machine().lower())
    if executable.stem.lower() == "codex" and target is not None:
        package_arch, architecture = target
        for package in (
            executable.parent / "node_modules/@openai/codex",
            executable.parent.parent / "@openai/codex",
        ):
            if not (package / "bin/codex.js").is_file():
                continue
            optional = f"@openai/codex-win32-{package_arch}/vendor"
            for vendor in (
                package / "node_modules" / optional,
                package.parents[1] / optional,
                package / "vendor",
            ):
                native = vendor / f"{architecture}-pc-windows-msvc/bin/codex.exe"
                if native.is_file():
                    return str(native.resolve())
    raise RuntimeError("Cannot resolve Windows batch launcher; pass --codex with a native codex.exe path")


def stop_owned_processes(process: subprocess.Popen) -> None:
    if os.name == "nt":
        if process.poll() is None:
            process.kill()
    else:
        # Each verifier command owns a new session; include any wrapper children.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def run_json(
    codex: str, args: list[str], cwd: Path, environment: dict, *, timeout: float = 30,
) -> dict:
    with subprocess.Popen(
        [resolve_codex_executable(codex), *args], cwd=cwd, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", start_new_session=os.name != "nt",
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        finally:
            stop_owned_processes(process)
        if process.returncode:
            raise RuntimeError(f"Codex {' '.join(args[:3])} failed: {stderr.strip()}")
        return json.loads(stdout)


def inspect_host(
    codex: str, vault: Path, environment: dict, home: Path, *, installed_root: Path | None = None,
) -> dict:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_log:
        process = subprocess.Popen([resolve_codex_executable(codex), "app-server", "--stdio"], cwd=vault,
                                   env=environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=error_log,
                                   text=True, encoding="utf-8", bufsize=1,
                                   start_new_session=os.name != "nt")
        responses: queue.Queue = queue.Queue()

        def consume() -> None:
            try:
                for line in process.stdout:
                    responses.put(json.loads(line))
            except (ValueError, OSError) as error:
                responses.put(error)
            finally:
                responses.put(None)

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()

        def send(message: dict) -> None:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def request(number: int, method: str, params: dict) -> dict:
            send({"id": number, "method": method, "params": params})
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    response = responses.get(timeout=max(0.01, deadline - time.monotonic()))
                except queue.Empty as error:
                    raise RuntimeError(f"{method} timed out") from error
                if not isinstance(response, dict):
                    raise RuntimeError("Codex app-server closed or returned invalid JSON")
                if response.get("id") == number:
                    if "error" in response:
                        raise RuntimeError(f"{method}: {response['error']}")
                    return response["result"]
            raise RuntimeError(f"{method} timed out")

        try:
            initialized = request(1, "initialize", {
                "clientInfo": {"name": "agentic-vault-verification", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            })
            # A host answer that reaches the plain limit is located by its plain path in either
            # spelling; a shorter one is compared as it always was (see _resolved and _answer).
            if _resolved(initialized["codexHome"]) != _plain(home):
                raise RuntimeError("Codex did not use the isolated test home")
            send({"method": "initialized", "params": {}})
            skills = request(2, "skills/list", {"cwds": [str(vault)], "forceReload": True})
            hooks = request(3, "hooks/list", {"cwds": [str(vault)]})
            if any(item.get("errors") for item in skills["data"] + hooks["data"]):
                raise RuntimeError("Codex reported skill or hook discovery errors")
            owned_skills = [skill for item in skills["data"] for skill in item["skills"]
                            if skill.get("pluginId") == PLUGIN_ID]
            owned_hooks = [hook for item in hooks["data"] for hook in item["hooks"]
                           if hook.get("pluginId") == PLUGIN_ID]
            required_skills = {
                "agentic-vault:agentic-vault": "skills/agentic-vault/SKILL.md",
                "agentic-vault:webapp-principles": "skills/webapp-principles/SKILL.md",
            }
            resource_root = _resolved(installed_root) if installed_root is not None else None
            discovered_paths = set()
            for name, relative in required_skills.items():
                matches = [skill for skill in owned_skills if skill["name"] == name]
                if (len(matches) != 1 or not matches[0]["enabled"]
                        or not _answer(matches[0]["path"]).is_file()):
                    raise RuntimeError(f"Required enabled skill was not discovered exactly once: {name}")
                skill_path = _resolved(matches[0]["path"])
                if resource_root is None:
                    if skill_path.parts[-3:] != Path(relative).parts:
                        raise RuntimeError(f"Required skill has the wrong resource path: {name}")
                    resource_root = skill_path.parents[2]
                if (skill_path != _resolved(resource_root / relative)
                        or skill_path in discovered_paths):
                    raise RuntimeError(f"Required skill has the wrong resource path: {name}")
                discovered_paths.add(skill_path)
            expected_events = {"sessionStart": 3, "preCompact": 1, "sessionEnd": 1}
            discovered_events = {event: sum(hook["eventName"] == event for hook in owned_hooks)
                                 for event in expected_events}
            if len(owned_hooks) != sum(expected_events.values()) or discovered_events != expected_events:
                raise RuntimeError("Expected startup/compact context and both checkpoint events")
            if any(hook["trustStatus"] != "untrusted" for hook in owned_hooks):
                raise RuntimeError("Disposable-home hooks unexpectedly trusted")
            return {"codex": initialized["userAgent"], "skill": "agentic-vault:agentic-vault",
                    "skills": list(required_skills),
                    "session_start_hooks": discovered_events["sessionStart"],
                    "hook_events": discovered_events, "hook_trust": "untrusted"}
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass  # A host that exited early may have already closed its pipe.
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            stop_owned_processes(process)
            reader.join(timeout=2)
            process.stdout.close()


def verify(codex: str) -> dict:
    # Below the plain limit this is the call it always was (dir=None). A workspace that would reach
    # the limit is created in the extended-length form of the temporary folder: what is copied into
    # it goes through that form, and so does its removal by tempfile, which reports a failed removal
    # as before.
    with tempfile.TemporaryDirectory(
        prefix=WORKSPACE_PREFIX, dir=_workspace_parent(_workspace_reserve(ROOT)),
    ) as temporary:
        root = Path(temporary).resolve()
        package = root / PACKAGE_FOLDER
        try:
            shutil.copytree(ROOT, package, ignore=COPY_IGNORE)
        except shutil.Error as error:
            # copytree lists the files it could not copy with their destinations in the form of the
            # workspace; the message names them in plain form, as the rest of the output does.
            raise shutil.Error(_plain_text(str(error))) from None
        # The host receives each path in the form the length of that path calls for: the plain
        # paths it always received, unless one of them reaches the plain limit itself.
        home = _fitted(root / HOME_FOLDER)
        home.mkdir()
        vault = _fitted(root / "test vault")
        vault.mkdir()
        environment = os.environ.copy()
        environment.update(CODEX_HOME=str(home), PYTHONUTF8="1")
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CLAUDE_PROJECT_DIR"):
            environment.pop(name, None)
        market = run_json(
            codex, ["plugin", "marketplace", "add", str(_fitted(package)), "--json"], vault, environment)
        if market["marketplaceName"] != "agentic-vault-local":
            raise RuntimeError("Unexpected marketplace identity")
        installed = run_json(codex, ["plugin", "add", PLUGIN_ID, "--json"], vault, environment)
        expected = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        # The installed copy is located as _resolved locates a host answer, and each of its files is
        # read in the form the length of that file's path calls for.
        cached = _resolved(installed["installedPath"])
        cached.relative_to(_plain(home))
        if installed["version"] != expected["version"]:
            raise RuntimeError("Codex installed a different version")
        report = inspect_host(codex, vault, environment, home, installed_root=cached)
        for relative in ("hooks/session_start.py", "hooks/session_checkpoint.py", "hooks/hooks.json", "hooks/run_python_hook.sh",
                         "skills/agentic-vault/SKILL.md",
                         "skills/webapp-principles/SKILL.md", "docs/webapp-principles.md",
                         "skills/agentic-vault/references/codex.md",
                         "skills/agentic-vault/scripts/vault_recall.py",
                         # the shared deny-zone matcher, the health check and the Telegram
                         # bridge carry the v0.46.0 fixes
                         "skills/agentic-vault/scripts/vault_paths.py",
                         "skills/agentic-vault/scripts/vault_healthcheck.py",
                         "skills/agentic-vault/scripts/jarvis_bridge.py",
                         "skills/agentic-vault/scripts/vault_links.py",
                         "skills/agentic-vault/scripts/vault_evidence.py",
                         "skills/agentic-vault/scripts/vault_retrieval.py",
                         "skills/agentic-vault/scripts/vault_memory.py",
                         "skills/agentic-vault/scripts/vault_revocation.py",
                         "scripts/evaluate_memory_lifecycle.py",
                         "docs/memory-revocation.md", "docs/memory-lifecycle.md",
                         "skills/agentic-vault/scripts/vault_memory_trust.py",
                         "scripts/evaluate_memory_trust.py", "scripts/evaluate_model_memory_lifecycle.py",
                         "docs/memory-trust.md", "docs/model-memory-lifecycle.md",
                         "skills/agentic-vault/scripts/vault_impact.py",
                         "skills/agentic-vault/scripts/vault_okf.py",
                         "skills/agentic-vault/scripts/vault_ontology.py",
                         "skills/agentic-vault/scripts/vault_ontology_query.py",
                         "skills/agentic-vault/scripts/vault_ontology_cli.py",
                         "docs/ontology-contracts.md",
                         "skills/agentic-vault/scripts/vault_bundle.py",
                         "skills/agentic-vault/scripts/vault_review_plan.py",
                         "docs/recoverable-bundles.md",
                         "docs/review-planning.md",
                         "docs/generated-memory-lifecycle.md",
                         "scripts/evaluate_generated_memory_lifecycle.py",
                         "scripts/generated_memory_worker.py",
                         "skills/agentic-vault/scripts/vault_capacity.py",
                         "skills/agentic-vault/scripts/vault_handoff_fold.py",
                         "skills/agentic-vault/scripts/vault_plugin_scope.py",
                         "skills/agentic-vault/scripts/vault_lesson_clusters.py",
                         "skills/agentic-vault/scripts/vault_lesson_cues.py",
                         "skills/agentic-vault/scripts/vault_worktrees.py",
                         "skills/agentic-vault/scripts/vault_commit.py",
                         "docs/recall-scope.md",
                         "docs/capacity.md",
                         "docs/handoff-fold.md",
                         "docs/lesson-recurrence.md",
                         "docs/verifier-fidelity.md",
                         "docs/concurrent-commits.md",
                         "scripts/evaluate_handoff_fold.py",
                         "tests/fixtures/handoff_fold/pack.json",
                         "scripts/evaluate_verifier_fidelity.py",
                         "scripts/env_vary.py",
                         "scripts/evaluate_knowledge_evolution.py",
                         "tests/fixtures/knowledge_evolution/history.json",
                         "docs/knowledge-impact.md", "docs/okf-export.md", "docs/knowledge-evolution.md",
                         "skills/agentic-vault/scripts/vault_lesson_metrics.py",
                         "skills/agentic-vault/scripts/vault_runs.py",
                         "skills/agentic-vault/scripts/vault_task_contracts.py",
                         "skills/agentic-vault/scripts/vault_staged_evaluation.py",
                         "skills/agentic-vault/scripts/vault_improvement_cycles.py",
                         "skills/agentic-vault/scripts/bizrouter_client.py",
                         "docs/task-contracts.md",
                         "docs/staged-evaluation.md",
                         "docs/improvement-cycles.md",
                         "skills/agentic-vault/scripts/vault_run_costs.py",
                         "docs/improvement-accounting.md",
                         "scripts/evaluate_improvement_accounting.py",
                         "skills/agentic-vault/scripts/vault_improvements.py",
                         "docs/improvement-experiments.md",
                         "skills/agentic-vault/scripts/vault_assembly.py",
                         "skills/agentic-vault/scripts/vault_procedures.py",
                         "docs/memory-formation.md",
                         "docs/memory-assembly.md",
                         "docs/procedural-recall.md",
                         "scripts/evaluate_memory_formation.py",
                         "scripts/evaluate_memory_reliability.py",
                         "scripts/evaluate_state_revisions.py",
                         "scripts/evaluate_evidence_insufficiency.py",
                         "tests/fixtures/state_revisions/pack.json",
                         "tests/fixtures/evidence_insufficiency/pack.json",
                         "docs/state-revision-evaluation.md",
                         "docs/evidence-insufficiency-evaluation.md",
                         "docs/support-row-diagnostics.md",
                         "skills/agentic-vault/scripts/vault_query_expansion.py",
                         "skills/agentic-vault/scripts/vault_lint_extensions.py",
                         "skills/agentic-vault/scripts/vault_warning_policy.py",
                         "skills/agentic-vault/scripts/vault_provenance.py",
                         "skills/agentic-vault/scripts/vault_egress.py",
                         "skills/agentic-vault/scripts/vault_state.py",
                         "skills/agentic-vault/scripts/vault_ssot.py",
                         "skills/agentic-vault/scripts/vault_period.py",
                         "skills/agentic-vault/scripts/vault_doctor.py",
                         "skills/agentic-vault/scripts/vault_session_evidence.py",
                         "skills/agentic-vault/scripts/vault_lesson_delta.py",
                         "skills/agentic-vault/scripts/vault_declarative_rules.py",
                         "skills/agentic-vault/scripts/vault_compile_quality.py",
                         "skills/agentic-vault/scripts/vault_relation_probes.py",
                         "skills/agentic-vault/scripts/vault_context_guard.py",
                         "skills/agentic-vault/scripts/vault_recall_support.py",
                         "scripts/evaluate_recall_support.py",
                         "tests/fixtures/recall_support/pack.json",
                         "scripts/evaluate_lesson_cue_effects.py",
                         "docs/recall-support.md",
                         "docs/recall-support-evaluation.md",
                         "docs/lesson-cue-effects.md",
                         "docs/relation-preservation.md", "docs/revocation-egress.md",
                         "scripts/evaluate_relation_preservation.py",
                         "tests/fixtures/relation_preservation/pack.json",
                         "scripts/evaluate_revocation_egress.py",
                         "tests/fixtures/revocation_egress/pack.json",
                         "skills/agentic-vault/scripts/vault_token_calibration.py",
                         "skills/agentic-vault/scripts/resources/lint-benign-v1.json",
                         "docs/hardening.md",
                         "assets/templates/ssot-ledger.md",
                         "commands/vault-harden.md",
                         "docs/memory-patterns.md",
                         "scripts/import_qmd_candidates.py",
                         "scripts/evaluate_recall.py",
                         "scripts/eval_stats.py",
                         "scripts/evaluate_memory_workflows.py",
                         "docs/evidence.md",
                         "docs/link-proposals.md",
                         "assets/templates/AGENTS-vault-stub.md",
                         # doctor's adapter report reads the bundled Aside helper and procedure command, deny-zone
                         # guard and lesson cue hook for their stamps
                         "skills/agentic-vault/scripts/vault_adapters.py",
                         "assets/adapters/deny-guard/deny-guard.py",
                         "assets/adapters/deny-guard/ps-parse.ps1",
                         "assets/adapters/lesson-cues/lesson-cues.py",
                         "assets/adapters/lesson-cues/settings-hooks.json",
                         "assets/adapters/lesson-cues/guide.md",
                         "assets/adapters/aside/aside-up.ps1",
                         "assets/adapters/aside/aside-repl-command.md"):
            if _fitted(cached / relative).read_bytes() != (ROOT / relative).read_bytes():
                raise RuntimeError(f"Installed resource differs: {relative}")
        return {"version": installed["version"], "installed_resources": "verified",
                "isolated_home": True, "model_turns": 0, **report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex", default=shutil.which("codex"),
        help="Codex executable path (Windows custom launchers require a native codex.exe)",
    )
    args = parser.parse_args()
    if not args.codex:
        parser.error("Codex CLI is required; pass --codex PATH")
    try:
        report = verify(args.codex)
    except (OSError, RuntimeError, ValueError, KeyError, queue.Empty, subprocess.TimeoutExpired) as error:
        print(f"Codex plugin verification failed: {_shown(error)}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
