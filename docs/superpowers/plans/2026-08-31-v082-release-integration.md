# agentic-vault v0.8.2 Release Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assemble the independently verified Jarvis and staged-gate work into a version-consistent local v0.8.2 release candidate without pushing or publishing it.

**Architecture:** A metadata contract test makes the version and documented capabilities machine-checkable. Release notes then record upgrade and security boundaries, followed by one full local verification matrix.

**Tech Stack:** JSON, Markdown, Python 3.10+ `unittest`, Git and POSIX shell syntax checks.

**Spec:** `docs/superpowers/specs/2026-08-31-v082-release-integration-design.md`

## Global Constraints

- Both subproject test suites must pass before version strings change.
- Plugin manifest, marketplace, README badge/tree, and release note must all say `0.8.2`.
- No GitHub push, tag push, or GitHub Release publication.
- Existing `briefing_time` remains documented as backward compatible.
- Release notes must state that Git hooks and prompt-level deny rules are not OS security boundaries.

---

### Task 1: Version and release-surface contract

**Files:**
- Create: `tests/test_release_metadata.py`
- Create: `docs/releases/v0.8.2.md`
- Modify: `.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: completed Jarvis and staged-gate files.
- Produces: machine-checked `0.8.2` metadata and release notes.

- [ ] **Step 1: Add failing metadata test**

```python
EXPECTED = "0.8.2"

def test_all_release_surfaces_match(self):
    plugin = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8-sig"))
    market = json.loads(Path(".claude-plugin/marketplace.json").read_text(encoding="utf-8-sig"))
    readme = Path("README.md").read_text(encoding="utf-8")
    release = Path("docs/releases/v0.8.2.md").read_text(encoding="utf-8")
    self.assertEqual(plugin["version"], EXPECTED)
    self.assertEqual(market["plugins"][0]["version"], EXPECTED)
    for text in (readme, release):
        self.assertIn(EXPECTED, text)
```

Add assertions that the release note names `briefing_times`, private chat, process-then-ack offset, staged index, `--no-verify`, and `briefing_time` fallback.

- [ ] **Step 2: Run metadata test and verify RED**

Run: `python -m unittest tests.test_release_metadata -v`

Expected: missing release note and current `0.8.1` values fail.

- [ ] **Step 3: Update all release surfaces**

Bump both JSON files and README badge/tree to `0.8.2`. Write release notes with sections: 보안 수정, 데이터 내구성, commit gate, 하위호환, 업그레이드, 검증, 알려진 경계, 미포함 v0.9.0 항목.

- [ ] **Step 4: Run metadata and JSON tests GREEN**

Run: `python -m unittest tests.test_release_metadata -v`

Run: `python -m json.tool .claude-plugin/plugin.json > $null`

Run: `python -m json.tool .claude-plugin/marketplace.json > $null`

Expected: all exit 0.

- [ ] **Step 5: Commit**

```powershell
git add -- tests/test_release_metadata.py docs/releases/v0.8.2.md .claude-plugin/plugin.json .claude-plugin/marketplace.json README.md
git commit -m "release: prepare agentic-vault v0.8.2"
```

### Task 2: Full release-candidate verification

**Files:**
- Modify only if a verification failure requires an in-scope fix; add a failing regression test before production changes.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: fresh verification evidence and clean release-candidate worktree.

- [ ] **Step 1: Run the complete unit suite**

Run: `python -m unittest discover -s tests -v`

Expected: 0 failures, 0 errors.

- [ ] **Step 2: Run static and format validation**

```powershell
python -m py_compile hooks/session_start.py skills/agentic-vault/scripts/vault_healthcheck.py skills/agentic-vault/scripts/backup_vault.py skills/agentic-vault/scripts/jarvis_bridge.py
python -m json.tool .claude-plugin/plugin.json > $null
python -m json.tool .claude-plugin/marketplace.json > $null
python -m json.tool assets/templates/vault-config.json > $null
sh -n assets/git-hooks/pre-commit
sh -n assets/git-hooks/pre-push
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 3: Run runtime smoke checks**

Run: `python skills/agentic-vault/scripts/jarvis_bridge.py --self-test`

Run: `python skills/agentic-vault/scripts/vault_healthcheck.py --vault .`

Expected: Jarvis FAIL 0; non-vault healthcheck exits 0 without creating a report.

- [ ] **Step 4: Verify Git state and release range**

Run: `git status --short --branch`

Run: `git log --oneline origin/master..HEAD`

Expected: no uncommitted files; local commits listed; no push performed.

- [ ] **Step 5: Record verification-only completion**

Do not create an empty commit or tag. Put exact command results in the SDD task report and hand the branch to final review.
