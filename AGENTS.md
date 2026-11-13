# Working on the agentic-vault repository

This guide is for people and coding agents who change this plugin repository. It does not apply to an
Obsidian vault or to a session that only uses the installed plugin: a vault has its own contract
(`CLAUDE.md`, `AGENTS.md` and `.claude/rules/` written by `/vault-init`). If you reached this file from a
vault session, ignore it. `.claude/CLAUDE.md` imports this file for Claude Code; Codex reads it
directly. Antigravity CLI reads it too when a session starts inside the checkout.

## Who decides what

- The maintainer owns the design of the plugin, the release decisions and what is published.
- A contributor or agent changes what the task asks for. Keep the diff small. Leave unrelated code, wording
  and structure as they are unless the task asks for a restructuring. If a fix seems to need a wider
  change, say so and ask before making it.
- Several rules are copied on purpose into files that run alone (next section). Do not merge, share by
  import or deduplicate those copies.
- Do not push, tag, create or edit releases, or change repository settings unless the maintainer asks for
  that step.

## Deliberate verbatim copies

Some engine files run without the plugin's other modules on `sys.path`, so they cannot import the module
that owns a rule and keep an exact copy of it instead:

- `skills/agentic-vault/scripts/vault_healthcheck.py` is copied into each git vault as
  `00-meta/scripts/vault_healthcheck.py` and run alone by the pre-commit hook.
- `assets/adapters/deny-guard/deny-guard.py` is copied into a vault and run alone by a Claude Code
  `PreToolUse` hook.
- `skills/agentic-vault/scripts/vault_lint_extensions.py` must not import `vault_paths.py`: a vault copy set
  without it would stop the health check at import.
- `skills/agentic-vault/scripts/vault_ssot.py` imports `vault_state.py`, so `vault_state.py` cannot import it
  back; the heading helper stays in `vault_ssot.py` because the warning-promotion certificate binds that file.

A test compares each copy with its source and fails on any difference. All paths below are under
`skills/agentic-vault/scripts/` unless they start with `assets/`.

| Source | Copy | What must be identical | Test |
| --- | --- | --- | --- |
| `vault_paths.py`, between `# --- BEGIN SHARED PATH RULE` and `# --- END SHARED PATH RULE ---` | `vault_healthcheck.py`, same markers | the text after the BEGIN line up to the END line | `tests/test_rule_sources.py`: `PathRuleSourceTests.test_healthcheck_carries_a_verbatim_copy_of_the_shared_rule` |
| `vault_paths.fold_path_part` and `vault_paths.zone_matches` | `assets/adapters/deny-guard/deny-guard.py`, between `# --- BEGIN SHARED ZONE RULE` and `# --- END SHARED ZONE RULE ---` | the source of the two functions, joined by one blank line | `tests/test_rule_sources.py`: `PathRuleSourceTests.test_deny_guard_carries_verbatim_copies_of_fold_path_part_and_zone_matches` |
| `vault_paths.fold_path_part` | `fold_path_part` in `vault_healthcheck.py` and in `vault_lint_extensions.py` (no markers) | the function source (ASCII) | `tests/test_rule_sources.py`: `PathRuleSourceTests.test_fold_path_part_has_one_source_and_verbatim_copies` |
| `vault_worktrees.py`, between `# --- BEGIN SHARED WORKTREE RULE` and `# --- END SHARED WORKTREE RULE ---` | `vault_healthcheck.py` and `assets/adapters/deny-guard/deny-guard.py`, same markers | the text after the BEGIN line up to the END line | `tests/test_vault_worktrees.py`: `SharedBlockTests.test_the_guard_and_the_healthcheck_carry_a_verbatim_copy` |
| `vault_ssot.py`, between `# --- BEGIN ATX HEADING (verbatim copy) ---` and `# --- END ATX HEADING (verbatim copy) ---` | `vault_state.py`, same markers | the bytes from the BEGIN marker up to the END marker | `tests/test_rule_sources.py`: `AtxHeadingSourceTests.test_state_carries_a_verbatim_copy_of_the_ledger_heading_rule` |

The same tests also fix who may hold each rule (`test_no_other_script_reimplements_the_segment_rule`,
`AtxHeadingSourceTests.test_no_other_script_carries_the_heading_block`, and the owner lists in the two
`fold_path_part`/`zone_matches` tests). The outbound secret pattern has one owner, `jev_client.py`
(`SecretFilterSourceTests.test_no_other_script_defines_its_own_secret_pattern`). Two pairs must agree without
being copies: the deny guard's fallback zones equal the health check's default zones
(`PathRuleSourceTests.test_deny_guard_fallback_zones_match_the_healthcheck_default`), and `ps-parse.ps1`
carries the same stamp as `deny-guard.py` (`tests/test_vault_adapters.py`:
`test_deny_guard_parser_is_ascii_and_stamped_like_the_guard`). `lesson-cues.py` and `aside-up.ps1` also run
alone but hold no copy.

To change a copied rule:

1. Change the source.
2. Copy the changed block or function into every copy byte for byte. Copy the text; do not retype or
   reformat it, and keep LF line ends (`*.py text eol=lf` in `.gitattributes`).
3. Run `python -m unittest tests.test_rule_sources tests.test_vault_worktrees -v` and the tests of the
   behaviour you changed (for zones: `tests.test_zone_normalization`, `tests.test_deny_guard`,
   `tests.test_vault_lint_extensions`).
4. If a copy or its source is one of the certificate-bound files (below), the certificate changes as well.
5. If `deny-guard.py` changed, its stamp and the stamp of `ps-parse.ps1` move to the release version together.

### Generated Antigravity CLI package

`antigravity/` is generated: `python scripts/build_antigravity_package.py build` writes it from the
repository for Antigravity CLI. It holds byte copies of the engine scripts under
`skills/agentic-vault/scripts/`, of four hook files (`session_start.py`, `session_checkpoint.py`,
`antigravity_hook.py`, `run_python_hook.sh`) and of `assets/`, and rewritten copies of the commands (one
skill each), the shared skills and the documents they link. The root `AGENTS.md` and `.claude/CLAUDE.md`
are not copied into it.

Do not edit anything under `antigravity/` by hand. Change the source, rebuild with
`python scripts/build_antigravity_package.py build`, confirm with
`python scripts/build_antigravity_package.py check` (it prints every difference and exits 1 on any) and
commit the rebuilt folder with the source change. The sync test in `tests/test_antigravity_package.py`
runs the same check. It is the only test that keeps these copies equal to their sources: the copy tests
above do not read `antigravity/`.

## Engine files and vault data

Everything in this repository is engine: commands, skills, hooks, templates, adapters and scripts. A change to
any of them changes what agents do in every vault after the update.

- `/vault-init` and `/vault-upgrade` copy `vault_healthcheck.py` and its helper bundle (`vault_*.py`,
  `jev_client.py`, `jev_ask.py`, `resources/lint-benign-v1.json`) to the vault's `00-meta/scripts/`, the git
  hooks to `00-meta/scripts/git-hooks/` and the rule templates to `.claude/rules/vault-*.md`. After the user
  approves, they also copy the adapters (`aside-up.ps1`, `deny-guard.py` with `ps-parse.ps1`, `lesson-cues.py`)
  and the `/aside-repl` procedure command (`.claude/commands/aside-repl.md`).
- Every copied file except the helper bundle carries a stamp with `engine=X.Y.Z` at its head: on the first
  line, on the second line after a `#!` line (the health check and the git hooks), or right after the front
  matter (the `/aside-repl` command). `/vault-upgrade` and doctor compare it with the bundled file to offer an
  update. The helper bundle carries no stamp; `/vault-upgrade` replaces a helper only when its hash matches
  the previous install.
- Notes, `00-meta/vault-config.json`, the handoff and the ledgers in a vault are the user's data. The plugin
  ships no vault data. Tests build synthetic vaults in temporary folders; `tests/fixtures/` holds synthetic
  data only.

## Release rules

- Every change that alters a result for an existing vault or user without a new option is listed in the
  release note's `기본 결과 변경` section, with what changes and for whom.
- A changed rule template or adapter gets `engine=<release version>` on its stamp line; an unchanged one
  keeps its stamp. The health check's stamp line and `ENGINE_VERSION` follow the manifest version. Tests pin
  stamps (`tests/test_release_metadata.py`, `tests/test_vault_adapters.py`, `tests/test_git_hooks.py`).
- The warning-promotion certificate's `checker_sha256` hashes the bytes of seven files: `vault_lint_extensions.py`,
  `vault_warning_policy.py`, `vault_provenance.py`, `vault_egress.py`, `vault_ssot.py`, `jev_client.py` and
  `vault_paths.py`. Any byte change in them changes the fingerprint and stops vaults that promote warnings
  until they follow the upgrade order. Such a change needs the maintainer's decision, the new value in
  `tests/test_release_metadata.py` and the release documents.
- The four manifests (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
  `.codex-plugin/plugin.json` and the generated `antigravity/plugin.json`, which takes its version from
  `.claude-plugin/plugin.json` when the package is rebuilt), the health check stamp, `README.md`,
  `docs/codex.md` and `SECURITY.md` move to a new version together; `tests/test_release_metadata.py` and
  `tests/test_readme_truth.py` check them.
- `README.md` is bilingual: a release adds a Korean section and an English section. Korean documents stay
  Korean and English documents stay English.
- Release notes (`docs/releases/`) and verification records (`docs/verification/`) of a published version keep
  their published text. A correction is a dated errata section appended at the end of the file.
- Public files contain no personal names, company or customer names, machine paths, e-mail addresses
  (except reserved example domains), tokens or internal system names. Use neutral example paths.
- The runtime is the Python 3.10+ standard library. Default paths make no model or network calls.

## Running the tests

CI runs these commands, among others (`.github/workflows/tests.yml`); on Windows set `PYTHONUTF8=1` as CI does.

```text
python -m unittest discover -s tests -v
python -m compileall -q hooks skills/agentic-vault/scripts scripts
git diff --check 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD
```

To check only the copied rules: `python -m unittest tests.test_rule_sources -v` (the full suite includes it).

The full suite takes tens of minutes on Windows. The stateful lifecycle tests need the development
dependencies: `python -m pip install -r requirements-dev.txt`, then
`python -m unittest tests.test_stateful_memory_lifecycle tests.test_stateful_relation_probes tests.test_stateful_revocation_egress -v`.
The evaluation smoke commands are the other steps of the `unittest` job in the workflow file.

Tests do not assert on elapsed time, write nothing inside the repository, and build vaults and git
repositories in temporary folders. Use `tests/_git_env.py` to keep a developer's git settings out of test
repositories, and switch off git's automatic maintenance (`maintenance.auto=false`, `gc.auto=0`) in a test
repository that another test copies.
