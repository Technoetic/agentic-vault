# OWASP 2026 provided-document hardening and verification

This development branch strengthens agentic-vault using the user's provided *OWASP Top 10 for LLM Applications 2026* PDF. It is a traceability and regression record, not certification that every OWASP mitigation is implemented or that the PDF is the final official release. The source retains publication placeholders on pages 1 and 2.

Source: 122 pages, SHA256 `ef87993a4e50ae9d83b41ff7a3d3e6320a82dfa8d4ec6bf98d0ce264b2e6108e`. Baseline `ca6b8c2dd74819e11aba587eeef40a9c0aeeaf97`; branch `fix/owasp-2026-hardening`. No dependency or release-version changes.

## Implemented boundaries

- Unattended Jarvis generation has no model tools. The host supplies source-labelled, bounded Markdown evidence; configured hot/handoff prefixes and recent-log lines stay available even when a note is large. Source text, paths and the final envelope are checked for known secret patterns. Missing/truncated context is reported to the model.
- Claude argv includes `--bare`, `--tools ""`, `--setting-sources ""`, `--disable-slash-commands`, `--no-session-persistence`, `--strict-mcp-config` and explicit disabled hooks. Unsupported flags fail closed. These flags replace broad Read/Grep/Glob grants in the published 0.17.1 bridge.
- **Authentication compatibility:** the installed Claude CLI help says `--bare` skips OAuth/keychain reads and needs API authentication or the selected third-party provider's credentials. The bridge supplies no API-key helper. A subscription-only login will fail in this mode. This code is not installed into the live bridge during this task.
- Input including evidence is capped at 64 KiB; child stdout/stderr are each capped at 64 KiB. Cancellable pipe polling and bounded collection close readers after timeout/overflow even if a descendant holds a pipe. File-backed stdin avoids a blocked writer thread.
- Existing regular-file readers reject `st_nlink != 1` before content reads, supplementing path, deny-zone, symbolic-link and Windows-junction checks. Hardlinked config is rejected too. Recall title metadata is capped at 512 characters without changing full-title ranking.
- Jev input validates structured credential labels, 8,192 nodes, depth 64, aggregate text 65,536 characters, integer conversion size and wire size 64 KiB. Cycles are rejected; normal shared containers remain supported. Encoding is incremental and no transport occurs after a rejected input.

## Ten-risk applicability

The IDs and page numbers below belong to the provided 2026 PDF, not the 2025 numbering.

| Provided risk | Existing and added controls | Relevant implementation / tests | Residual limit or non-applicable scope |
|---|---|---|---|
| LLM01 Prompt Injection (pp10–17) | No model tools/MCP/hooks/skills in unattended bridge; data envelope and source boundaries; explicit facts/omissions | `jarvis_bridge.py`, `JarvisOwaspBoundaryTests`, `JarvisOwaspReviewRegressionTests` | A model can still be misled within the permitted evidence and produce a wrong answer. Other interactive sessions are outside this bridge boundary. |
| LLM02 Sensitive Information Disclosure (pp18–22) | Deny paths and file-object checks; known string and structured credential filters; final source-path/envelope filter; bot token withheld from child env | `vault_paths.py`, `jev_client.py`, `JarvisProvenanceSecretTests`, structured credential tests | Pattern-based protection does not detect every password, personal datum, encoded secret or image attachment. Approved context still leaves the device for the selected model. |
| LLM03 Excessive Agency (pp23–26) | Host performs selected reads; model gets zero tools; fixed argv/stdin; existing private-sender whitelist and capture scope | `jarvis_bridge.py`; model launch, Telegram authorization and capture tests | This is a CLI boundary, not an OS sandbox. Health reporting and mirror operations remain host-owned. `gates` configuration is still not an automatic action-authorization engine. |
| LLM04 Supply Chain (pp27–32) | Standard-library-only code; existing native-launcher refusal and pinned CI actions; isolated versioned source and regression verification | `resolve_claude_executable`; launcher tests; `.github/workflows/tests.yml` | This patch does not implement binary signing, provider provenance certification or server-side release attestation. Executable/plugin paths and write access remain trusted. |
| LLM05 Data and Model Poisoning (pp33–37) | File provenance/object checks; existing source hashes, stale-evidence checks and human-approved lesson promotion; hardlink regression | `session_start.py`, `vault_recall.py`, `vault_evidence.py`, `vault_proposals.py` and respective suites | Notes can still contain false claims. There is no local model training/fine-tuning pipeline to harden; training-specific controls are N/A. |
| LLM06 Unbounded Consumption (pp38–42) | Existing recall scan/token/time/rate budgets plus bounded title, child input/output, cancellable readers and Jev traversal/wire caps | title metadata tests; child cap/timeout tests; cyclic/shared-fanout/huge-integer tests | No provider-wide spending quota or OS process-tree/CPU/disk sandbox is implemented. The title cap is not a total recall JSON response byte cap. |
| LLM07 Misinformation (pp43–45) | Source-labelled evidence, missing/truncation diagnostics, actual tool results and independently rerun verification; existing advisory/abstain/stale statuses | recall benchmark16; briefing log/git regression; `vault_judge.py`, `vault_evidence.py` | Lexical retrieval success and valid response JSON do not measure final answer truth. No live-model hallucination rate is claimed. |
| LLM08 Hidden Context Exposure (pp46–49) | `--bare` skips automatic CLAUDE.md/memory discovery per installed CLI help; no saved session; explicit minimal context; secrets and enforcement kept out of model policy | fixed-argv tests; context secret/path tests | Managed host policy, the CLI binary and OS account are trusted. Real-provider automatic-context/auth behavior was not exercised here. System prompt text is not a secret store. |
| LLM09 Vector and Embedding Weaknesses (pp50–54) | Lexical Markdown retrieval retains source/path/deny/object boundaries | `vault_recall.py` and filesystem tests | There is no vector database, embedding index or tenant-isolated RAG server in this plugin. Those component controls are N/A; a future vector integration needs a separate access-control review. |
| LLM10 Improper Output Handling (pp55–57) | Existing Jev response validation/advisory role; bounded child output; text never becomes argv or a shell command | `jev_client.py`, response validation tests, hostile stdin tests and output-cap tests | Semantic safety is not proved by JSON schema. The plugin does not implement a generated SQL/HTML/code execution runtime; consumers must validate those contexts separately. |

## Verification record

Baseline: `python -m unittest discover -s tests -v` ran 641 tests, 0 failures, 22 existing platform skips. New regressions were observed failing before production fixes: hardlinks, overlong titles, credential-labelled JSON, cyclic/fanout/large-integer traversal, model-tool grants, prompt/output excess, lingering pipe readers, missing briefing evidence, large configured-note prefixes and source-path secret shapes.

Final root execution: **663 tests, 641 passed, 22 existing platform/environment skips, 0 failures/errors, exit 0** (127.926 seconds). The independent run exercised the same final product files and found three documentation/text assertions during concurrent edits; independent reruns of all 23 affected documentation/release tests then passed. This independent result is recorded as passing after affected-check reruns, not as a single all-green full run. All four changed product files remained unchanged throughout that verification.

Commands, file hashes and both execution histories are stored in `owasp-2026-verification.json` in this directory. The 22 skips comprise 12 symlink privilege/support cases, six POSIX-only cases, three cases requiring an inactive Aside browser and one case-sensitive-filesystem case. The bilingual lexical benchmark comprises 16 synthetic queries and measures retrieval, not real Claude answer quality. Local tests use temporary/synthetic files, fixture transports and real local subprocesses; they do not contact Telegram/Jev/Claude or read live credentials.

Independent reviews caught two initial Jarvis regressions and a source-path filter omission; all received reproducing regression tests and fixes. A second reviewer independently executed the whole suite against final product file hashes and reran the affected documentation checks. Live provider authentication, actual Telegram delivery and non-Windows CI were not exercised in this task.

A separate installed Jev adapter made one actual two-question batch using only three safe paragraphs about scope. It classified the stated limits as explicit and the training/vector N/A description as supported, both confidence 1.0. This is advisory interpretation of the supplied prose, not code security certification, action authorization or a live integration test of the changed Jev client. Its input/response hashes and scope are recorded in the verification JSON.

## Handoff

- `task_id`: `agentic-vault-owasp-hardening-20261004`
- `artifact_paths`: four product modules (`jarvis_bridge.py`, `jev_client.py`, `vault_recall.py`, `hooks/session_start.py`), their four test modules, this report, plan, verification JSON, README/SECURITY/setup updates.
- `verification_commands_and_results`: final machine-readable verification JSON; root663/0 failures/22 skips; independent663 followed by all23 affected checks passing; baseline641; focused Jarvis168/skip2; lexical16 Recall@3=1.0/MRR=1.0; compile/diff checks exit0.
- `assumptions`: provided-document numbering, trusted plugin/CLI/OS, API authentication for the strict bridge, stdlib/Python3.10+.
- `unresolved`: live provider/auth/Telegram behavior, OS isolation and provider-wide quotas, complete secret detection, future training/vector components. No official OWASP certification.
- `next_safe_action`: review the local branch and API-auth migration before installing or publishing it; compare authenticated live behavior with these documented limits.
- `verified_by`: Codex `/root/owasp_repo_controls`, 2026-10-04 08:53:22 KST, boundary8/8; Codex `/root/semantic_independent_verification`, 2026-10-04 08:59:49 KST, independent execution and affected-check reruns, lexical16, Python36 compilation and diff checks.
