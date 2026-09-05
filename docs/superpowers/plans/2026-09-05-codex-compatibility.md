# Claude Code and Codex compatibility

Base: `fd2718f`. Deliver local prerelease `0.9.0-local.2` in the existing plugin
repository. Preserve the Python 3.10+ standard-library engine, existing Claude
commands/config, independent snapshots and bounded reader behavior.

The user requested dual-client support. Use one engine and one authoritative set
of command documents, with host-specific entry instructions. No installation into
the user's active Codex/Claude configuration, remote publication or model calls.
Native Codex packaging tests may install into a disposable, isolated test home.

## Implementation and acceptance

1. Extend `hooks/session_start.py` with an explicit `--vault` argument and working
   directory fallback for Codex hooks. Explicit path beats the legacy Claude env;
   legacy env beats cwd. Keep text output, all budgets/path validation, generic
   diagnostics and quiet non-vault behavior. Codex documents hooks running in the
   session cwd and providing CLAUDE_PLUGIN_ROOT compatibility, so reuse hook wiring.
   Test both clients and recovery with Claude variables removed.
2. Add `.codex-plugin/plugin.json`, sharing `skills/` and default `hooks/hooks.json`.
   Add Codex skill routing with paths resolved relative to the installed SKILL.md,
   not assumed environment variables or hard-coded cache paths. Expose session
   start/end, recall, lint, backup and the existing note workflows through
   `$agentic-vault:agentic-vault <operation>` (native-discovered plugin name).
   Jarvis remains a separately documented Claude-backed
   integration. Do not map Claude permissions to Codex permissions.
3. Use an agent-neutral AGENTS stub for the existing init/upgrade generator. Append
   the existing canonical rule bodies; preserve the manual/generated ownership
   rules and direct Codex to user policy outside the managed CLAUDE.md block.
   Generated vault contracts must not contain installation-specific paths.
4. Package a local Codex marketplace source for this repository. Validate the
   manifest and skill with the supplied validators; verify native marketplace
   loading and plugin installation using an isolated temporary Codex home. If
   available, inspect native skill discovery without sending a model request.
5. Run the full unittest suite, bilingual recall regression, compilation, strict
   Claude static validation and independent review. Record exact supported and
   untested behavior, hook trust requirements, Codex output truncation behavior,
   install instructions, and produce a new ZIP without replacing local.1.

## Ownership

- Session agent: session_start.py, test_session_start.py, test_memory_recovery.py.
- Skill agent: shared SKILL.md, new references/codex.md, AGENTS-vault-stub.md,
  vault-init.md and vault-upgrade.md.
- Controller: plugin/marketplace metadata, native packaging validation, docs,
  release metadata, final testing/review and distribution.

## Evidence sources

- Local Codex CLI 0.150.1 help and native schema.
- https://developers.openai.com/plugins/build/plugins
- https://learn.chatgpt.com/docs/build-skills
- https://learn.chatgpt.com/docs/hooks
- https://learn.chatgpt.com/docs/agent-configuration/agents-md
