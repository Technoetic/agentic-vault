# Operator deny-rule & hook hash drift audit (`vault_policy_drift.py`)

`skills/agentic-vault/scripts/vault_policy_drift.py` is an opt-in, offline, standard-library helper added in `v0.60.0` (inspired by [arXiv:2611.32195](https://arxiv.org/abs/2611.32195) on silent removal of operator `deny` rules via permission replies and [arXiv:2611.01976](https://arxiv.org/abs/2611.01976) on post-trust mutations in external tool/hook updates).

```sh
# 1. Capture a sealed, content-free baseline (writes 00-meta/.vault-policy-baseline.json)
python skills/agentic-vault/scripts/vault_policy_drift.py record --vault <vault> [--plugin-root <plugin>]

# 2. Read-only drift audit (never modifies settings, hooks, or vault notes)
python skills/agentic-vault/scripts/vault_policy_drift.py check --vault <vault> [--plugin-root <plugin>] [--format text|json]

# 3. Opt-in fail-soft view inside vault_doctor.py (never changes doctor exit code)
python skills/agentic-vault/scripts/vault_doctor.py --vault <vault> --policy-drift [--format json]
```

## What it checks

1. **Operator `permissions.deny` monotonicity (`.claude/settings.json`, `.claude/settings.local.json`)**:
   - Records sorted unique SHA-256 digests of trimmed `permissions.deny` strings (never raw rule text).
   - Flags `deny_rules_removed` (or `settings_file_missing` / `settings_unreadable_or_invalid`) whenever any recorded `deny` rule disappears or weakens.
   - Adding new `deny` rules increments `deny_rules_added` while keeping `status: "clean"` (monotonic restriction strengthening).
2. **Hook configuration & resolved hook script SHA-256 integrity**:
   - Computes canonical content-free `hooks_config_sha256` of each `.claude/settings*.json` `hooks` block (detecting inline `python -c` / shell commands, matcher changes, event changes, or command flag tampering while still allowing monotonic `permissions.deny` additions).
   - Computes SHA-256 digests of scripts referenced by `.claude/settings*.json` hooks, managed vault git hooks (`00-meta/scripts/git-hooks/pre-commit`, `pre-push`, and `vault_healthcheck.py`), and bundled plugin hooks (`hooks/*.py`, `hooks/run_python_hook.sh` when `--plugin-root` is given).
   - Flags `hooks_config_modified`, `hooks_config_untracked`, `hook_script_modified`, `hook_script_missing`, `hook_unwired_from_settings`, `hook_script_unsafe` (symlink, junction, hardlink, UNC/device path, or oversized), `plugin_root_unset`, and `hook_script_untracked`.

## Schema & exit codes

- Report schema: `agentic-vault/policy-drift/v1` (`authority`: `policy_drift_audit_only`, `trust_boundary`: `static_settings_and_hook_hashes_only_runtime_host_unverified`).
- Baseline schema: `agentic-vault/policy-drift-baseline/v1` sealed with `baseline_sha256` at `00-meta/.vault-policy-baseline.json`.
- Standalone CLI `check` exit codes: `0` (`clean` or `not_recorded`), `1` (`drift_detected`), `2` (`invalid_baseline` or `error`).
- `vault_doctor.py --policy-drift` is fail-soft and never changes the doctor exit code.

## Boundaries

- Strictly offline and read-only during `check` and `vault_doctor --policy-drift`; never runs hooks, never follows UNC/network paths, and never modifies host settings.
- Content-free diagnostics: raw `permissions.deny` strings, hook command lines, environment values, and exception text are never stored in the baseline or emitted in reports.
- Verifies static files on disk against the local sealed baseline; it cannot prove runtime host enforcement or protect against an adversary with write access to both the settings/hooks and `00-meta/.vault-policy-baseline.json`.
- Git-hook coverage audits the managed vault hook files (`00-meta/scripts/git-hooks/pre-commit`, `pre-push`, and `00-meta/scripts/vault_healthcheck.py`) and does not inspect `.git/config` (`core.hooksPath`) or unmanaged `.git/hooks/` files.
- Does not modify any of the seven certificate-bound warning-promotion checker files (`checker_sha256` remains unchanged).
