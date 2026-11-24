"""Verifier subprocess failures must not leave children holding temporary resources."""
from __future__ import annotations

import ast
import contextlib
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from _winfs import PLAIN_PATH_LIMIT, deep_path, extended, make_junction, plain
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import PLAIN_PATH_LIMIT, deep_path, extended, make_junction, plain


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "codex_verification", ROOT / "scripts/verify_codex_plugin.py",
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

WINDOWS = os.name == "nt"
PREFIX = "\\\\?\\"  # the extended-length prefix; only Windows adds it
# Room for the longest path below a fixture package or its installed copy. A fixture folder takes
# the extended-length form only when it plus this room reaches the plain limit, so nothing changes
# under an ordinary TEMP.
FIXTURE_ROOM = 100
# The npm layouts put the native binary up to some 120 characters below the launcher, and the
# stand-in binary needs room for the names of the runtime libraries next to it.
NPM_LAYOUT_ROOM = 128
PROGRAM_NAME_ROOM = 16
# What verify() reports for the fixture plugin, whatever the length of the workspace.
FIXTURE_REPORT = {"version": "0.11.0", "installed_resources": "verified", "isolated_home": True,
                  "model_turns": 0, "host": "fixture"}


def handed(path: Path) -> str:
    """What the host is expected to receive for path: plain, unless path itself reaches the plain limit."""
    return str(deep_path(plain(path)))


class CodexVerificationTests(unittest.TestCase):
    def temporary_directory(self, room: int) -> tempfile.TemporaryDirectory:
        """tempfile.TemporaryDirectory(), in extended-length form when `room` characters below it reach the plain limit.

        Created in the extended-length form of the temporary folder, the directory holds entries
        beyond the plain limit of Windows without long-path support and tempfile removes them with
        it. Under an ordinary TEMP, and on every other system, it is the plain call.
        """
        parent = Path(tempfile.gettempdir()).resolve()
        chosen = deep_path(parent, len(os.sep) + len("tmp") + 8 + room)
        return tempfile.TemporaryDirectory(dir=None if chosen == parent else str(chosen))

    def long_folder(self, base: Path, length: int) -> Path:
        """Create an empty folder below base whose plain path has at least `length` characters; return it plain."""
        folder = plain(Path(base).resolve()) / "long"
        while len(str(folder)) < length:
            folder = folder / ("w" * min(100, max(1, length - len(str(folder)) - len(os.sep))))
        extended(folder).mkdir(parents=True)
        return folder

    def make_plugin_fixture(self, root: Path) -> Path:
        source = root / "source"
        resources = (
            "hooks/session_start.py", "hooks/session_checkpoint.py", "hooks/hooks.json", "hooks/run_python_hook.sh",
            "skills/agentic-vault/SKILL.md",
            "skills/agentic-vault/references/codex.md",
            "skills/agentic-vault/scripts/vault_recall.py",
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
            "docs/recoverable-bundles.md", "docs/review-planning.md",
            "docs/generated-memory-lifecycle.md",
            "scripts/evaluate_generated_memory_lifecycle.py",
            "scripts/generated_memory_worker.py",
            "skills/agentic-vault/scripts/vault_capacity.py",
            "skills/agentic-vault/scripts/vault_handoff_fold.py",
            "skills/agentic-vault/scripts/vault_plugin_scope.py",
            "skills/agentic-vault/scripts/vault_hook_wiring.py",
            "skills/agentic-vault/scripts/vault_install_state.py",
            "scripts/verify_release_claims.py",
            "skills/agentic-vault/scripts/vault_lesson_clusters.py",
            "skills/agentic-vault/scripts/vault_lesson_cues.py",
            "skills/agentic-vault/scripts/vault_worktrees.py",
            "skills/agentic-vault/scripts/vault_commit.py",
            "docs/recall-scope.md", "docs/capacity.md", "docs/handoff-fold.md",
            "docs/lesson-recurrence.md", "docs/verifier-fidelity.md",
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
            "skills/agentic-vault/scripts/vault_cloud_sync.py",
            "skills/agentic-vault/scripts/vault_coverage_matrix.py",
            "skills/agentic-vault/scripts/bizrouter_client.py",
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
            "scripts/eval_recall_hygiene.py",
            "scripts/sync_result_tables.py",
            "scripts/rerank_candidates.py",
            "scripts/evaluate_memory_workflows.py",
            "docs/evidence.md", "docs/link-proposals.md", "assets/templates/AGENTS-vault-stub.md",
            "skills/agentic-vault/scripts/vault_adapters.py",
            "assets/adapters/deny-guard/deny-guard.py", "assets/adapters/deny-guard/ps-parse.ps1",
            "assets/adapters/lesson-cues/lesson-cues.py",
            "assets/adapters/lesson-cues/settings-hooks.json",
            "assets/adapters/lesson-cues/guide.md",
            "assets/adapters/aside/aside-up.ps1",
        )
        self.assertLess(max(map(len, resources)), FIXTURE_ROOM)
        for relative in resources:
            resource = source / relative
            resource.parent.mkdir(parents=True, exist_ok=True)
            resource.write_text(f"Fixture resource: {relative}\n", encoding="utf-8")
        manifest = source / ".codex-plugin/plugin.json"
        manifest.parent.mkdir()
        manifest.write_text('{"version": "0.11.0"}', encoding="utf-8")
        return source

    def inspect_discovery_fixture(
        self, mutate_skills=None, mutate_hooks=None, *, bind_installed_root=True, deep_as=None,
    ) -> dict:
        # deep_as (plain or extended) installs the skills beyond the plain limit and has the host
        # report their paths in that spelling; the home and the working folder stay as short as
        # verify() hands them over, and the host reports the home as it received it.
        with self.temporary_directory(0 if deep_as is None else 2 * PLAIN_PATH_LIMIT) as temporary:
            root = plain(Path(temporary))
            home = root / "codex-home"
            home.mkdir()
            vault = root / "vault"
            vault.mkdir()
            installed = root / "installed"
            if deep_as is not None:
                installed = extended(self.long_folder(root, PLAIN_PATH_LIMIT)) / "installed"
            skills = []
            for directory, name in (
                ("agentic-vault", "agentic-vault:agentic-vault"),
            ):
                resource = installed / "skills" / directory / "SKILL.md"
                resource.parent.mkdir(parents=True)
                resource.write_text(f"Fixture skill: {name}\n", encoding="utf-8")
                skills.append({"name": name, "enabled": True, "path": str((deep_as or Path)(resource)),
                               "pluginId": "agentic-vault@agentic-vault-local"})
            hooks = [{"eventName": event, "trustStatus": "untrusted",
                      "pluginId": "agentic-vault@agentic-vault-local"}
                     for event in ("sessionStart", "sessionStart", "sessionStart",
                                   "preCompact", "sessionEnd")]
            if mutate_skills is not None:
                mutate_skills(skills)
            if mutate_hooks is not None:
                mutate_hooks(hooks)
            (vault / "discovery.json").write_text(json.dumps({
                "skills/list": {"data": [{"cwd": str(vault), "skills": skills, "errors": []}]},
                "hooks/list": {"data": [{"cwd": str(vault), "hooks": hooks, "errors": []}]},
            }), encoding="utf-8")
            # Python treats the app-server argument as a local script. This exercises
            # the real stdio protocol and cleanup without a native install or model.
            (vault / "app-server").write_text(
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "responses = json.loads(Path('discovery.json').read_text(encoding='utf-8'))\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    if 'id' not in request:\n"
                "        continue\n"
                "    if request['method'] == 'initialize':\n"
                "        result = {'codexHome': os.environ['CODEX_HOME'], 'userAgent': 'fixture-host'}\n"
                "    else:\n"
                "        result = responses[request['method']]\n"
                "    print(json.dumps({'id': request['id'], 'result': result}), flush=True)\n",
                encoding="utf-8",
            )
            environment = {**os.environ, "CODEX_HOME": handed(home)}
            options = {"installed_root": plain(installed)} if bind_installed_root else {}
            return verifier.inspect_host(sys.executable, vault, environment, home.resolve(), **options)

    def test_required_native_skills_preserve_primary_report_and_allow_other_owned_skills(self) -> None:
        def add_other_skills(skills):
            skills.append({"name": "agentic-vault:another-command", "enabled": True,
                           "path": "unused-other-skill", "pluginId": "agentic-vault@agentic-vault-local"})
            skills.append({**skills[0], "pluginId": "another-plugin@another-marketplace"})

        report = self.inspect_discovery_fixture(mutate_skills=add_other_skills)

        self.assertEqual(report, {
            "codex": "fixture-host", "skill": "agentic-vault:agentic-vault",
            "skills": ["agentic-vault:agentic-vault"],
            "session_start_hooks": 3,
            "hook_events": {"sessionStart": 3, "preCompact": 1, "sessionEnd": 1},
            "hook_trust": "untrusted",
        })

    def test_native_skill_discovery_without_installed_root_remains_compatible(self) -> None:
        report = self.inspect_discovery_fixture(bind_installed_root=False)

        self.assertEqual(report["skill"], "agentic-vault:agentic-vault")
        self.assertEqual(report["skills"], ["agentic-vault:agentic-vault"])

    def test_each_required_native_skill_must_be_present(self) -> None:
        for name in ("agentic-vault:agentic-vault",):
            with self.subTest(skill=name):
                def remove_skill(skills):
                    skills[:] = [skill for skill in skills if skill["name"] != name]

                with self.assertRaisesRegex(RuntimeError, "skill"):
                    self.inspect_discovery_fixture(mutate_skills=remove_skill)

    def test_each_required_native_skill_must_be_enabled(self) -> None:
        for name in ("agentic-vault:agentic-vault",):
            with self.subTest(skill=name):
                def disable_skill(skills):
                    next(skill for skill in skills if skill["name"] == name)["enabled"] = False

                with self.assertRaisesRegex(RuntimeError, "skill"):
                    self.inspect_discovery_fixture(mutate_skills=disable_skill)

    def test_each_required_native_skill_must_not_be_duplicated(self) -> None:
        for name in ("agentic-vault:agentic-vault",):
            for duplicate_enabled in (True, False):
                with self.subTest(skill=name, duplicate_enabled=duplicate_enabled):
                    def duplicate_skill(skills):
                        skill = next(skill for skill in skills if skill["name"] == name)
                        skills.append({**skill, "enabled": duplicate_enabled})

                    with self.assertRaisesRegex(RuntimeError, "skill"):
                        self.inspect_discovery_fixture(mutate_skills=duplicate_skill)

    def test_each_required_native_skill_path_must_exist(self) -> None:
        for name in ("agentic-vault:agentic-vault",):
            with self.subTest(skill=name):
                def remove_skill_file(skills):
                    skill = next(skill for skill in skills if skill["name"] == name)
                    Path(skill["path"]).unlink()

                with self.assertRaisesRegex(RuntimeError, "skill"):
                    self.inspect_discovery_fixture(mutate_skills=remove_skill_file)

    def test_required_native_skill_paths_must_identify_the_expected_resource(self) -> None:
        for mutation in ("wrong-filename", "other-package"):
            with self.subTest(mutation=mutation):
                def mismatch_resource(skills):
                    expected = Path(skills[0]["path"])
                    if mutation == "wrong-filename":
                        wrong = expected.with_name("another-skill.md")
                    else:
                        wrong = expected.parents[2].with_name("other-package") / "skills/agentic-vault/SKILL.md"
                    wrong.parent.mkdir(parents=True, exist_ok=True)
                    wrong.write_bytes(expected.read_bytes())
                    skills[0]["path"] = str(wrong)

                with self.assertRaisesRegex(RuntimeError, "skill"):
                    self.inspect_discovery_fixture(mutate_skills=mismatch_resource)

    def test_required_native_skill_paths_must_match_installed_package(self) -> None:
        def move_both_paths_to_another_package(skills):
            for skill in skills:
                expected = Path(skill["path"])
                wrong = expected.parents[2].with_name("other-package") / "skills" / expected.parent.name / "SKILL.md"
                wrong.parent.mkdir(parents=True, exist_ok=True)
                wrong.write_bytes(expected.read_bytes())
                skill["path"] = str(wrong)

        with self.assertRaisesRegex(RuntimeError, "skill"):
            self.inspect_discovery_fixture(mutate_skills=move_both_paths_to_another_package)

    def test_required_native_skills_must_belong_to_this_plugin(self) -> None:
        for name in ("agentic-vault:agentic-vault",):
            with self.subTest(skill=name):
                def change_owner(skills):
                    skill = next(skill for skill in skills if skill["name"] == name)
                    skill["pluginId"] = "another-plugin@another-marketplace"

                with self.assertRaisesRegex(RuntimeError, "skill"):
                    self.inspect_discovery_fixture(mutate_skills=change_owner)

    def test_native_hook_counts_events_and_untrusted_status_remain_required(self) -> None:
        for mutation in ("missing", "extra", "wrong-event", "trusted"):
            with self.subTest(mutation=mutation):
                def mutate_hooks(hooks):
                    if mutation == "missing":
                        hooks.pop()
                    elif mutation == "extra":
                        hooks.append(dict(hooks[0]))
                    elif mutation == "wrong-event":
                        hooks[-1]["eventName"] = "sessionStart"
                    else:
                        hooks[0]["trustStatus"] = "trusted"

                with self.assertRaises(RuntimeError):
                    self.inspect_discovery_fixture(mutate_hooks=mutate_hooks)

    def test_installed_skill_resources_must_exist_and_match(self) -> None:
        for relative in ("skills/agentic-vault/SKILL.md", "scripts/eval_recall_hygiene.py",
                         "scripts/sync_result_tables.py", "scripts/rerank_candidates.py"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Changed webapp resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_verification_does_not_write_live_codex_config_or_install_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_plugin_fixture(root)
            live_home = root / "live-codex-home"
            live_home.mkdir()
            config = live_home / "config.toml"
            config.write_text("# Existing live configuration\n", encoding="utf-8")

            with patch.dict(os.environ, {"CODEX_HOME": str(live_home)}):
                report = self.verify_fixture(source)

            self.assertTrue(report["isolated_home"])
            self.assertEqual(report["model_turns"], 0)
            self.assertEqual(config.read_text(encoding="utf-8"), "# Existing live configuration\n")
            self.assertEqual(list(live_home.iterdir()), [config])

    def test_installed_knowledge_evolution_resources_must_exist_and_match(self) -> None:
        for relative in ("skills/agentic-vault/scripts/vault_impact.py",
                         "skills/agentic-vault/scripts/vault_okf.py",
                         "scripts/evaluate_knowledge_evolution.py",
                         "tests/fixtures/knowledge_evolution/history.json",
                         "docs/knowledge-impact.md", "docs/okf-export.md",
                         "docs/knowledge-evolution.md"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))
                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Corrupted knowledge resource\n", encoding="utf-8")
                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_relation_and_egress_resources_must_exist_and_match(self) -> None:
        for relative in ("skills/agentic-vault/scripts/vault_relation_probes.py",
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
                         "tests/fixtures/revocation_egress/pack.json"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Changed relation/egress resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_recovery_review_and_generated_resources_must_match(self) -> None:
        for relative in ("skills/agentic-vault/scripts/vault_bundle.py",
                         "skills/agentic-vault/scripts/vault_review_plan.py",
                         "docs/recoverable-bundles.md", "docs/review-planning.md",
                         "docs/generated-memory-lifecycle.md",
                         "scripts/evaluate_generated_memory_lifecycle.py",
                         "scripts/generated_memory_worker.py"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Changed new resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_large_vault_resources_must_match(self) -> None:
        for relative in ("skills/agentic-vault/scripts/vault_capacity.py",
                         "skills/agentic-vault/scripts/vault_handoff_fold.py",
                         "skills/agentic-vault/scripts/vault_plugin_scope.py",
                         "skills/agentic-vault/scripts/vault_lesson_clusters.py",
                         "skills/agentic-vault/scripts/vault_lesson_cues.py",
                         "skills/agentic-vault/scripts/vault_worktrees.py",
                         "skills/agentic-vault/scripts/vault_commit.py",
                         "assets/adapters/lesson-cues/lesson-cues.py",
                         "assets/adapters/lesson-cues/settings-hooks.json",
                         "assets/adapters/lesson-cues/guide.md",
                         "docs/recall-scope.md", "docs/capacity.md", "docs/handoff-fold.md",
                         "docs/lesson-recurrence.md", "docs/verifier-fidelity.md",
                         "docs/concurrent-commits.md",
                         "scripts/evaluate_handoff_fold.py",
                         "tests/fixtures/handoff_fold/pack.json",
                         "scripts/evaluate_verifier_fidelity.py",
                         "scripts/env_vary.py"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Changed new resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_ontology_resources_must_exist_and_match(self) -> None:
        for relative in ("skills/agentic-vault/scripts/vault_ontology.py",
                         "skills/agentic-vault/scripts/vault_ontology_query.py",
                         "skills/agentic-vault/scripts/vault_ontology_cli.py",
                         "docs/ontology-contracts.md"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Changed ontology resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_revocation_resources_must_exist_and_match(self) -> None:
        for relative in ("skills/agentic-vault/scripts/vault_revocation.py",
                         "scripts/evaluate_memory_lifecycle.py",
                         "docs/memory-revocation.md", "docs/memory-lifecycle.md"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Corrupted revocation resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_memory_trust_resources_must_exist_and_match(self) -> None:
        resources = ("skills/agentic-vault/scripts/vault_memory_trust.py",
                     "scripts/evaluate_memory_trust.py", "scripts/evaluate_model_memory_lifecycle.py",
                     "docs/memory-trust.md", "docs/model-memory-lifecycle.md")
        for relative in resources:
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Corrupted trust resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_hardening_resources_must_exist_and_match(self) -> None:
        for relative in ('hooks/session_checkpoint.py', 'skills/agentic-vault/scripts/vault_ssot.py',
                         'skills/agentic-vault/scripts/vault_period.py',
                         'skills/agentic-vault/scripts/resources/lint-benign-v1.json'):
            for mutation in ('changed', 'missing'):
                with self.subTest(resource=relative, mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))
                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == 'missing':
                            resource.unlink()
                        else:
                            resource.write_text('Corrupted hardening resource\n', encoding='utf-8')
                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def verify_fixture(self, source: Path, inspect_package=None, mutate_cache=None, *,
                       seen: list | None = None, reported=plain) -> dict:
        # seen collects what verify() did: ("workspace", keywords, plain root) for the temporary
        # directory it created, ("run_json", args, cwd, environment) for each host command,
        # ("installed", length of the longest plain path of the installed copy) and
        # ("inspect_host", vault, home, installed_root) for the inspection.
        # reported (plain or extended) is the spelling the stand-in host answers the install in.
        package = None
        make_directory = tempfile.TemporaryDirectory

        def temporary_directory(**keywords):
            made = make_directory(**keywords)
            seen.append(("workspace", keywords, plain(Path(made.name).resolve())))
            return made

        def run_json(codex, args, cwd, environment):
            nonlocal package
            if seen is not None:
                seen.append(("run_json", list(args), cwd, dict(environment)))
            if args[:3] == ["plugin", "marketplace", "add"]:
                # Like a host that copes with long paths, the stand-in reaches what it was handed
                # through the extended-length form once the copies below would pass the plain limit.
                package = deep_path(Path(args[3]), FIXTURE_ROOM)
                if inspect_package is not None:
                    inspect_package(package)
                return {"marketplaceName": "agentic-vault-local"}
            self.assertEqual(args[:2], ["plugin", "add"])
            cached = deep_path(Path(environment["CODEX_HOME"]) / "plugins/cache/fixture", FIXTURE_ROOM)
            shutil.copytree(package, cached)
            if seen is not None:
                seen.append(("installed", max(
                    len(str(plain(os.path.join(current, name))))
                    for current, folders, files in os.walk(cached) for name in folders + files)))
            if mutate_cache is not None:
                mutate_cache(cached)
            return {"installedPath": str(reported(cached)), "version": "0.11.0"}

        def inspect_host(codex, vault, environment, home, *, installed_root=None):
            if seen is not None:
                seen.append(("inspect_host", vault, home, installed_root))
            return {"host": "fixture"}

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(verifier, "ROOT", source))
            stack.enter_context(patch.object(verifier, "run_json", side_effect=run_json))
            stack.enter_context(patch.object(verifier, "inspect_host", side_effect=inspect_host))
            if seen is not None:
                stack.enter_context(patch.object(tempfile, "TemporaryDirectory", side_effect=temporary_directory))
            return verifier.verify("unused-fixture-executable")

    def test_installed_doctor_resources_must_exist_and_match(self) -> None:
        # The doctor turns a reader it cannot load into a fixed probe_failed block.
        for relative in ("skills/agentic-vault/scripts/vault_doctor.py",
                         "skills/agentic-vault/scripts/vault_session_evidence.py",
                         "skills/agentic-vault/scripts/vault_hook_wiring.py",
                         "skills/agentic-vault/scripts/vault_install_state.py"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Changed doctor resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_adapter_resources_must_exist_and_match(self) -> None:
        # doctor's adapter report compares an installed Aside helper and deny-zone guard with these
        # bundled copies.
        for relative in ("skills/agentic-vault/scripts/vault_adapters.py",
                         "assets/adapters/deny-guard/deny-guard.py",
                         "assets/adapters/deny-guard/ps-parse.ps1",
                         "assets/adapters/lesson-cues/lesson-cues.py",
                         "assets/adapters/lesson-cues/settings-hooks.json",
                         "assets/adapters/lesson-cues/guide.md",
                         "assets/adapters/aside/aside-up.ps1"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Changed adapter resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_verifier_compares_every_bundled_file_the_adapter_report_reads(self) -> None:
        # Every BUNDLED_* file of the adapter report is byte-compared after the native install.
        tree = ast.parse((ROOT / "scripts/verify_codex_plugin.py").read_text(encoding="utf-8"))
        compared: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
                names = {element.value for element in node.iter.elts
                         if isinstance(element, ast.Constant) and isinstance(element.value, str)}
                if "skills/agentic-vault/scripts/vault_adapters.py" in names:
                    compared |= names
        self.assertIn("assets/adapters/aside/aside-up.ps1", compared)
        scripts = str(ROOT / "skills" / "agentic-vault" / "scripts")
        sys.path.insert(0, scripts)
        try:
            import vault_adapters
        finally:
            sys.path.remove(scripts)
        bundled = {name: value for name, value in vars(vault_adapters).items() if name.startswith("BUNDLED_")}
        self.assertIn("BUNDLED_HELPER", bundled)
        for name, path in sorted(bundled.items()):
            with self.subTest(constant=name):
                self.assertIn(Path(path).resolve().relative_to(ROOT.resolve()).as_posix(), compared)

    def test_installed_zone_healthcheck_and_bridge_resources_must_exist_and_match(self) -> None:
        # The shared deny-zone matcher, the health check and the Telegram bridge carry the
        # v0.46.0 fixes; an installed copy that differs from the source must be rejected.
        for relative in ("skills/agentic-vault/scripts/vault_paths.py",
                         "skills/agentic-vault/scripts/vault_healthcheck.py",
                         "skills/agentic-vault/scripts/jarvis_bridge.py"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Changed zone, check or bridge resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_memory_boundary_resources_must_exist_and_match(self) -> None:
        for relative in ("scripts/evaluate_state_revisions.py",
                         "scripts/evaluate_evidence_insufficiency.py",
                         "tests/fixtures/state_revisions/pack.json",
                         "tests/fixtures/evidence_insufficiency/pack.json",
                         "docs/state-revision-evaluation.md",
                         "docs/evidence-insufficiency-evaluation.md",
                         "docs/support-row-diagnostics.md"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Changed memory-boundary resource\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_evidence_resources_must_exist_and_match(self) -> None:
        for relative in ("skills/agentic-vault/scripts/vault_evidence.py", "docs/evidence.md"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Corrupted installation\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_link_resources_must_exist_and_match(self) -> None:
        for relative in ("skills/agentic-vault/scripts/vault_links.py", "docs/link-proposals.md"):
            for mutation in ("changed", "missing"):
                with self.subTest(resource=relative, mutation=mutation), \
                     tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))

                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == "missing":
                            resource.unlink()
                        else:
                            resource.write_text("Corrupted link helper installation\n", encoding="utf-8")

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_disposable_package_excludes_nested_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = self.make_plugin_fixture(Path(temporary))
            excluded = (".worktrees", "worktrees", ".git", ".superpowers", "__pycache__")
            for parent in (source, source / "nested"):
                for name in excluded:
                    child = parent / name / "other-checkout/private-state.txt"
                    child.parent.mkdir(parents=True)
                    child.write_text("Must remain outside the disposable package", encoding="utf-8")

            def inspect_package(package):
                for parent in (package, package / "nested"):
                    for name in excluded:
                        self.assertFalse((parent / name).exists(), str(parent / name))
                self.assertTrue((package / "docs/evidence.md").is_file())

            result = self.verify_fixture(source, inspect_package=inspect_package)
            self.assertEqual(result["installed_resources"], "verified")
            self.assertEqual(result["model_turns"], 0)

    def test_installed_advanced_memory_resources_must_exist_and_match(self) -> None:
        for relative in ('skills/agentic-vault/scripts/vault_retrieval.py',
                         'skills/agentic-vault/scripts/vault_memory.py',
                         'skills/agentic-vault/scripts/vault_lesson_metrics.py',
                         'docs/memory-patterns.md', 'scripts/import_qmd_candidates.py',
                         'scripts/evaluate_recall.py', 'scripts/eval_stats.py',
                         'scripts/evaluate_memory_workflows.py'):
            for mutation in ('changed', 'missing'):
                with self.subTest(resource=relative, mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))
                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == 'missing':
                            resource.unlink()
                        else:
                            resource.write_text('Changed memory resource\n', encoding='utf-8')
                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_installed_memory_reliability_resources_must_exist_and_match(self) -> None:
        for relative in ('skills/agentic-vault/scripts/vault_assembly.py',
                         'skills/agentic-vault/scripts/vault_run_costs.py',
                         'docs/improvement-accounting.md', 'scripts/evaluate_improvement_accounting.py',
                         'skills/agentic-vault/scripts/vault_procedures.py',
                         'docs/memory-formation.md', 'docs/memory-assembly.md',
                         'docs/procedural-recall.md', 'scripts/evaluate_memory_formation.py',
                         'scripts/evaluate_memory_reliability.py'):
            for mutation in ('changed', 'missing'):
                with self.subTest(resource=relative, mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                    source = self.make_plugin_fixture(Path(temporary))
                    def mutate_cache(cached):
                        resource = cached / relative
                        if mutation == 'missing':
                            resource.unlink()
                        else:
                            resource.write_text('Changed reliability resource\n', encoding='utf-8')
                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        self.verify_fixture(source, mutate_cache=mutate_cache)

    def test_direct_executable_returns_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = verifier.run_json(
                sys.executable, ["-c", "print('{\"source\": \"native\"}')"],
                Path(temporary), os.environ.copy(),
            )
            self.assertEqual(result, {"source": "native"})

    @unittest.skipUnless(os.name == "nt", "Windows npm launcher regression")
    def test_npm_launcher_uses_native_binary_for_supported_vendor_layouts(self) -> None:
        target = "aarch64" if platform.machine().lower() == "arm64" else "x86_64"
        package_arch = "arm64" if target == "aarch64" else "x64"
        for layout in ("nested", "hoisted", "bundled"):
            with self.subTest(layout=layout), self.temporary_directory(NPM_LAYOUT_ROOM) as temporary:
                root = Path(temporary)
                launcher = root / "codex.CMD"
                launcher.write_text('@echo {"source":"wrapper"}\n', encoding="utf-8")
                package = root / "node_modules/@openai/codex"
                (package / "bin").mkdir(parents=True)
                (package / "bin/codex.js").write_text("// npm entrypoint\n", encoding="utf-8")
                if layout == "bundled":
                    vendor = package / "vendor"
                else:
                    modules = package / "node_modules" if layout == "nested" else root / "node_modules"
                    vendor = modules / f"@openai/codex-win32-{package_arch}/vendor"
                native = vendor / f"{target}-pc-windows-msvc/bin/codex.exe"
                if deep_path(plain(native.parent), PROGRAM_NAME_ROOM) == plain(native.parent):
                    native.parent.mkdir(parents=True)
                else:
                    # Without long-path support Windows starts no program from a path beyond
                    # MAX_PATH, not even in the extended-length form, and this stand-in stops
                    # loading some characters earlier. Where a long TEMP makes the layout put the
                    # binary at the plain limit, the folder that holds it is a junction to a
                    # short one in the same temporary directory, the way a linking package
                    # manager lays packages out; the resolver follows it. Under an ordinary TEMP
                    # the layout is the plain folders it always was.
                    (root / "native").mkdir()
                    native.parent.parent.mkdir(parents=True)
                    make_junction(native.parent, plain(root / "native"))
                # A Windows venv python.exe is a redirector requiring pyvenv.cfg.
                # This fixture needs a relocatable stand-in for the native binary.
                native_python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
                shutil.copyfile(native_python, native)
                for dll in native_python.parent.glob("python*.dll"):
                    shutil.copyfile(dll, native.parent / dll.name)
                environment = {**os.environ, "PYTHONHOME": sys.base_prefix}

                result = verifier.run_json(
                    str(launcher), ["-c", "print('{\"source\": \"native\"}')"],
                    root, environment,
                )

                self.assertEqual(result, {"source": "native"})

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher regression")
    def test_unknown_batch_is_rejected_before_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for extension in ("cmd", "bat"):
                with self.subTest(extension=extension):
                    launcher = root / f"custom.{extension}"
                    launcher.write_text('@echo {"source":"wrapper"}\n', encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, "--codex.*native"):
                        verifier.run_json(str(launcher), [], root, os.environ.copy())

    def make_sleeping_launcher(self, root: Path) -> Path:
        child = root / "child.py"
        child.write_text(
            "from pathlib import Path\n"
            "import os, time\n"
            "root = Path.cwd()\n"
            "handle = (root / 'child.lock').open('w')\n"
            "if os.name != 'nt':\n"
            "    import fcntl\n"
            "    fcntl.flock(handle, fcntl.LOCK_EX)\n"
            "(root / 'started').write_text('started')\n"
            "print('invalid JSON', flush=True)\n"
            "deadline = time.monotonic() + 15\n"
            "while not (root / 'release').exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.02)\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = root / "custom.cmd"
            launcher.write_text(f'@"{sys.executable}" "{child}"\n', encoding="utf-8")
        else:
            import shlex
            launcher = root / "custom.sh"
            launcher.write_text(
                f"#!/bin/sh\n{shlex.quote(sys.executable)} {shlex.quote(str(child))} &\nwait\n",
                encoding="utf-8",
            )
            launcher.chmod(0o700)
        return launcher

    def cleanup_fixture(self, temporary: tempfile.TemporaryDirectory) -> None:
        root = Path(temporary.name)
        if root.exists():
            (root / "release").touch()
            if os.name != "nt" and (root / "child.lock").is_file():
                self.assert_child_lock_released(root)
        # Release an old broken implementation's child before removing its cwd.
        deadline = time.monotonic() + 5
        while True:
            try:
                temporary.cleanup()
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)

    def assert_child_lock_released(self, root: Path) -> None:
        import fcntl
        with (root / "child.lock").open("a") as handle:
            deadline = time.monotonic() + 0.75
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        self.fail("Verifier child retained its lock after process cleanup")
                    time.sleep(0.01)

    def test_failed_inspection_releases_the_temporary_directory(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_fixture, temporary)
        root = Path(temporary.name)
        launcher = self.make_sleeping_launcher(root)
        expected = "--codex.*native" if os.name == "nt" else "invalid JSON"

        try:
            with self.assertRaisesRegex(RuntimeError, expected):
                verifier.inspect_host(str(launcher), root, os.environ.copy(), root)
        finally:
            # This is deliberately immediate: retries would mask an orphaned process.
            temporary.cleanup()

        self.assertFalse(root.exists())

    def test_native_command_timeout_releases_temporary_resources(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_fixture, temporary)
        root = Path(temporary.name)
        self.make_sleeping_launcher(root)

        with self.assertRaises(subprocess.TimeoutExpired):
            verifier.run_json(
                sys.executable, [str(root / "child.py")], root,
                os.environ.copy(), timeout=2.5,
            )

        self.assertTrue((root / "started").is_file())
        temporary.cleanup()
        self.assertFalse(root.exists())

    @unittest.skipIf(os.name == "nt", "POSIX owned process group regression")
    def test_run_json_timeout_releases_child_lock(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_fixture, temporary)
        root = Path(temporary.name)
        launcher = self.make_sleeping_launcher(root)

        with self.assertRaises(subprocess.TimeoutExpired):
            verifier.run_json(str(launcher), [], root, os.environ.copy(), timeout=0.5)

        self.assertTrue((root / "started").is_file())
        self.assert_child_lock_released(root)

    @unittest.skipIf(os.name == "nt", "POSIX owned process group regression")
    def test_failed_inspection_releases_child_lock(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_fixture, temporary)
        root = Path(temporary.name)
        launcher = self.make_sleeping_launcher(root)

        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            verifier.inspect_host(str(launcher), root, os.environ.copy(), root)

        self.assertTrue((root / "started").is_file())
        self.assert_child_lock_released(root)

    # Long paths: Windows without long-path support refuses a plain path of 260 characters. The
    # tests below hold on every system; the extended-length form they expect exists on Windows only.

    def events(self, seen: list, kind: str) -> list:
        return [event[1:] for event in seen if event[0] == kind]

    def test_long_path_helpers_keep_short_paths_and_switch_at_the_plain_limit(self) -> None:
        self.assertEqual(verifier.PLAIN_PATH_LIMIT, PLAIN_PATH_LIMIT)
        base = "C:\\t" if WINDOWS else "/t"
        below = base + os.sep + "n" * (PLAIN_PATH_LIMIT - 1 - len(base) - 1)
        at_limit = below + "n"
        short = base + os.sep + "work"
        room = PLAIN_PATH_LIMIT - len(short)
        relative = os.path.join("notes", "n" * PLAIN_PATH_LIMIT)

        def long_form(text: str) -> str:
            return PREFIX + text if WINDOWS else text

        self.assertEqual((len(below), len(at_limit)), (PLAIN_PATH_LIMIT - 1, PLAIN_PATH_LIMIT))
        # plain below the limit, extended at it, and the reserve counts
        self.assertEqual(str(verifier._workspace(below, 0)), below)
        self.assertEqual(str(verifier._workspace(at_limit, 0)), long_form(at_limit))
        self.assertEqual(str(verifier._workspace(short, room - 1)), short)
        self.assertEqual(str(verifier._workspace(short, room)), long_form(short))
        # the two forms undo each other and neither is applied twice
        self.assertEqual(str(verifier._extended(short)), long_form(short))
        self.assertEqual(str(verifier._extended(long_form(short))), long_form(short))
        self.assertEqual(str(verifier._plain(long_form(at_limit))), at_limit)
        self.assertEqual(str(verifier._plain(short)), short)
        # a relative path has no extended-length form
        self.assertEqual(str(verifier._workspace(relative, 0)), relative)
        self.assertEqual(str(verifier._extended(relative)), relative)
        if WINDOWS:
            share = "\\\\server\\share\\folder"
            self.assertEqual(str(verifier._extended(share)), PREFIX + "UNC\\server\\share\\folder")
            self.assertEqual(str(verifier._plain(PREFIX + "UNC\\server\\share\\folder")), share)
        # tests/_winfs.py carries the same helpers for fixtures; the two sets agree
        for sample in (below, at_limit, short, relative, long_form(at_limit)):
            with self.subTest(sample=sample[:12], length=len(sample)):
                self.assertEqual(verifier._extended(sample), extended(sample))
                self.assertEqual(verifier._plain(sample), plain(sample))
                for reserve in (0, room):
                    self.assertEqual(verifier._workspace(sample, reserve), deep_path(sample, reserve))

    def test_fitted_path_takes_the_form_its_own_length_calls_for(self) -> None:
        base = "C:\\t" if WINDOWS else "/t"
        below = base + os.sep + "n" * (PLAIN_PATH_LIMIT - 1 - len(base) - 1)
        at_limit = below + "n"
        for spelled in (plain, extended):  # whichever spelling it is given
            with self.subTest(spelled=spelled.__name__):
                self.assertEqual(str(verifier._fitted(spelled(below))), below)
                self.assertEqual(str(verifier._fitted(spelled(at_limit))), str(extended(at_limit)))

    def test_resolved_locates_a_deep_file_in_plain_form_from_either_spelling(self) -> None:
        with self.temporary_directory(2 * PLAIN_PATH_LIMIT) as temporary:
            target = self.long_folder(Path(temporary), PLAIN_PATH_LIMIT + 30) / "SKILL.md"
            extended(target).write_text("Deep fixture skill\n", encoding="utf-8")
            for spelled in (plain, extended):
                with self.subTest(spelled=spelled.__name__):
                    self.assertEqual(str(verifier._resolved(str(spelled(target)))), str(target))
                    self.assertTrue(verifier._fitted(str(spelled(target))).is_file())
        # a short path is resolved the way it always was
        self.assertEqual(verifier._resolved(os.curdir), Path(os.curdir).resolve())

    def test_workspace_reserve_is_the_cache_nesting_plus_the_longest_copied_path(self) -> None:
        with self.temporary_directory(FIXTURE_ROOM) as temporary:
            source = Path(temporary) / "source"
            longest = os.path.join("skills", "agentic-vault", "references", "codex.md")
            for relative in (os.path.join(".codex-plugin", "plugin.json"), os.path.join("docs", "guide.md"), longest):
                (source / relative).parent.mkdir(parents=True, exist_ok=True)
                (source / relative).write_text('{"version": "1.2.3"}', encoding="utf-8")
            nesting = os.path.join("codex-home", "plugins", "cache", "agentic-vault-local", "agentic-vault", "1.2.3")
            reserve = len(os.sep) + len(nesting) + len(os.sep) + len(longest)
            self.assertEqual(verifier._workspace_reserve(source), reserve)
            # what the copy leaves out does not count, however long its name
            left_out = source / ".git" / ("g" * len(longest)) / "config"
            left_out.parent.mkdir(parents=True)
            left_out.write_text("[core]\n", encoding="utf-8")
            (source / ("c" * len(longest) + ".pyc")).write_bytes(b"")
            self.assertEqual(verifier._workspace_reserve(source), reserve)
            # a longer file and a longer empty folder count in full
            grown = len("docs") + len(os.sep)
            (source / "docs" / ("n" * len(longest))).write_text("A longer path\n", encoding="utf-8")
            self.assertEqual(verifier._workspace_reserve(source), reserve + grown)
            (source / "docs" / ("f" * (len(longest) + 3))).mkdir()
            self.assertEqual(verifier._workspace_reserve(source), reserve + grown + 3)
            # an unreadable manifest only takes the version out of the bound; verify() reports it itself
            (source / ".codex-plugin" / "plugin.json").write_text("not JSON", encoding="utf-8")
            self.assertEqual(verifier._workspace_reserve(source), reserve + grown + 3 - len("1.2.3"))

    def test_workspace_parent_is_the_default_until_the_workspace_reaches_the_plain_limit(self) -> None:
        # Made-up temporary folders of exact lengths; nothing is created.
        anchor = plain(Path(tempfile.gettempdir()).resolve()).anchor
        reserve = 100
        name = len(os.sep) + len("agentic-vault-codex-") + 8  # the workspace folder tempfile names
        below = anchor + "f" * (PLAIN_PATH_LIMIT - 1 - reserve - name - len(anchor))
        at_limit = below + "f"
        with patch.object(tempfile, "tempdir", below):
            self.assertIsNone(verifier._workspace_parent(reserve))
        with patch.object(tempfile, "tempdir", at_limit):
            self.assertEqual(verifier._workspace_parent(reserve), PREFIX + at_limit if WINDOWS else None)
            self.assertIsNone(verifier._workspace_parent(reserve - 1))

    def test_host_receives_the_plain_workspace_paths_it_always_did_below_the_plain_limit(self) -> None:
        seen: list = []
        with tempfile.TemporaryDirectory() as temporary:
            source = self.make_plugin_fixture(Path(temporary))
            reserve = verifier._workspace_reserve(source)
            self.assertEqual(self.verify_fixture(source, seen=seen), FIXTURE_REPORT)
            # Below the plain limit a host answer is compared as it always was: the extended-length
            # spelling of a short installed path does not lie in the plain home (v0.52.0 refused it too).
            if WINDOWS:
                with self.assertRaises(ValueError):
                    self.verify_fixture(source, reported=extended)
            else:  # no other system has that spelling
                self.assertEqual(self.verify_fixture(source, reported=extended), FIXTURE_REPORT)
        (keywords, root), = self.events(seen, "workspace")
        # A workspace that stays below the plain limit is created by the call verify() always made.
        short = not WINDOWS or len(str(root)) + reserve < PLAIN_PATH_LIMIT
        self.assertEqual(keywords, {"prefix": "agentic-vault-codex-",
                                    "dir": None if short else PREFIX + str(root.parent)})
        (added, add_cwd, add_environment), (installed, install_cwd, install_environment) = self.events(seen, "run_json")
        self.assertEqual(added, ["plugin", "marketplace", "add", handed(root / "agentic-vault"), "--json"])
        self.assertEqual(installed, ["plugin", "add", "agentic-vault@agentic-vault-local", "--json"])
        for cwd, environment in ((add_cwd, add_environment), (install_cwd, install_environment)):
            self.assertEqual(str(cwd), handed(root / "test vault"))
            self.assertEqual(environment["CODEX_HOME"], handed(root / "codex-home"))
            self.assertEqual(environment["PYTHONUTF8"], "1")
        (vault, home, installed_root), = self.events(seen, "inspect_host")
        self.assertEqual((str(vault), str(home)), (handed(root / "test vault"), handed(root / "codex-home")))
        self.assertEqual(str(installed_root), str(root / "codex-home" / "plugins" / "cache" / "fixture"))
        # Nothing the host receives carries the extended-length prefix below the plain limit.
        for text in (added[3], str(add_cwd), add_environment["CODEX_HOME"]):
            plain_length = len(text) - (len(PREFIX) if text.startswith(PREFIX) else 0)
            self.assertEqual(text.startswith(PREFIX), WINDOWS and plain_length >= PLAIN_PATH_LIMIT, text)

    def test_long_workspace_is_verified_and_removed_whatever_the_temp_length(self) -> None:
        # The workspace is created below a folder of at least 190 characters, so its copies pass
        # MAX_PATH under any TEMP: on Windows this run always takes the extended-length form. The
        # installed path the host reports then reaches the plain limit itself, so it is located by
        # its plain path in either spelling.
        with self.temporary_directory(2 * PLAIN_PATH_LIMIT) as temporary, \
             tempfile.TemporaryDirectory() as fixture:
            source = self.make_plugin_fixture(Path(fixture))
            reserve = verifier._workspace_reserve(source)
            folder = self.long_folder(Path(temporary), 190)
            for reported in (plain, extended):
                with self.subTest(reported=reported.__name__):
                    seen: list = []
                    with patch.object(tempfile, "tempdir", str(folder)):
                        report = self.verify_fixture(source, seen=seen, reported=reported)
                    self.assertEqual(report, FIXTURE_REPORT)
                    self.assertEqual(os.listdir(extended(folder)), [])
                    (keywords, root), = self.events(seen, "workspace")
                    self.assertEqual(root.parent, folder)
                    self.assertEqual(keywords, {"prefix": "agentic-vault-codex-",
                                                "dir": PREFIX + str(folder) if WINDOWS else None})
                    (added, cwd, environment), _ = self.events(seen, "run_json")
                    self.assertEqual(
                        (added[3], str(cwd), environment["CODEX_HOME"]),
                        (handed(root / "agentic-vault"), handed(root / "test vault"), handed(root / "codex-home")))
                    (_, _, installed_root), = self.events(seen, "inspect_host")
                    self.assertEqual(str(installed_root), str(root / "codex-home" / "plugins" / "cache" / "fixture"))
                    self.assertGreaterEqual(len(str(installed_root)), PLAIN_PATH_LIMIT)
                    (longest,), = self.events(seen, "installed")
                    self.assertGreater(longest, 260)  # beyond MAX_PATH, out of reach of the plain form
                    self.assertLessEqual(longest, len(str(root)) + reserve)  # and within the reserve
            with self.subTest(installed="missing"):
                seen = []

                def remove_resource(cached):
                    (cached / "hooks" / "hooks.json").unlink()

                with patch.object(tempfile, "tempdir", str(folder)), self.assertRaises(FileNotFoundError) as raised:
                    self.verify_fixture(source, mutate_cache=remove_resource, seen=seen)
                self.assertEqual(os.listdir(extended(folder)), [])
                (_, root), = self.events(seen, "workspace")
                missing = root / "codex-home" / "plugins" / "cache" / "fixture" / "hooks" / "hooks.json"
                self.assertEqual(
                    verifier._shown(raised.exception),
                    str(FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(missing))))

    def test_workspace_that_cannot_be_removed_is_reported_whatever_its_length(self) -> None:
        # An open file blocks the removal on Windows only. verify() then raises, as it always did,
        # for the workspace below the ambient TEMP and for one that needs the extended-length form.
        with self.temporary_directory(2 * PLAIN_PATH_LIMIT) as temporary, \
             tempfile.TemporaryDirectory() as fixture:
            source = self.make_plugin_fixture(Path(fixture))
            long_parent = self.long_folder(Path(temporary), 170)
            for label, folder in (("ambient", tempfile.gettempdir()), ("long", str(long_parent))):
                with self.subTest(workspace=label):
                    seen: list = []
                    held: list = []

                    def hold_open(cached):
                        held.append((cached / "hooks" / "hooks.json").open("rb"))

                    try:
                        with patch.object(tempfile, "tempdir", folder):
                            if WINDOWS:
                                with self.assertRaises(OSError):
                                    self.verify_fixture(source, mutate_cache=hold_open, seen=seen)
                            else:
                                self.assertEqual(
                                    self.verify_fixture(source, mutate_cache=hold_open, seen=seen), FIXTURE_REPORT)
                    finally:
                        for handle in held:
                            handle.close()
                    (_, root), = self.events(seen, "workspace")
                    self.assertEqual(extended(root).exists(), WINDOWS)
                    if WINDOWS:
                        shutil.rmtree(extended(root))

    def test_workspace_at_the_plain_limit_reaches_the_host_in_extended_form(self) -> None:
        # Only a workspace whose own folders reach the plain limit is handed over in extended-length form.
        with self.temporary_directory(2 * PLAIN_PATH_LIMIT) as temporary, \
             tempfile.TemporaryDirectory() as fixture:
            source = self.make_plugin_fixture(Path(fixture))
            folder = self.long_folder(
                Path(temporary), PLAIN_PATH_LIMIT - len(os.sep) - len("agentic-vault-codex-") - 8)
            seen: list = []
            with patch.object(tempfile, "tempdir", str(folder)):
                self.assertEqual(self.verify_fixture(source, seen=seen), FIXTURE_REPORT)
            self.assertEqual(os.listdir(extended(folder)), [])
        (_, root), = self.events(seen, "workspace")
        self.assertGreaterEqual(len(str(root)), PLAIN_PATH_LIMIT)
        (added, cwd, environment), _ = self.events(seen, "run_json")
        self.assertEqual(
            (added[3], str(cwd), environment["CODEX_HOME"]),
            tuple(str(extended(root / name)) for name in ("agentic-vault", "test vault", "codex-home")))
        self.assertEqual(added[3].startswith(PREFIX), WINDOWS)
        (vault, home, installed_root), = self.events(seen, "inspect_host")
        self.assertEqual((vault, home), (cwd, Path(environment["CODEX_HOME"])))
        self.assertEqual(str(installed_root), str(root / "codex-home" / "plugins" / "cache" / "fixture"))

    def test_deep_skill_paths_are_checked_in_whichever_form_the_host_reports_them(self) -> None:
        for deep_as in (plain, extended):
            for bind_installed_root in (True, False):
                with self.subTest(reported=deep_as.__name__, bind_installed_root=bind_installed_root):
                    report = self.inspect_discovery_fixture(deep_as=deep_as, bind_installed_root=bind_installed_root)
                    self.assertEqual(report["skills"],
                                     ["agentic-vault:agentic-vault"])
            with self.subTest(reported=deep_as.__name__, mutation="other-package"):
                def move_to_another_package(skills):
                    expected = extended(skills[0]["path"])
                    wrong = expected.parents[2].with_name("other-package") / "skills/agentic-vault/SKILL.md"
                    wrong.parent.mkdir(parents=True)
                    wrong.write_bytes(expected.read_bytes())
                    skills[0]["path"] = str(deep_as(wrong))

                with self.assertRaisesRegex(RuntimeError, "skill"):
                    self.inspect_discovery_fixture(mutate_skills=move_to_another_package, deep_as=deep_as)
            with self.subTest(reported=deep_as.__name__, mutation="missing"):
                def remove_skill_file(skills):
                    extended(skills[0]["path"]).unlink()

                with self.assertRaisesRegex(RuntimeError, "skill"):
                    self.inspect_discovery_fixture(mutate_skills=remove_skill_file, deep_as=deep_as)

    def test_a_short_answer_in_the_extended_spelling_is_compared_as_before(self) -> None:
        short = ROOT / "docs"
        self.assertLess(len(str(short)), PLAIN_PATH_LIMIT)
        answer = verifier._resolved(str(extended(short)))
        # v0.52.0 compared Path(answer).resolve(), which keeps the prefix of the spelling
        self.assertEqual(answer, Path(str(extended(short))).resolve())
        self.assertEqual(answer == plain(short.resolve()), not WINDOWS)
        self.assertEqual(verifier._resolved(str(short)), short.resolve())

    def test_a_long_plain_answer_that_leads_to_a_short_folder_is_compared_plainly(self) -> None:
        # On Windows the tool collapses the '..' of a plain drive spelling of 240 or more characters as
        # resolving a plain path does, before it would read the spelling in the extended-length form (Python
        # 3.10 keeps '..' in an extended-length path); elsewhere Path.resolve() collapses it. Where '..' leads
        # back to a short folder, the answer is read and located by its plain path, as v0.52.0 did.
        short = ROOT / "docs"
        spelling = os.path.join(str(short), "d" * 130, "e" * 130, os.pardir, os.pardir)
        self.assertGreaterEqual(len(spelling), PLAIN_PATH_LIMIT)
        if WINDOWS:
            self.assertEqual(verifier._answer(spelling), short)
        self.assertEqual(verifier._resolved(spelling), short.resolve())

    def test_an_extended_answer_keeps_its_literal_names(self) -> None:
        # Windows drops a trailing dot or space from a plain name but not from an extended-length one,
        # so a sibling such as 'codex-home.' must not be taken for the home, nor 'SKILL.md.' for SKILL.md.
        with self.temporary_directory(2 * PLAIN_PATH_LIMIT) as temporary:
            folder = self.long_folder(Path(temporary), PLAIN_PATH_LIMIT + 10)
            home = folder / "codex-home"
            extended(home).mkdir()
            (extended(home) / "SKILL.md").write_text("skill\n", encoding="utf-8")
            for name in ("codex-home.", "codex-home "):
                with self.subTest(name=name):
                    sibling = extended(folder) / name
                    sibling.mkdir()
                    self.assertNotEqual(verifier._resolved(str(sibling)), plain(home))
            self.assertEqual(verifier._resolved(str(extended(home))), plain(home))
            self.assertEqual(verifier._resolved(str(home)), plain(home))
            self.assertTrue(verifier._answer(str(extended(home) / "SKILL.md")).is_file())
            self.assertFalse(verifier._answer(str(extended(home) / "SKILL.md.")).is_file())

    def test_a_copy_failure_names_its_files_in_plain_form(self) -> None:
        destination = os.path.join(os.path.abspath(os.sep), "work", "n" * PLAIN_PATH_LIMIT, "hooks.json")
        source = os.path.join(os.path.abspath(os.sep), "src", "hooks.json")

        def failure(spelled):
            reason = str(PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(spelled(destination))))
            return shutil.Error([(source, str(spelled(destination)), reason)])

        for spelled in (plain, extended):
            with self.subTest(spelled=spelled.__name__), \
                 patch.object(verifier.shutil, "copytree", side_effect=failure(spelled)), \
                 self.assertRaises(shutil.Error) as raised:
                verifier.verify("unused-fixture-executable")
            self.assertEqual(str(raised.exception), str(failure(plain)))
            self.assertNotIn(PREFIX, str(raised.exception))

    def test_failure_message_names_a_file_in_plain_form(self) -> None:
        location = os.path.join(os.path.abspath(os.sep), "work", "n" * PLAIN_PATH_LIMIT, "hooks.json")
        reason = os.strerror(errno.ENOENT)
        cases = [(FileNotFoundError(errno.ENOENT, reason, str(spelled(location))),
                  str(FileNotFoundError(errno.ENOENT, reason, location))) for spelled in (plain, extended)]
        # any other message is printed as it is
        cases.append((FileNotFoundError(errno.ENOENT, reason, "missing/resource.json"),
                      f"[Errno {errno.ENOENT}] {reason}: 'missing/resource.json'"))
        cases.append((RuntimeError("Installed resource differs: hooks/hooks.json"),
                      "Installed resource differs: hooks/hooks.json"))
        for error, message in cases:
            with self.subTest(error=repr(error)[:60]):
                printed = io.StringIO()
                with patch.object(verifier, "verify", side_effect=error), \
                     patch.object(sys, "argv", ["verify_codex_plugin.py", "--codex", "codex"]), \
                     contextlib.redirect_stderr(printed):
                    self.assertEqual(verifier.main(), 1)
                self.assertEqual(printed.getvalue(), f"Codex plugin verification failed: {message}\n")


if __name__ == "__main__":
    unittest.main()
