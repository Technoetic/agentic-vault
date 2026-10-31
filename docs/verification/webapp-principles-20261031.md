# Webapp principles source integration verification

This is an **unreleased source addition** based on v0.34.0. It does not update
installed plugins, operating webapps, vault engines or host settings.

The six rules are in [the canonical guide](../webapp-principles.md), with a
[dedicated shared skill](../../skills/webapp-principles/SKILL.md). Scope and
implementation are described in the [design](../superpowers/specs/2026-10-31-webapp-principles-design.md).
Machine-readable results and tested-source hashes are in the
[verification record](webapp-principles-20261031.json).

## What ran

- Delivery verifier: 28 tests, 26 passed, two POSIX-only process-group checks
  skipped on Windows. Missing, disabled, duplicate, nonexistent, aliased,
  swapped and wrong-package skills plus installed byte mutations are covered.
- Release metadata: 37 tests passed. README truth: 11 tests passed. These are
  **76 distinct selected regressions: 74 passed, two skipped, zero failures**.
- Native Codex disposable-home install/discovery: both named skills enabled,
  required installed bytes exact, three session-start hooks plus one compact
  and one end hook, trust remains untrusted, zero model turns. The existing
  primary `skill` report field remains compatible; `skills` lists both entries.
- Independent review found one shared-path defect: both skill names could
  point to the core skill. The fix binds discovery to distinct expected files
  in the installed package root. Its original reproduction and new regression
  checks now reject the alias. No findings remain in the reviewed code/content.
- One no-skill baseline plan recognized the cache/open-tab issue but offered a
  manual reload. A fresh agent reading the skill and guide required automatic
  clean next-navigation upgrade, kept a read-only note task read-only and
  preserved an existing React architecture. These are **three hypothetical
  agent-plan observations**, not executed app acceptance tests.

## Important handoff

- task_id: `webapp-principles-20261031`.
- artifact_paths: canonical guide, shared skill, linked entry points,
  `scripts/verify_codex_plugin.py`, its tests, design/plan and this record.
- verification_commands_and_results: selected regressions and native verifier
  above; raw private logs are not a public release artifact. No deployment or
  real application data was used.
- verified_by: independent read-only `/root/av_webapp_entry_audit` reran path,
  compatibility/resource/isolation and README checks and resolved 338 relative
  links; `/root/av_webapp_candidate` produced the separate plan observations.
- assumptions: explicit user/project instructions take priority; webapp scope
  does not turn ordinary vault work into an app task. Engine/hook/template and
  manifest bytes are unchanged. Native testing uses an isolated temporary home.
- unresolved: real-app tests remain with each consuming application. Native
  discovery was measured on Windows Codex; Claude delivery is structural.
  Full engine and multi-OS release CI were not rerun for these scoped changes.
- next_safe_action: local integration, then a separately authorized release
  and installation workflow; apply the guide's matrix to actual applications.

Jev's one native Choice request selected the scoped-skill integration with
confidence 0.99. This was advisory architecture input, not an approval or a
claim of implementation correctness.
