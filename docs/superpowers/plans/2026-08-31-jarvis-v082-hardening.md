# Jarvis v0.8.2 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jarvis private-chat-only and crash-safe for Telegram update/capture handling while completing validated multi-slot briefings.

**Architecture:** Extract pure configuration, authorization, state, and update-acknowledgement boundaries from the existing loop. The network loop composes those tested functions and advances Telegram offset only after successful handling.

**Tech Stack:** Python 3.10+ standard library, `unittest`, Telegram Bot API through existing `urllib` calls.

**Spec:** `docs/superpowers/specs/2026-08-31-jarvis-v082-hardening-design.md`

## Global Constraints

- Python 3.10+ and standard library only.
- Existing `jarvis.briefing_time` remains backward compatible.
- Only `chat.type == "private"`, `chat.id == from.id`, whitelisted users may receive vault-derived responses.
- Offset advances only after successful processing and is written atomically.
- Token plaintext never appears in paths, logs, or errors.
- No network calls in automated tests.

---

### Task 1: Configuration and authorization boundaries

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_jarvis_bridge.py`
- Modify: `skills/agentic-vault/scripts/jarvis_bridge.py:39-90`

**Interfaces:**
- Produces: `JarvisConfigError`, `parse_briefing_slots(block) -> list[tuple[int, int]]`, `is_authorized_private_message(message, whitelist) -> bool`.
- Consumes: existing JSON `jarvis` block and Telegram message dictionaries.

- [ ] **Step 1: Add failing parser and authorization tests**

```python
def test_parse_briefing_slots_prefers_multiple_and_deduplicates(self):
    block = {"briefing_time": "07:30", "briefing_times": ["19:30", "08:30", "08:30"]}
    self.assertEqual(parse_briefing_slots(block), [(8, 30), (19, 30)])

def test_parse_briefing_slots_falls_back_only_when_key_absent(self):
    self.assertEqual(parse_briefing_slots({"briefing_time": "07:30"}), [(7, 30)])
    with self.assertRaises(JarvisConfigError):
        parse_briefing_slots({"briefing_time": "07:30", "briefing_times": []})

def test_parse_briefing_slots_rejects_bad_values(self):
    for block in ({"briefing_times": "08:30"}, {"briefing_times": ["24:00"]},
                  {"briefing_times": ["08:60"]}, {"briefing_times": [8]}):
        with self.subTest(block=block), self.assertRaises(JarvisConfigError):
            parse_briefing_slots(block)

def test_authorization_requires_private_self_chat_and_whitelist(self):
    allowed = {111}
    self.assertTrue(is_authorized_private_message(
        {"from": {"id": 111}, "chat": {"id": 111, "type": "private"}}, allowed))
    self.assertFalse(is_authorized_private_message(
        {"from": {"id": 111}, "chat": {"id": -99, "type": "group"}}, allowed))
    self.assertFalse(is_authorized_private_message(
        {"from": {"id": 111}, "chat": {"id": 222, "type": "private"}}, allowed))
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m unittest tests.test_jarvis_bridge.JarvisConfigAuthorizationTests -v`

Expected: import/attribute failures for the new interfaces.

- [ ] **Step 3: Implement minimal validation and authorization**

```python
class JarvisConfigError(ValueError):
    pass

def parse_briefing_slots(block: dict) -> list[tuple[int, int]]:
    raw = block["briefing_times"] if "briefing_times" in block else [block.get("briefing_time", "07:30")]
    if not isinstance(raw, list) or not raw:
        raise JarvisConfigError("briefing_times는 비어 있지 않은 HH:MM 문자열 배열이어야 합니다")
    slots = set()
    for value in raw:
        if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
            raise JarvisConfigError(f"잘못된 브리핑 시각 형식: {value!r}")
        hour, minute = map(int, value.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise JarvisConfigError(f"브리핑 시각 범위 오류: {value}")
        slots.add((hour, minute))
    return sorted(slots)

def is_authorized_private_message(message: dict, whitelist: set[int]) -> bool:
    sender = (message.get("from") or {}).get("id")
    chat = message.get("chat") or {}
    return sender in whitelist and chat.get("type") == "private" and chat.get("id") == sender
```

Add `import re`; make `load_jarvis_config` call `parse_briefing_slots` and store `_briefing_slots`.

- [ ] **Step 4: Run tests and existing self-test GREEN**

Run: `python -m unittest tests.test_jarvis_bridge.JarvisConfigAuthorizationTests -v`

Run: `python skills/agentic-vault/scripts/jarvis_bridge.py --self-test`

Expected: focused tests pass; self-test has FAIL 0.

- [ ] **Step 5: Commit**

```powershell
git add -- tests/__init__.py tests/test_jarvis_bridge.py skills/agentic-vault/scripts/jarvis_bridge.py
git commit -m "fix: validate Jarvis schedule and private chats"
```

### Task 2: Namespaced atomic state and idempotent captures

**Files:**
- Modify: `tests/test_jarvis_bridge.py`
- Modify: `skills/agentic-vault/scripts/jarvis_bridge.py:30-120`

**Interfaces:**
- Produces: `state_dir_for(vault: Path, token: str, root: Path = STATE_ROOT) -> Path`, `atomic_write_text(path: Path, text: str) -> None`, `migrate_legacy_state(root: Path, namespace: Path, owner_key: str) -> None`, `do_capture(vault: Path, body: str, source: str, capture_id: str | None = None, received_at: datetime | None = None) -> str`.
- Consumes: validated token string, resolved vault path, Telegram `update_id`.

- [ ] **Step 1: Add failing state and capture tests**

```python
def test_state_namespace_separates_vaults_and_bots_without_token_plaintext(self):
    a = state_dir_for(Path("C:/vault-a"), "12345:secret", self.root)
    b = state_dir_for(Path("C:/vault-b"), "12345:secret", self.root)
    c = state_dir_for(Path("C:/vault-a"), "67890:other", self.root)
    self.assertEqual(len({a, b, c}), 3)
    self.assertNotIn("secret", str(a))

def test_atomic_write_replaces_complete_value(self):
    target = self.root / "offset"
    atomic_write_text(target, "41")
    atomic_write_text(target, "42")
    self.assertEqual(target.read_text(encoding="utf-8"), "42")
    self.assertEqual(list(self.root.glob("*.tmp")), [])

def test_capture_id_makes_retry_idempotent_and_same_second_distinct(self):
    received = datetime(2026, 8, 31, 9, 0, 0)
    first = do_capture(self.vault, "A", "telegram", capture_id="100", received_at=received)
    retry = do_capture(self.vault, "A", "telegram", capture_id="100", received_at=received)
    second = do_capture(self.vault, "B", "telegram", capture_id="101", received_at=received)
    self.assertEqual(first, retry)
    self.assertNotEqual(first, second)
```

Add migration tests proving only the first `owner_key` claims legacy files and a second owner receives a warning/result without moving them.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m unittest tests.test_jarvis_bridge.JarvisStateTests -v`

Expected: missing interface/signature failures.

- [ ] **Step 3: Implement state helpers and capture identity**

Use SHA-256 of `os.path.normcase(str(vault.resolve(strict=False)))` for a 12-character vault ID. Require the token prefix before `:` to be numeric and use it as bot ID; malformed tokens raise `JarvisConfigError`. Build `<root>/<vault-id>-<bot-id>`. Keep `jarvis.log` global under `STATE_ROOT`; namespace only offset/butler/briefing state.

Write state through `<name>.<pid>.tmp`, call `flush` and `os.fsync`, close, then `Path.replace`. Claim `legacy-owner.json` with exclusive create (`"x"` mode); move only `offset`, `last_butler`, and `last_brief` when the stored owner matches. A same-owner rerun resumes missing moves; an existing target is never overwritten; a different owner leaves legacy files untouched and logs a warning.

For captures, sanitize `capture_id` to `[A-Za-z0-9_-]`, append it to the timestamp filename, and derive `received_at` from Telegram message `date` so retry content is stable. If the retry target already contains identical text return it unchanged; conflicting content for the same ID raises `RuntimeError`.

- [ ] **Step 4: Run focused and full Jarvis tests GREEN**

Run: `python -m unittest tests.test_jarvis_bridge -v`

Expected: all Jarvis tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- tests/test_jarvis_bridge.py skills/agentic-vault/scripts/jarvis_bridge.py
git commit -m "fix: make Jarvis state and captures durable"
```

### Task 3: Process-then-ack Telegram updates

**Files:**
- Modify: `tests/test_jarvis_bridge.py`
- Modify: `skills/agentic-vault/scripts/jarvis_bridge.py:212-360`

**Interfaces:**
- Produces: `tg_send(token: str, chat_id: int, text: str) -> bool`, `process_update(vault: Path, cfg: dict, token: str, whitelist: set[int], update: dict, started: float, qa_times: deque[float], qa_attempt_ids: set[int], sender: Callable[[str, int, str], bool]) -> bool`, `process_update_batch(updates: list[dict], offset: int, handler: Callable[[dict], bool], save_offset: Callable[[int], None]) -> tuple[int, bool]`.
- Consumes: Tasks 1-2 authorization, state, and capture interfaces.

- [ ] **Step 1: Add failing acknowledgement tests**

```python
def test_batch_commits_offset_only_after_success(self):
    saved = []
    def handler(update): return update["update_id"] != 11
    offset, complete = process_update_batch(
        [{"update_id": 10}, {"update_id": 11}, {"update_id": 12}], 9, handler, saved.append)
    self.assertEqual((offset, complete), (10, False))
    self.assertEqual(saved, [10])

def test_group_update_is_discarded_without_response(self):
    sender = Mock(return_value=True)
    update = {"update_id": 7, "message": {"text": "/status", "date": 1_788_134_400,
              "from": {"id": 111}, "chat": {"id": -99, "type": "group"}}}
    ok = process_update(self.vault, self.cfg, "12345:secret", {111}, update,
                        0.0, deque(), set(), sender)
    self.assertTrue(ok)
    sender.assert_not_called()

def test_capture_send_failure_retries_same_update_without_duplicate_file(self):
    sender = Mock(side_effect=[False, True])
    update = {"update_id": 77, "message": {"text": "기억해: A", "date": 1_788_134_400,
              "from": {"id": 111}, "chat": {"id": 111, "type": "private"}}}
    args = (self.vault, self.cfg, "12345:secret", {111}, update, 0.0, deque(), set(), sender)
    self.assertFalse(process_update(*args))
    self.assertTrue(process_update(*args))
    self.assertEqual(len(list((self.vault / "10-inbox/jarvis").glob("*.md"))), 1)

def test_corrupt_offset_aborts_startup(self):
    path = self.vault / "offset"
    path.write_text("not-an-int", encoding="utf-8")
    with self.assertRaises(JarvisConfigError):
        read_int_state(path, default=0)

def test_same_qa_update_consumes_quota_once_across_retry(self):
    sender = Mock(side_effect=[False, True])
    qa_times, attempted = deque(), set()
    update = {"update_id": 88, "message": {"text": "질문", "date": 1_788_134_400,
              "from": {"id": 111}, "chat": {"id": 111, "type": "private"}}}
    with patch.object(jarvis, "do_qa", return_value="답"):
        for _ in range(2):
            process_update(self.vault, self.cfg, "12345:secret", {111}, update,
                           0.0, qa_times, attempted, sender)
    self.assertEqual((len(qa_times), attempted), (1, {88}))
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m unittest tests.test_jarvis_bridge.JarvisUpdateDurabilityTests -v`

Expected: missing `process_update_batch` and old `tg_send` return contract failures.

- [ ] **Step 3: Implement process-then-ack flow**

`tg_send` returns `False` on final HTML/plain delivery failure and `True` only after all chunks send. Log only exception class/code for Telegram failures, never raw token-bearing URLs. `process_update` treats unauthorized, group, and non-text updates as handled; authorized routes return the actual send result. Capture passes `str(update_id)` to `do_capture`. Q&A adds a timestamp only when its `update_id` first enters `qa_attempt_ids`.

`process_update_batch` iterates in update ID order, stops at the first false handler result, calls `save_offset(update_id)` only after true, and returns the last committed offset plus completion flag. `serve` writes offsets through `atomic_write_text` and never writes them before routing.

- [ ] **Step 4: Run focused tests, full Jarvis tests, and self-test GREEN**

Run: `python -m unittest tests.test_jarvis_bridge -v`

Run: `python skills/agentic-vault/scripts/jarvis_bridge.py --self-test`

Expected: tests pass and self-test FAIL 0.

- [ ] **Step 5: Commit**

```powershell
git add -- tests/test_jarvis_bridge.py skills/agentic-vault/scripts/jarvis_bridge.py
git commit -m "fix: acknowledge Telegram updates after processing"
```

### Task 4: Complete multi-slot scheduling documentation

**Files:**
- Modify: `tests/test_jarvis_bridge.py`
- Modify: `skills/agentic-vault/scripts/jarvis_bridge.py:152-320`
- Modify: `assets/templates/vault-config.json`
- Modify: `commands/vault-jarvis-setup.md`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-17-vault-jarvis-design.md`

**Interfaces:**
- Consumes: `_briefing_slots` from Task 1.
- Produces: neutral briefing prompt and documented `briefing_times` contract.

- [ ] **Step 1: Add failing prompt and date-transition tests**

Add a test that patches `run_claude`, calls `do_brief`, and asserts the prompt contains `정기 브리핑` and not `아침 브리핑`. Add pure scheduler tests for a new day resetting fired slots and for startup consuming only already-passed slots.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_jarvis_bridge.JarvisBriefingTests -v`

Expected: current prompt assertion fails and scheduler helper is absent.

- [ ] **Step 3: Implement minimal neutral prompt and scheduler helper**

Use `briefing_label(slot)` for Telegram labels and `due_briefing_slots(now, slots, fired_date, fired_slots)` for deterministic date rollover. Mark scheduled briefing and butler state complete only after `tg_send` returns true so transport failures retry. Keep `/brief` wording neutral.

Change the template Jarvis block to:

```json
"briefing_times": ["07:30"],
```

Document that existing `briefing_time` remains a fallback only when `briefing_times` is absent. Correct README version-tree text only in the release integration plan, not here.

- [ ] **Step 4: Run all Jarvis tests and JSON parsing GREEN**

Run: `python -m unittest tests.test_jarvis_bridge -v`

Run: `python -m json.tool assets/templates/vault-config.json > $null`

Expected: tests and JSON parse pass.

- [ ] **Step 5: Commit**

```powershell
git add -- tests/test_jarvis_bridge.py skills/agentic-vault/scripts/jarvis_bridge.py assets/templates/vault-config.json commands/vault-jarvis-setup.md README.md docs/superpowers/specs/2026-07-17-vault-jarvis-design.md
git commit -m "docs: complete multi-slot Jarvis contract"
```
