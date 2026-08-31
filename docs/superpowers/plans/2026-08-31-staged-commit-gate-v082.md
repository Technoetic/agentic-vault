# Staged Commit Gate v0.8.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the self-contained shell linter with a deterministic Git-index Python gate that handles Korean paths, renames, staged configuration, and deletion backlinks.

**Architecture:** Extend the shared healthcheck with validated config and a write-free `--staged` mode. Install that engine into each vault and keep pre-commit as a fail-closed launcher whose only policy is locating Python and propagating the engine exit code.

**Tech Stack:** Python 3.10+ standard library, Git CLI, POSIX `sh`, `unittest` temporary repositories.

**Spec:** `docs/superpowers/specs/2026-08-31-staged-commit-gate-design.md`

## Global Constraints

- All staged decisions read Git index content, never unstaged working-tree content.
- `deny_zones` and `exclude_dirs` are pruned through Git pathspec exclusions and not enumerated.
- `--staged` writes no health report.
- Existing full mode remains compatible with existing config files.
- Config parse/type/path failures are fail-closed.
- Hook bypass remains possible and must not be described as impossible.

---

### Task 1: Validated config and NUL-safe staged Git primitives

**Files:**
- Create: `tests/test_vault_healthcheck.py`
- Modify: `skills/agentic-vault/scripts/vault_healthcheck.py:1-250`

**Interfaces:**
- Produces: `HealthcheckError`, `VaultPolicy`, `StagedChange`, `validate_config(raw) -> dict`, `load_staged_config(vault) -> dict`, `list_staged_changes(vault, config) -> list[StagedChange]`, `read_index_text(vault, path) -> str`.
- Consumes: Git CLI and `00-meta/vault-config.json`.

- [ ] **Step 1: Add failing config and Git-index tests**

Create temporary Git repositories with local identity. Tests must cover UTF-8 BOM config, staged config differing from working tree, Korean/space filenames, A/C/M/R/D NUL records, unborn HEAD, absolute paths, drive/UNC paths, `..`, wrong list/dict/integer types, Git subprocess failure, and staged config deletion. Assert config path escapes fail while an explicit CLI `--output` outside the vault remains allowed.

```python
def test_staged_config_wins_over_worktree(self):
    self.write_config({"required_keys": ["title"]})
    self.git("add", "00-meta/vault-config.json")
    self.write_config({"required_keys": []})
    self.assertEqual(load_staged_config(self.repo)["required_keys"], ["title"])

def test_name_status_z_parses_korean_rename(self):
    self.write("20-knowledge/이전 노트.md", self.valid_note("이전 노트"))
    self.commit_all("seed")
    self.git("mv", "20-knowledge/이전 노트.md", "20-knowledge/새 노트.md")
    changes = list_staged_changes(self.repo, load_staged_config(self.repo))
    self.assertIn(StagedChange("R", "20-knowledge/새 노트.md",
                               "20-knowledge/이전 노트.md"), changes)

def test_config_rejects_path_escape(self):
    for value in ("../outside.md", "C:/outside.md", "/outside.md"):
        with self.subTest(value=value), self.assertRaises(HealthcheckError):
            validate_config(self.base_config(handoff_note=value))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_vault_healthcheck.ConfigAndGitIndexTests -v`

Expected: missing types/functions.

- [ ] **Step 3: Implement validated config and Git helpers**

Use `dataclasses.dataclass(frozen=True)` for policy/change types, `utf-8-sig` for config, and `subprocess.run(args, stdout=PIPE, stderr=PIPE, check=False, timeout=30)` with bytes output for `-z` parsing. Reject absolute paths, UNC prefixes, `^[A-Za-z]:`, empty segments, and `..` before constructing config `Path` values. A non-zero/timeout Git command raises `HealthcheckError`; it never becomes an empty successful result. Pass `--find-renames` explicitly.

Defaults:

```python
"frontmatter_roots": ["00-meta", "20-knowledge", "30-journal", "40-people", "50-projects"],
"frontmatter_exempt_paths": ["00-meta/scratch", "00-meta/scripts", "10-inbox"],
```

Treat legacy `fm_exempt_zones` as an alias when the new key is absent. Preserve legacy full-mode “all active notes” behavior when `frontmatter_roots` is absent; staged mode uses the five defaults. Add `ENGINE_VERSION = "0.8.2"` and an `agentic-vault:healthcheck engine=0.8.2` header comment.

- [ ] **Step 4: Run focused tests and existing full smoke GREEN**

Run: `python -m unittest tests.test_vault_healthcheck.ConfigAndGitIndexTests -v`

Run: `python skills/agentic-vault/scripts/vault_healthcheck.py --vault .`

Expected: focused tests pass; repository without vault config exits 0 with the existing non-vault message.

- [ ] **Step 5: Commit**

```powershell
git add -- tests/test_vault_healthcheck.py skills/agentic-vault/scripts/vault_healthcheck.py
git commit -m "refactor: validate vault policy and staged Git input"
```

### Task 2: Staged schema and deletion-backlink gate

**Files:**
- Modify: `tests/test_vault_healthcheck.py`
- Modify: `skills/agentic-vault/scripts/vault_healthcheck.py`

**Interfaces:**
- Consumes: Task 1 policy/change/index helpers and existing frontmatter/link parsers.
- Produces: `validate_staged(vault, config) -> list[str]` and Git pathspec exclusions.

- [ ] **Step 1: Add failing staged-rule tests**

Tests must prove: staged content beats dirty content; required frontmatter/keys/Enum/unquoted links block only configured roots; exempt paths pass; untouched legacy violations pass; renamed and copied new content is checked; copy does not delete the old stem; directory-only rename with the same stem passes; old changed stem backlinks block in all four forms (`[[A]]`, alias, anchor, folder path); regex and Korean stems work; same-commit backlink repair passes; another result-index note with the same stem keeps links valid; log/health referrers are excluded; deny/exclude content is not returned by Git searches; non-Markdown commit still validates config; `--staged` creates no report.

```python
def test_rename_blocks_old_stem_backlink_until_repaired(self):
    self.seed_notes({"20-knowledge/A.md": self.valid_note("A"),
                     "20-knowledge/Ref.md": self.valid_note("Ref", body="[[A]]")})
    self.git("mv", "20-knowledge/A.md", "20-knowledge/B.md")
    errors = validate_staged(self.repo, load_staged_config(self.repo))
    self.assertTrue(any("Ref.md" in error and "A" in error for error in errors))
    self.write("20-knowledge/Ref.md", self.valid_note("Ref", body="[[B]]"))
    self.git("add", "20-knowledge/Ref.md")
    self.assertEqual(validate_staged(self.repo, load_staged_config(self.repo)), [])

def test_deny_zone_is_excluded_from_backlink_search(self):
    self.seed_notes({"20-knowledge/A.md": self.valid_note("A"),
                     "20-knowledge/_archive/비밀.md": self.valid_note("비밀", body="[[A]]")})
    self.git("rm", "20-knowledge/A.md")
    errors = validate_staged(self.repo, load_staged_config(self.repo))
    self.assertFalse(any("_archive" in error or "비밀.md" in error for error in errors))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_vault_healthcheck.StagedGateTests -v`

Expected: `validate_staged` missing or current behavior fails assertions.

- [ ] **Step 3: Implement minimal staged gate**

Build pathspecs beginning with `:(top,glob)**/*.md`, followed by `:(exclude,top,glob)<deny>/**` and name-based `:(exclude,glob)**/<exclude>/**`. Use `git grep --cached -z -l -I -E` against the index tree. Treat rename as deletion only when the stem changes, and copy as new-content validation only. Before backlink failure, query result-index paths to see whether another Markdown file with the old stem remains. Return stable, sorted Korean diagnostics; do not write report files.

- [ ] **Step 4: Run focused and full healthcheck tests GREEN**

Run: `python -m unittest tests.test_vault_healthcheck -v`

Expected: all healthcheck tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- tests/test_vault_healthcheck.py skills/agentic-vault/scripts/vault_healthcheck.py
git commit -m "feat: add Git-index staged vault gate"
```

### Task 3: Thin fail-closed hook and upgrade installation

**Files:**
- Create: `tests/test_git_hooks.py`
- Modify: `assets/git-hooks/pre-commit`
- Modify: `assets/git-hooks/pre-push`
- Modify: `commands/vault-init.md`
- Modify: `commands/vault-upgrade.md`
- Modify: `assets/templates/vault-config.json`

**Interfaces:**
- Consumes: installed `00-meta/scripts/vault_healthcheck.py --staged` from Tasks 1-2.
- Produces: engine-stamped hook/install contract.

- [ ] **Step 1: Add failing wrapper integration tests**

```python
def test_precommit_propagates_checker_failure(self):
    self.install_checker("import sys; sys.exit(23)\n")
    result = self.run_hook()
    self.assertEqual(result.returncode, 23)

def test_precommit_fails_when_checker_missing(self):
    result = self.run_hook()
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("staged 검사기 없음", result.stderr)

def test_precommit_uses_python3_fallback(self):
    self.install_checker("import sys; sys.exit(0)\n")
    result, argv = self.run_hook_with_fake_python3()
    self.assertEqual(result.returncode, 0)
    self.assertEqual(argv[-3:], ["--vault", ".", "--staged"])

def test_hook_files_have_engine_stamp_and_shell_syntax(self):
    for hook in (Path("assets/git-hooks/pre-commit"), Path("assets/git-hooks/pre-push")):
        self.assertIn("engine=0.8.2", hook.read_text(encoding="utf-8"))
        self.assertEqual(subprocess.run(["sh", "-n", str(hook)]).returncode, 0)
```

Add text-contract tests asserting init copies the healthcheck into `00-meta/scripts/` before hooks and hooksPath activation; upgrade documents missing/unversioned/lower/equal/higher stamps, compares engine stamps, preserves custom edits, and does not replace an unrelated existing `core.hooksPath`; template config contains the two new path arrays.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_git_hooks -v`

Expected: old self-contained hook and missing install instructions fail.

- [ ] **Step 3: Replace the hook and update install contracts**

Use this wrapper shape:

```sh
#!/bin/sh
# agentic-vault:hook engine=0.8.2
SCRIPT="$(dirname "$0")/../vault_healthcheck.py"
if command -v python >/dev/null 2>&1; then PYTHON=python
elif command -v python3 >/dev/null 2>&1; then PYTHON=python3
else echo "🚫 [pre-commit] python을 찾을 수 없습니다." >&2; exit 1
fi
[ -f "$SCRIPT" ] || { echo "🚫 [pre-commit] staged 검사기 없음: $SCRIPT" >&2; exit 1; }
"$PYTHON" "$SCRIPT" --vault . --staged
exit $?
```

Stamp pre-push without changing policy. Add `frontmatter_roots` and `frontmatter_exempt_paths` to the template. Init copies the engine from `${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/vault_healthcheck.py` before hook files and hooksPath; upgrade replaces only stamped engine-owned older versions after showing custom diffs. Unversioned/custom hook or engine files are never overwritten automatically, and a different existing `core.hooksPath` requires explicit user confirmation.

- [ ] **Step 4: Run hook tests and syntax checks GREEN**

Run: `python -m unittest tests.test_git_hooks -v`

Run: `sh -n assets/git-hooks/pre-commit`

Run: `sh -n assets/git-hooks/pre-push`

Expected: all commands exit 0.

- [ ] **Step 5: Preserve executable bits and commit**

```powershell
git add -- tests/test_git_hooks.py assets/git-hooks/pre-commit assets/git-hooks/pre-push commands/vault-init.md commands/vault-upgrade.md assets/templates/vault-config.json
git update-index --chmod=+x -- assets/git-hooks/pre-commit assets/git-hooks/pre-push
git commit -m "fix: install thin fail-closed vault hooks"
```

### Task 4: Cross-mode regression and documentation

**Files:**
- Modify: `tests/test_vault_healthcheck.py`
- Modify: `tests/test_git_hooks.py`
- Modify: `README.md`
- Modify: `commands/vault-lint.md`

**Interfaces:**
- Consumes: all staged gate tasks.
- Produces: documented separation between full report mode and staged blocking mode.

- [ ] **Step 1: Add failing CLI contract test**

Invoke the script as a subprocess in an initialized temp vault and assert full mode writes the configured report while staged mode writes nothing and returns 0/1 solely from staged violations.

- [ ] **Step 2: Run the CLI test and verify RED**

Run: `python -m unittest tests.test_vault_healthcheck.CliModeTests -v`

Expected: missing/incorrect staged CLI behavior fails.

- [ ] **Step 3: Wire `--staged` CLI and update documentation**

Add an argparse mutually exclusive mode, emit each staged diagnostic to stderr, and return 1 when diagnostics exist. README and `/vault-lint` must describe hooks as local enforcement that can be bypassed by `--no-verify`, not a security sandbox.

- [ ] **Step 4: Run all gate tests GREEN**

Run: `python -m unittest tests.test_vault_healthcheck tests.test_git_hooks -v`

Run: `git diff --check`

Expected: all tests pass; no whitespace errors.

- [ ] **Step 5: Commit**

```powershell
git add -- tests/test_vault_healthcheck.py tests/test_git_hooks.py README.md commands/vault-lint.md skills/agentic-vault/scripts/vault_healthcheck.py
git commit -m "docs: define staged and full vault validation"
```
