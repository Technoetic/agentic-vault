# Large-vault operations, shared worktrees and verifier fidelity

The user authorized implementing and publicly releasing this work on 2026-11-07. Base: v0.42.0 / 10a4db1d07d4a897b9661549a5e95fa893c9d974. Target: v0.43.0. Existing state locks, recall, deny zones, lesson deltas, preservation checks and the doctor are reused; they must not be described as new capabilities.

## Global boundaries

- Python 3.10+ standard library; no third-party source or dependency copied. The seven certificate-bound checker files, `hooks/hooks.json`, `assets/templates/vault-config.json` and `assets/templates/settings-permissions.json` stay unchanged. No active installation or company vault migration.
- New tools are opt-in. Nothing here runs from a hook, a session command or a default configuration unless the user installs or calls it. A key that is absent leaves prior output unchanged, except the listed defect fixes.
- Defect fixes that change a default result must be listed in the release notes. Each states its trade.
- Measured statements stay within what was measured. Synthetic numbers are labeled synthetic. The fact precedence sentence is reported as not harming prohibition retention on one small local model, with no gain confirmed.
- Errors are fixed codes without note bodies. Public documents carry no private vault content, company data or local machine paths.

## Unit 1 — recall read budget and scope

`recall.max_total_read_bytes` (512 KiB to 1 GiB, absent means 16 MiB) and `recall.priority_paths` (at most 32 vault-relative entries) in an optional block. The 512 KiB per-file limit stays. Priority reads first and prefers ties; it never passes a deny or exclusion rule or lifts a note over a higher score. An invalid block stops recall with `invalid_config` and `recall_config_invalid`, and unknown keys are refused. Results add `omitted_paths_sample`, `scope_state` and `answer_state`. Matches, ranking and context stay fixed by digest. Documented in `docs/recall-scope.md`.

## Unit 2 — state failures, capacity and staged growth

`vault_state` keeps `input_too_large` and `file_changed_during_read` distinct. Checkpoints hash sources under a 64 MiB bound and keep no bytes; `read_note` stays at 256 KiB. The checkpoint hook prints a fixed code and exits 0. `vault_capacity.py` backs a read-only `vault_doctor.py --capacity` that never changes the exit code. `staged_growth_check` (`off`, `warn`, `block`) and `staged_growth_max_tokens` compare budgeted notes with `HEAD` and store nothing. Documented in `docs/capacity.md`.

## Unit 3 — handoff folding

`vault_handoff_fold.py` has two subcommands and no default action: `analyze` reads only, and `apply` alone writes. Both need a threshold and a protected-section count. `apply` also needs the analyzed hash, moves sections byte for byte into new archive notes, keeps a stub and lifts unresolved lines. It rebuilds the original from its own output and refuses on any difference. No default threshold. `scripts/evaluate_handoff_fold.py` is an offline loss alarm over a synthetic pack: literal reachability only, no model. Documented in `docs/handoff-fold.md`.

## Unit 4 — session evidence and plugin scope

`--session-evidence` adds replacement character counts, a lower-bound double context count and an asynchronous failure outcome. `vault_plugin_scope.py` backs `--plugin-scope`, which reads this plugin's keys from three settings files and one health-check copy path, and leaves other sources `unknown`. `--plugin-scope-host` runs `claude plugin list --json` with fixed arguments, no shell and a time bound. Hook commands are neither read nor printed, and no hook is run. Documented in `docs/reliability.md`.

## Unit 5 — lesson recurrence and cues

`vault_lesson_clusters.py` is read-only and keeps explicit classes apart from uncalibrated candidates. Lesson markers gain optional `cues`; a marker without them keeps its bytes. Ledger rows with the wrong cell count surface as `ignored_rows`. `vault_lesson_cues.py build` compiles rows in force. `assets/adapters/lesson-cues/` is a standalone Claude Code PreToolUse hook, not installed by default, that gives hints or a once-per-session `deny`, and never `allow` or `ask`. Cues use literals and globs, never regular expressions. Effect measurement is deferred. Documented in `docs/lesson-recurrence.md` and the adapter guide.

## Unit 6 — verifier fidelity and environment variation

`scripts/evaluate_verifier_fidelity.py` mutates synthetic notes that pass and reports each category with a one-sided exact bound, with out-of-scope rows and a false alarm rate kept separate. `scripts/env_vary.py` reruns selected tests under controls, then all variations, then shuffled blame. Three fixes in `vault_compile_quality.py` follow from the measurement. Documented in `docs/verifier-fidelity.md`.

## Unit 7 — linked worktree copies

`vault_worktrees.py` identifies a linked tree of the vault's own repository by its `.git` file. Walks skip and name it. `vault_evidence.py`, `vault_proposals.py` and `vault_lesson_metrics.py` refuse a zone inside a copy. The deny guard adds a view from each working tree. Bound files stay unchanged, so `vault_ssot.py` still counts copies and other direct-path callers are listed as limits. Documented in `docs/hardening.md`.

## Unit 8 — declared-path commits

`vault_commit.py` commits declared files with a sha256 per path, bounded jittered retries and verification against the commit named in Git's output. `--own-lines` applies the caller's edits to `HEAD` through `commit-tree` and a compare-and-swap `update-ref`, and runs `pre-commit` on the proposed tree. Shared notes are refused by default. It is a commit tool, not a lock. Documented in `docs/concurrent-commits.md`.

## Unit 9 — constraints note, rule wording and permission sample

`constraints_note` renders item by item within the existing fixed block budget. The `vault-workflow.md` rule adds a prohibition-first sentence on fact precedence, and its stamp moves to 0.43.0. The deny-guard guide adds a `permissions.ask` sample. Lifecycle workers drop ignored `PYTHON*` values and run with `-B`.

## Publication and reference evidence

Concepts only, with pins in the release notes: LCM and lossless-claw, Beads, a vendor context compression post, Sourcegraph and Zoekt skip reporting, Prometheus `predict_linear`, Betterer, lessons-learned, Drain3 (weighed and rejected), DocOps, reprotest, two arXiv abstracts on stale instructions and compaction, and host and Git documentation. Prior use is kept apart from new material. Require focused and whole-suite results, independent review, certificate-bound bytes with harmless replay, exact master and tag CI, an isolated native plugin test, an independently checked archive, draft and public assets, immutable Latest and serialized attestations. Then update Reference Atlas with pinned public evidence. GitHub Pages only, never Sites hosting. No permission re-request for already authorized merge, push or publication.
