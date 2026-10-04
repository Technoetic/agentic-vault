# OWASP 2026 provided-document hardening plan

> Implementation: root owns Jarvis; scoped agents own Jev and filesystem readers. Every production fix starts with a failing behavioral test and ends with review and full-suite verification.

**Goal:** strengthen agentic-vault boundaries against risks in the user-provided OWASP LLM Top 10 2026 PDF and produce a ten-risk applicability and verification record.

**Architecture:** retain the standard-library-only plugin and existing command interfaces. Restrict unattended model sessions to host-selected context with no model tools, independently validate filesystem objects and outbound judgment data, and bound child-process input/output. Policies and model judgments remain advisory; deterministic code controls execution.

**Source:** user-provided `OWASP-GenAI-LLM-Top-10-2026-v1.0.pdf`, SHA256 `ef87993a4e50ae9d83b41ff7a3d3e6320a82dfa8d4ec6bf98d0ce264b2e6108e`. The cover and revision history retain publication placeholders. This work does not certify it as the official final release.

## Constraints

- Base commit: ca6b8c2dd74819e11aba587eeef40a9c0aeeaf97; isolated branch fix/owasp-2026-hardening.
- Python 3.10+ and standard library only; no new external services or dependencies.
- Do not change the installed plugin, live vault permissions, Telegram daemon, release version, remote branch or production data.
- No real secrets, API calls or outgoing messages in tests. Tests use temporary files, synthetic secrets, local subprocesses and fixture transports.
- Deterministic controls must not be described as full OS isolation or full secret detection.
- Training/model poisoning and vector multi-tenancy have no local training/vector subsystem; record applicability honestly.

## Task 1: filesystem objects

Files: vault_recall.py, hooks/session_start.py (actual session reader), tests/test_vault_recall.py, tests/test_session_start.py.

- [x] Reproduce a multiply-linked allowed Markdown file exposing a sentinel outside the permitted location; verify RED.
- [x] Reject non-single-link files in the existing regular-file reader before content access; retain path, deny-zone and stable-read checks.
- [x] Verify legitimate notes still load, denied files are not read, and diagnostics report omissions; verify GREEN.

## Task 2: outbound judgment input

Files: skills/agentic-vault/scripts/jev_client.py and tests/test_jev_client.py (related entry-point tests only if needed).

- [x] Reproduce JSON credential field/value separation and cyclic-container traversal; verify RED with no network transmission.
- [x] Detect credential-labelled nonempty scalar values independently of textual labels, reject cycles/excessive nodes/depth and preserve bounded execution.
- [x] Keep all supported public question/result contracts intact and verify benign inputs, normalization and unsupported shapes; verify GREEN.

## Task 3: unattended generation

Files: skills/agentic-vault/scripts/jarvis_bridge.py, tests/test_jarvis_bridge.py, supporting documentation.

- [x] Verify a malicious note cannot obtain a model tool, ambient settings/skills or a persistent model session; verify RED at the launch boundary.
- [x] Host-select bounded Markdown context through existing recall and safe configured context readers; fail closed on invalid configuration and treat contents as evidence. Disable model tools, ambient settings and skills; keep hooks/MCP disabled.
- [x] Reproduce excess input and child stdout/stderr against real local processes; verify RED.
- [x] Bound prompt bytes and collected stdout/stderr, terminate excess-producing children and return fixed diagnostics without leaking their output; verify GREEN.
- [x] Preserve successful Q&A/briefing behavior and existing caller failure contracts; document the retrieval and CLI compatibility tradeoff.

## Review focus

Hardlinks to deny/outside files; valid configs with zero context budgets; malformed/cyclic and large input containers; labelled JSON secrets and harmless labels; child outputs exactly at/above the cap and child stdin stalls; ambient CLI settings; retrieval omissions; benign question/briefing compatibility.

## Final verification and documentation

- [x] Map all ten PDF risks to actual files, controls, regression tests, operational requirements and honest residual limits in docs/security/owasp-2026-hardening.md.
- [x] Update SECURITY.md/README.md where changed behavior invalidates statements.
- [x] Run `python -m unittest discover -s tests -v`, `python scripts/evaluate_recall.py`, compile sources and `git diff --check`.
- [x] Independent reviewer re-runs verification and checks boundary claims; fix material findings.
- [x] Prepare the verified changes and NS vault handoff for exact-file local commits. Publishing and installation are separate actions; the resulting commit IDs are recorded in the NS handoff.
