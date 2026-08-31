#!/usr/bin/env python3
"""agentic-vault Jarvis bridge — Telegram 단일 채널 상시 데몬 (stdlib-only).

역할: 롱폴링 수신 → 화이트리스트 필터 → 라우팅(캡처/브리핑/상태/Q&A) → 스케줄(브리핑·집사).
메시지 기반 직접 쓰기는 `10-inbox/jarvis/` 캡처뿐이다.
예약 집사는 설정된 `health_report`를 갱신하고 설정된 `mirror` 원격으로 push할 수 있다.
거부된 텍스트 메시지는 `미승인 또는 비공개 아닌 발신자 폐기`를 콘솔과 `~/.vault-jarvis/jarvis.log`에 기록하며 본문은 기록하지 않는다.
캡처 파일명에는 정제된 Telegram `update_id` 접미사가 붙는다.
LLM 호출은 전부 읽기 전용 `claude -p --allowedTools Read Grep Glob` 세션이다.

사용:
  python jarvis_bridge.py --vault D:/NS            # 상시 실행
  python jarvis_bridge.py --vault D:/NS --self-test # 네트워크 없는 자체 검증

비밀: 봇 토큰은 env JARVIS_TELEGRAM_TOKEN 로만 전달한다. 볼트·리포에 저장 금지.
설정: <vault>/00-meta/vault-config.json 의 "jarvis" 블록. 없거나 enabled=false면 침묵 종료.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

API_BASE = "https://api.telegram.org/bot{token}/{method}"
STATE_ROOT = Path.home() / ".vault-jarvis"
LOG_MAX_BYTES = 1_000_000
TG_CHUNK = 4000

CAPTURE_PREFIXES = ("기억해", "메모", "remember")

DEFAULTS = {
    "enabled": False,
    "telegram_user_ids": [],
    "briefing_time": "07:30",
    "butler_interval_hours": 24,
    "qa_hourly_limit": 6,
    "qa_timeout_sec": 180,
    "claude_cmd": "claude",
}


class JarvisConfigError(ValueError):
    pass


class TelegramAPIError(RuntimeError):
    def __init__(self, source: str, code: object = None):
        safe_source = (
            source if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", source)
            else "TelegramError")
        self.source = safe_source
        self.code = code if type(code) is int else None
        suffix = f" code={self.code}" if self.code is not None else ""
        super().__init__(f"Telegram API failure: {self.source}{suffix}")


class BriefingState:
    def __init__(
            self, day: date, fired: set[tuple[int, int]],
            pending: list[tuple[int, int]], skipped: set[tuple[int, int]],
            started_at: datetime,
            ) -> None:
        self.day = day
        self.fired = fired
        self.pending = pending
        self.skipped = skipped
        self.started_at = started_at


class GenerationResult:
    def __init__(self, ok: bool, text: str) -> None:
        self.ok = ok
        self.text = text


def parse_telegram_user_ids(value: object) -> list[int]:
    if not isinstance(value, list):
        raise JarvisConfigError("telegram_user_ids must be a list of positive integers")
    parsed: list[int] = []
    for item in value:
        if type(item) is int:
            user_id = item
        elif isinstance(item, str) and re.fullmatch(r"[0-9]+", item, re.ASCII):
            try:
                user_id = int(item)
            except ValueError as error:
                raise JarvisConfigError(
                    "telegram_user_ids must contain only positive integers") from error
        else:
            raise JarvisConfigError(
                "telegram_user_ids must contain only positive integers")
        if user_id <= 0:
            raise JarvisConfigError(
                "telegram_user_ids must contain only positive integers")
        parsed.append(user_id)
    return parsed


def parse_telegram_token(token: object) -> tuple[str, str]:
    if not isinstance(token, str):
        raise JarvisConfigError("invalid Telegram bot token")
    match = re.fullmatch(
        r"([0-9]+):([A-Za-z0-9_-]+)", token, flags=re.ASCII)
    if match is None:
        raise JarvisConfigError("invalid Telegram bot token")
    return match.group(1), match.group(2)


def child_process_env() -> dict[str, str]:
    token_key = "JARVIS_TELEGRAM_TOKEN".casefold()
    return {
        key: value for key, value in os.environ.items()
        if key.casefold() != token_key
    }


def state_dir_for(vault: Path, token: str, root: Path = STATE_ROOT) -> Path:
    bot_id, _secret = parse_telegram_token(token)
    normalized_vault = os.path.normcase(str(vault.resolve(strict=False)))
    vault_id = hashlib.sha256(normalized_vault.encode("utf-8")).hexdigest()[:12]
    return root / f"{vault_id}-{bot_id}"


def atomic_write_text(path: Path, text: str) -> None:
    temporary = _write_unique_fsynced_temp(path, text)
    try:
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    """Best-effort durability for the directory entry published by a link."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_unique_fsynced_temp(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_path)
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        descriptor = -1
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _publish_text_no_clobber(path: Path, text: str) -> bool:
    """Publish complete UTF-8 text atomically, returning False if target won."""
    temporary = _write_unique_fsynced_temp(path, text)
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        _fsync_directory(path.parent)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _reject_duplicate_json_members(pairs: list[tuple[str, object]]) -> dict:
    record = {}
    for key, value in pairs:
        if key in record:
            raise ValueError("duplicate JSON member")
        record[key] = value
    return record


def _lstat_or_missing(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise JarvisConfigError(
            f"cannot inspect legacy state path: {path.name}") from None


def _legacy_state_remains(root: Path, names: tuple[str, ...]) -> bool:
    return any(_lstat_or_missing(root / name) is not None for name in names)


def read_int_state(path: Path, default: int = 0) -> int:
    try:
        value = int(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, TypeError, ValueError) as error:
        raise JarvisConfigError(f"invalid integer state: {path.name}") from error
    if value < 0:
        raise JarvisConfigError(f"invalid integer state: {path.name}")
    return value


def read_float_state(path: Path, default: float = 0.0) -> float:
    try:
        value = float(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, TypeError, ValueError) as error:
        raise JarvisConfigError(f"invalid numeric state: {path.name}") from error
    if not math.isfinite(value) or value < 0:
        raise JarvisConfigError(f"invalid numeric state: {path.name}")
    return value


def migrate_legacy_state(root: Path, namespace: Path, owner_key: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    owner_path = root / "legacy-owner.json"
    legacy_names = ("offset", "last_butler", "last_brief")
    if _lstat_or_missing(owner_path) is None:
        owner_payload = json.dumps(
            {"owner_key": owner_key}, ensure_ascii=False, separators=(",", ":"))
        try:
            _publish_text_no_clobber(owner_path, owner_payload)
        except OSError:
            if _lstat_or_missing(owner_path) is None:
                raise JarvisConfigError(
                    "cannot safely publish legacy state owner") from None
    try:
        owner_stat = _lstat_or_missing(owner_path)
        if owner_stat is None or not stat.S_ISREG(owner_stat.st_mode):
            raise ValueError("owner is not a regular file")
        owner_record = json.loads(
            owner_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_members)
        if not isinstance(owner_record, dict):
            raise ValueError("owner root is not an object")
        stored_owner = owner_record.get("owner_key")
        if not isinstance(stored_owner, str) or not stored_owner:
            raise ValueError("owner_key is missing or invalid")
    except (OSError, UnicodeError, ValueError):
        if _legacy_state_remains(root, legacy_names):
            raise JarvisConfigError(
                "legacy state owner is unreadable while legacy state remains") from None
        log("warning: legacy state owner is unreadable; leaving legacy files untouched")
        return
    if stored_owner != owner_key:
        log("warning: legacy state belongs to another owner; leaving legacy files untouched")
        return

    namespace.mkdir(parents=True, exist_ok=True)
    for name in legacy_names:
        source = root / name
        target = namespace / name
        try:
            os.link(source, target)
        except FileExistsError:
            try:
                source_stat = _lstat_or_missing(source)
            except JarvisConfigError:
                raise JarvisConfigError(
                    f"cannot verify legacy migration target: {name}") from None
            if source_stat is None:
                continue
            try:
                target_stat = _lstat_or_missing(target)
            except JarvisConfigError:
                raise JarvisConfigError(
                    f"cannot verify legacy migration target: {name}") from None
            if target_stat is None or not (
                    stat.S_ISREG(source_stat.st_mode)
                    and stat.S_ISREG(target_stat.st_mode)):
                raise JarvisConfigError(
                    f"cannot verify legacy migration target: {name}") from None
            same_file = os.path.samestat(source_stat, target_stat)
            if same_file:
                _fsync_directory(namespace)
                try:
                    source.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    raise JarvisConfigError(
                        f"cannot remove migrated legacy state: {name}") from None
                _fsync_directory(root)
            continue
        except FileNotFoundError:
            if _lstat_or_missing(source) is None:
                continue
            raise JarvisConfigError(
                f"cannot safely migrate legacy state: {name}") from None
        except OSError:
            raise JarvisConfigError(
                f"cannot safely migrate legacy state: {name}") from None
        _fsync_directory(namespace)
        try:
            source.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            raise JarvisConfigError(
                f"cannot remove migrated legacy state: {name}") from None
        _fsync_directory(root)


def parse_briefing_slots(block: dict) -> list[tuple[int, int]]:
    raw = block["briefing_times"] if "briefing_times" in block else [block.get("briefing_time", "07:30")]
    if not isinstance(raw, list) or not raw:
        raise JarvisConfigError("briefing_times must be a non-empty HH:MM string list")
    slots = set()
    for value in raw:
        if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
            raise JarvisConfigError(f"invalid briefing time format: {value!r}")
        hour, minute = map(int, value.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise JarvisConfigError(f"briefing time out of range: {value}")
        slots.add((hour, minute))
    return sorted(slots)


def is_authorized_private_message(message: dict, whitelist: set[int]) -> bool:
    sender = (message.get("from") or {}).get("id")
    chat = message.get("chat") or {}
    return sender in whitelist and chat.get("type") == "private" and chat.get("id") == sender


# ---------------------------------------------------------------- 로그

def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        STATE_ROOT.mkdir(parents=True, exist_ok=True)
        logfile = STATE_ROOT / "jarvis.log"
        if logfile.exists() and logfile.stat().st_size > LOG_MAX_BYTES:
            logfile.replace(STATE_ROOT / "jarvis.log.1")
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 로그 실패가 브리지를 죽여선 안 된다


# ---------------------------------------------------------------- 설정

def load_jarvis_config(vault: Path) -> dict | None:
    """볼트가 아니거나 jarvis 블록이 없거나 enabled=false면 None."""
    cfg_path = vault / "00-meta" / "vault-config.json"
    try:
        vault_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, OSError, UnicodeError) as error:
        raise JarvisConfigError("vault configuration is unreadable or invalid JSON") from error
    if not isinstance(vault_cfg, dict):
        raise JarvisConfigError("vault configuration root must be an object")
    if "jarvis" not in vault_cfg:
        return None
    block = vault_cfg["jarvis"]
    if not isinstance(block, dict):
        raise JarvisConfigError("jarvis configuration must be an object")
    if type(block.get("enabled")) is not bool:
        raise JarvisConfigError("jarvis.enabled must be a boolean")
    if block["enabled"] is False:
        return None
    cfg = {**DEFAULTS, **block}
    cfg["telegram_user_ids"] = parse_telegram_user_ids(
        cfg.get("telegram_user_ids", []))
    cfg["_briefing_slots"] = parse_briefing_slots(block)
    for name in ("butler_interval_hours", "qa_timeout_sec"):
        value = cfg[name]
        if type(value) not in (int, float):
            raise JarvisConfigError(f"jarvis.{name} must be a finite positive number")
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, ValueError) as error:
            raise JarvisConfigError(
                f"jarvis.{name} must be a finite positive number") from error
        if not finite or value <= 0:
            raise JarvisConfigError(f"jarvis.{name} must be a finite positive number")
    hourly_limit = cfg["qa_hourly_limit"]
    if type(hourly_limit) is not int or hourly_limit <= 0:
        raise JarvisConfigError("jarvis.qa_hourly_limit must be a positive integer")
    claude_cmd = cfg["claude_cmd"]
    if not isinstance(claude_cmd, str) or not claude_cmd.strip():
        raise JarvisConfigError("jarvis.claude_cmd must be a non-empty string")
    # Q&A 가드에 필요한 볼트 수준 키를 함께 전달
    cfg["_deny_zones"] = vault_cfg.get("deny_zones", [])
    cfg["_language"] = vault_cfg.get("language", "ko")
    cfg["_hot_note"] = vault_cfg.get("hot_note", "00-meta/hot.md")
    cfg["_handoff_note"] = vault_cfg.get("handoff_note", "")
    cfg["_log_note"] = vault_cfg.get("log_note", "00-meta/log.md")
    return cfg


# ---------------------------------------------------------------- 라우팅

def route(text: str) -> tuple[str, str]:
    """반환: ("capture", 본문) | ("brief", "") | ("status", "") | ("qa", 원문)."""
    stripped = text.strip()
    if stripped == "/brief":
        return ("brief", "")
    if stripped == "/status":
        return ("status", "")
    lower = stripped.lower()
    for prefix in CAPTURE_PREFIXES:
        if lower.startswith(prefix):
            body = stripped[len(prefix):].lstrip(" :：").strip()
            if body:
                return ("capture", body)
    return ("qa", stripped)


# ---------------------------------------------------------------- 동작

def _resolve_capture_directory(vault: Path, inbox: Path) -> Path:
    try:
        resolved_vault = vault.resolve(strict=True)
        resolved_inbox = inbox.resolve(strict=False)
    except (OSError, RuntimeError):
        raise RuntimeError("cannot safely resolve capture directory") from None
    try:
        resolved_inbox.relative_to(resolved_vault)
    except ValueError:
        raise RuntimeError("capture directory escapes vault") from None
    return resolved_inbox


def do_capture(
        vault: Path, body: str, source: str, capture_id: str | None = None,
        received_at: datetime | None = None) -> str:
    inbox = vault / "10-inbox" / "jarvis"
    resolved_inbox = _resolve_capture_directory(vault, inbox)
    try:
        resolved_inbox.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise RuntimeError("cannot safely create capture directory") from None
    now = received_at or datetime.now()
    suffix = ""
    if capture_id is not None:
        safe_capture_id = re.sub(r"[^A-Za-z0-9_-]", "_", capture_id) or "_"
        suffix = f"-{safe_capture_id}"
    name = now.strftime("%Y-%m-%d %H%M%S") + suffix + ".md"
    content = f"{body}\n\n---\n수신: {now.strftime('%Y-%m-%d %H:%M:%S')} · 채널: {source}\n"
    resolved_inbox = _resolve_capture_directory(vault, inbox)
    target = resolved_inbox / name
    try:
        published = _publish_text_no_clobber(target, content)
    except OSError:
        raise RuntimeError("cannot safely publish capture") from None
    if not published:
        try:
            if not stat.S_ISREG(target.lstat().st_mode):
                raise OSError("capture target is not a regular file")
            existing = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise RuntimeError("cannot safely inspect existing capture") from None
        if existing == content:
            return name
        capture_label = safe_capture_id if capture_id is not None else "timestamp"
        raise RuntimeError(f"conflicting capture content for ID {capture_label}")
    return name


def generate_claude(vault: Path, cfg: dict, prompt: str) -> GenerationResult:
    """Run one read-only generation and preserve success separately from text."""
    exe = shutil.which(cfg["claude_cmd"])
    if not exe:
        return GenerationResult(
            False, "⚠️ claude CLI를 찾을 수 없습니다. PATH를 확인하세요.")
    deny = ", ".join(cfg["_deny_zones"]) or "(없음)"
    guard = (
        "너는 이 옵시디언 볼트의 개인 비서다. 규칙: "
        f"(1) 탐색 순서 {cfg['_hot_note']} → 00-meta/index.md → Grep. "
        f"(2) 다음 경로는 절대 읽지 마라: {deny}, **/.env, 90-assets/. "
        f"(3) 볼트 내용만 근거로 {cfg['_language']} 언어로 간결히 답하고 근거 노트명을 인용하라. "
        "(4) 볼트에 근거가 없으면 없다고 답하라. 파일 생성·수정·삭제는 절대 하지 마라. "
        "(5) 출력은 Telegram 메시지다 — 마크다운 표를 절대 쓰지 마라(렌더링 불가). "
        "표가 필요한 내용은 항목별 불릿(·)으로 풀고, 제목은 짧은 굵은 줄로, 전체를 모바일 가독 길이로."
    )
    cmd = [exe, "-p", prompt,
           "--allowedTools", "Read", "Grep", "Glob",
           "--append-system-prompt", guard]
    try:
        r = subprocess.run(cmd, cwd=str(vault), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=cfg["qa_timeout_sec"], env=child_process_env())
    except subprocess.TimeoutExpired:
        return GenerationResult(
            False, "⏱️ 응답 생성이 시간 초과됐습니다. 질문을 좁혀 다시 시도해 주세요.")
    if r.returncode != 0:
        log(f"claude 실패 rc={r.returncode}")
        return GenerationResult(False, "⚠️ 응답 생성에 실패했습니다. 로그를 확인하세요.")
    output = r.stdout.strip()
    if not output:
        return GenerationResult(False, "(빈 응답)")
    return GenerationResult(True, output)


def run_claude(vault: Path, cfg: dict, prompt: str) -> str:
    """User-facing wrapper that renders handled generation failures as diagnostics."""
    return generate_claude(vault, cfg, prompt).text


def do_qa(vault: Path, cfg: dict, question: str) -> str:
    return run_claude(vault, cfg, question)


def briefing_label(slot: tuple[int, int]) -> tuple[str, str]:
    """브리핑 슬롯 → Telegram 전용 (이모지, 시간대명) 라벨."""
    hour, _minute = slot
    return ("🌅", "아침") if hour < 11 else (("🌞", "점심") if hour < 17 else ("🌆", "저녁"))


def _format_briefing_slot(slot: tuple[int, int]) -> str:
    return "%02d:%02d" % slot


def _parse_briefing_day(value: object) -> date:
    if not isinstance(value, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise JarvisConfigError("invalid last_brief day")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise JarvisConfigError("invalid last_brief day") from None
    if parsed.isoformat() != value:
        raise JarvisConfigError("invalid last_brief day")
    return parsed


def _parse_briefing_state_slots(
        value: object, name: str, configured: set[tuple[int, int]],
        ) -> list[tuple[int, int]]:
    if not isinstance(value, list):
        raise JarvisConfigError(f"invalid last_brief {name}")
    parsed: list[tuple[int, int]] = []
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(
                r"[0-9]{2}:[0-9]{2}", item):
            raise JarvisConfigError(f"invalid last_brief {name}")
        hour, minute = map(int, item.split(":"))
        slot = (hour, minute)
        if slot not in configured:
            raise JarvisConfigError(f"last_brief {name} contains an unconfigured slot")
        parsed.append(slot)
    if len(set(parsed)) != len(parsed):
        raise JarvisConfigError(f"last_brief {name} contains duplicate slots")
    if parsed != sorted(parsed):
        raise JarvisConfigError(f"last_brief {name} is out of order")
    return parsed


def _write_briefing_state(
        path: Path, day_value: date, fired: set[tuple[int, int]],
        pending: list[tuple[int, int]],
        ) -> None:
    payload = {
        "version": 1,
        "day": day_value.isoformat(),
        "fired": [_format_briefing_slot(slot) for slot in sorted(fired)],
        "pending": [_format_briefing_slot(slot) for slot in pending],
    }
    atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _read_briefing_disk_state(
        path: Path, slots: list[tuple[int, int]], today: date,
        ) -> tuple[date, set[tuple[int, int]], list[tuple[int, int]]] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        raise JarvisConfigError("last_brief state is unreadable") from None

    legacy_match = re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw)
    if legacy_match is not None:
        legacy_day = _parse_briefing_day(raw)
        if legacy_day > today:
            raise JarvisConfigError("last_brief day is in the future")
        if legacy_day == today:
            if len(slots) != 1:
                raise JarvisConfigError(
                    "legacy last_brief for today is ambiguous with multiple briefing slots")
            fired = {slots[0]}
        else:
            fired = set()
        try:
            _write_briefing_state(path, legacy_day, fired, [])
        except OSError:
            raise JarvisConfigError("cannot migrate legacy last_brief state") from None
        return legacy_day, fired, []

    try:
        record = json.loads(raw, object_pairs_hook=_reject_duplicate_json_members)
    except (ValueError, UnicodeError):
        raise JarvisConfigError("last_brief state is invalid JSON") from None
    if not isinstance(record, dict) or set(record) != {
            "version", "day", "fired", "pending"}:
        raise JarvisConfigError("last_brief state has an invalid schema")
    if type(record["version"]) is not int or record["version"] != 1:
        raise JarvisConfigError("last_brief state has an unsupported version")
    state_day = _parse_briefing_day(record["day"])
    if state_day > today:
        raise JarvisConfigError("last_brief day is in the future")
    configured = set(slots)
    fired_list = _parse_briefing_state_slots(
        record["fired"], "fired", configured)
    pending = _parse_briefing_state_slots(
        record["pending"], "pending", configured)
    fired = set(fired_list)
    if fired.intersection(pending):
        raise JarvisConfigError("last_brief fired and pending slots overlap")
    return state_day, fired, pending


def load_briefing_state(
        path: Path, slots: list[tuple[int, int]], started_at: datetime,
        ) -> BriefingState:
    """Validate persisted state and initialize cold-start-only skipped slots."""
    persisted = _read_briefing_disk_state(path, slots, started_at.date())
    clock = (started_at.hour, started_at.minute)
    if persisted is None:
        day_value, fired, pending = started_at.date(), set(), []
    else:
        day_value, fired, pending = persisted
        if day_value < started_at.date() and not pending:
            day_value, fired = started_at.date(), set()
    if day_value == started_at.date():
        skipped = {
            slot for slot in slots
            if slot < clock and slot not in fired and slot not in pending
        }
    else:
        skipped = set()
    return BriefingState(
        day=day_value, fired=set(fired), pending=list(pending),
        skipped=skipped, started_at=started_at)


def _briefing_prompt(vault: Path, cfg: dict) -> str:
    git_lines = _git(vault, "log", "--oneline", "--date=short", "-10") or "(git 이력 없음)"
    parts = [f"오늘({date.today().isoformat()}) 정기 브리핑을 만들어라.",
             f"다음 노트를 읽어라: {cfg['_hot_note']}"]
    if cfg["_handoff_note"]:
        parts.append(f"그리고 {cfg['_handoff_note']} (직전 세션 인계·NEXT 섹션 주목)")
    parts.append(f"그리고 {cfg['_log_note']} 최상단 10줄.")
    parts.append("아래는 브리지가 수집한 최근 git 활동이다(참고용 — 직접 git 실행 불가):\n" + git_lines)
    parts.append("형식: ① 지금 상태(2줄) ② 최우선 미결(최대 3개) ③ 오늘의 제안(1개). 전체 12줄 이내.")
    return "\n".join(parts)


def generate_brief(vault: Path, cfg: dict) -> GenerationResult:
    return generate_claude(vault, cfg, _briefing_prompt(vault, cfg))


def do_brief(vault: Path, cfg: dict) -> str:
    return run_claude(vault, cfg, _briefing_prompt(vault, cfg))


def do_status(vault: Path, started: float) -> str:
    last = _git(vault, "log", "-1", "--date=format:%m-%d %H:%M", "--format=%ad — %s (%h)") \
        or "(git 이력 없음)"
    inbox = vault / "10-inbox"
    pending = sum(1 for p in inbox.rglob("*.md") if "_processed" not in p.parts) if inbox.is_dir() else 0
    up_h = (time.time() - started) / 3600
    return (f"🤖 상태\n· 마지막 저장: {last}\n"
            f"· 인박스 대기: {pending}건" + (" — 정제 권장" if pending >= 5 else "") +
            f"\n· 브리지 가동: {up_h:.1f}시간")


def do_butler(vault: Path, cfg: dict) -> str:
    lines = ["🧹 집사 보고"]
    hc = Path(__file__).parent / "vault_healthcheck.py"
    if hc.is_file():
        r = subprocess.run([sys.executable, str(hc), "--vault", str(vault)],
                           cwd=str(vault), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300,
                           env=child_process_env())
        lines.append("· healthcheck: " + ("치명 없음 ✅" if r.returncode == 0
                     else "치명 위반 감지 🚨 — 세션에서 /vault-lint 필요"))
    else:
        lines.append("· healthcheck: 스크립트 없음(생략)")
    remotes = (_git(vault, "remote") or "").split()
    if "mirror" in remotes:
        push = _git(vault, "push", "mirror", "--all")
        lines.append("· mirror push: " + ("완료 ✅" if push is not None else "실패 ⚠️"))
    inbox = vault / "10-inbox"
    pending = sum(1 for p in inbox.rglob("*.md") if "_processed" not in p.parts) if inbox.is_dir() else 0
    lines.append(f"· 인박스 대기: {pending}건" + (" — 정제 권장(/vault-process-inbox)" if pending >= 10 else ""))
    return "\n".join(lines)


def _git(vault: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=str(vault), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120,
                           env=child_process_env())
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------- Telegram

def tg_call(token: str, method: str, http_timeout: int = 65, **params) -> dict:
    """params는 그대로 Telegram API로 간다 — urllib 타임아웃과 이름 충돌 금지."""
    parse_telegram_token(token)
    failure: tuple[str, object] | None = None
    try:
        if (not isinstance(method, str) or
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", method, re.ASCII)):
            raise TelegramAPIError("URLValidationError")
        url = API_BASE.format(token=token, method=method)
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except TelegramAPIError:
        raise
    except (http.client.HTTPException, urllib.error.URLError, OSError,
            TypeError, ValueError, UnicodeError) as error:
        failure = (type(error).__name__, getattr(error, "code", None))
    if failure is not None:
        raise TelegramAPIError(*failure)
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        code = payload.get("error_code") if isinstance(payload, dict) else None
        raise TelegramAPIError("TelegramResponseError", code)
    return payload


def md_to_telegram_html(text: str) -> str:
    """claude 출력 마크다운을 Telegram HTML로 결정론 변환.
    지원: 헤더(#..)→굵게, **굵게**, `코드`, [[위키링크]]→기울임. 나머지는 이스케이프."""
    import re
    out_lines = []
    for line in text.splitlines():
        # 마크다운 표 폴백: 구분선은 버리고, 행은 불릿으로 푼다 (Telegram은 표 렌더링 불가)
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue  # |---|---| 구분선 제거
            line = "• " + " · ".join(c for c in cells if c)
        esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
        esc = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", esc)
        esc = re.sub(r"\[\[([^\]]+)\]\]", r"<i>\1</i>", esc)
        m = re.match(r"^(#{1,6})\s+(.*)$", esc)
        if m:
            esc = f"<b>{m.group(2)}</b>"
        out_lines.append(esc)
    return "\n".join(out_lines)


def _chunks_by_line(text: str, limit: int = TG_CHUNK) -> list[str]:
    """태그가 조각나지 않도록 줄 단위로 분할."""
    if limit <= 0:
        raise ValueError("chunk limit must be positive")
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(line) <= limit:
            if cur and len(cur) + len(line) > limit:
                chunks.append(cur)
                cur = ""
            cur += line
            if len(cur) == limit:
                chunks.append(cur)
                cur = ""
            continue
        if cur:
            chunks.append(cur)
            cur = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        cur = line
    if cur:
        chunks.append(cur)
    return chunks or [""]


def _telegram_error_label(error: BaseException) -> str:
    code = getattr(error, "code", None)
    return type(error).__name__ + (f" code={code}" if code is not None else "")


def tg_send(token: str, chat_id: int, text: str) -> bool:
    for chunk in _chunks_by_line(text):
        html = md_to_telegram_html(chunk)
        if len(html) > TG_CHUNK:
            try:
                tg_call(token, "sendMessage", http_timeout=30, chat_id=chat_id,
                        text=chunk)
            except (TelegramAPIError, urllib.error.URLError, OSError,
                    json.JSONDecodeError) as error:
                log(f"sendMessage 실패({_telegram_error_label(error)})")
                return False
            continue
        try:
            tg_call(token, "sendMessage", http_timeout=30, chat_id=chat_id,
                    text=html, parse_mode="HTML")
        except (TelegramAPIError, urllib.error.HTTPError) as error:
            if (isinstance(error, TelegramAPIError) and
                    error.source == "TelegramResponseError"):
                log(f"sendMessage 실패({_telegram_error_label(error)})")
                return False
            log(f"HTML 전송 실패({_telegram_error_label(error)}) — 플레인 폴백")
            try:
                tg_call(token, "sendMessage", http_timeout=30, chat_id=chat_id,
                        text=chunk)
            except (TelegramAPIError, urllib.error.URLError, OSError,
                    json.JSONDecodeError) as fallback_error:
                log(f"sendMessage 실패({_telegram_error_label(fallback_error)})")
                return False
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
            log(f"sendMessage 실패({_telegram_error_label(error)})")
            return False
    return True


def process_update(
        vault: Path, cfg: dict, token: str, whitelist: set[int], update: dict,
        started: float, qa_times: deque[float], qa_attempt_ids: set[int],
        sender: Callable[[str, int, str], bool]) -> bool:
    message = update.get("message") or {}
    text = message.get("text")
    if not isinstance(text, str) or not text:
        return True
    if not is_authorized_private_message(message, whitelist):
        sender_id = (message.get("from") or {}).get("id")
        log(f"미승인 또는 비공개 아닌 발신자 폐기: from={sender_id}")
        return True

    update_id = update["update_id"]
    chat_id = message["chat"]["id"]
    kind, body = route(text)
    log(
        f"inbound route={kind} update_id={update_id} "
        f"sender={chat_id} length={len(text)}")
    if kind == "capture":
        received_at = datetime.fromtimestamp(message["date"])
        name = do_capture(
            vault, body, "telegram", capture_id=str(update_id),
            received_at=received_at)
        return bool(sender(token, chat_id, f"📝 적어뒀습니다 → 10-inbox/jarvis/{name}"))
    if kind == "status":
        return bool(sender(token, chat_id, do_status(vault, started)))
    if kind == "brief":
        return bool(sender(
            token, chat_id,
            "📋 정기 브리핑\n" + do_brief(vault, cfg)))

    now = time.time()
    cutoff = now - 3600
    while qa_times and qa_times[0] < cutoff:
        qa_times.popleft()
    if update_id not in qa_attempt_ids:
        if len(qa_times) >= cfg["qa_hourly_limit"]:
            return bool(sender(
                token, chat_id,
                f"⏳ 시간당 질의 한도({cfg['qa_hourly_limit']}회) 도달 — 잠시 후 다시."))
        qa_attempt_ids.add(update_id)
        qa_times.append(now)
    return bool(sender(token, chat_id, do_qa(vault, cfg, body)))


def process_update_batch(
        updates: list[dict], offset: int, handler: Callable[[dict], bool],
        save_offset: Callable[[int], None],
        after_commit: Callable[[dict], None] | None = None,
        ) -> tuple[int, bool]:
    committed = offset
    for update in sorted(updates, key=lambda item: item["update_id"]):
        if not handler(update):
            return committed, False
        committed = update["update_id"]
        save_offset(committed)
        if after_commit is not None:
            after_commit(update)
    return committed, True


def send_due_briefings(
        vault: Path, cfg: dict, token: str, chat_id: int, now: datetime,
        slots: list[tuple[int, int]], state: BriefingState, state_file: Path,
        sender: Callable[[str, int, str], bool],
        generator: Callable[[Path, dict], GenerationResult] = generate_brief,
        ) -> BriefingState:
    """Persist due work before generation and acknowledge one delivered head at a time."""
    if state.day > now.date():
        raise JarvisConfigError("last_brief day is in the future")

    def drain_pending() -> bool:
        while state.pending:
            slot = state.pending[0]
            emoji, name = briefing_label(slot)
            log("스케줄 브리핑 생성(%s %02d:%02d)" % (
                name, slot[0], slot[1]))
            generated = generator(vault, cfg)
            if not generated.ok:
                log("스케줄 브리핑 생성 실패 — pending 유지")
                return False
            if not sender(
                    token, chat_id,
                    f"{emoji} {name} 브리핑\n" + generated.text):
                return False
            next_fired = set(state.fired)
            next_fired.add(slot)
            next_pending = state.pending[1:]
            _write_briefing_state(
                state_file, state.day, next_fired, next_pending)
            state.fired = next_fired
            state.pending = next_pending
        return True

    if state.day < now.date():
        if state.pending and not drain_pending():
            return state
        cold_activation = state.started_at.date() == now.date()
        if cold_activation:
            startup_clock = (state.started_at.hour, state.started_at.minute)
            next_skipped = {slot for slot in slots if slot < startup_clock}
        else:
            next_skipped = set()
        _write_briefing_state(
            state_file, now.date(), set(), [])
        state.day = now.date()
        state.fired = set()
        state.pending = []
        state.skipped = next_skipped

    current_clock = (now.hour, now.minute)
    newly_due = [
        slot for slot in slots
        if slot <= current_clock
        and slot not in state.fired
        and slot not in state.pending
        and slot not in state.skipped
    ]
    if newly_due:
        complete_pending = [*state.pending, *newly_due]
        _write_briefing_state(
            state_file, state.day, state.fired, complete_pending)
        state.pending = complete_pending

    drain_pending()
    return state


def send_butler_if_due(
        vault: Path, cfg: dict, token: str, chat_id: int, last_butler: float,
        state_file: Path, now_epoch: float,
        sender: Callable[[str, int, str], bool],
        ) -> float:
    if now_epoch - last_butler <= cfg["butler_interval_hours"] * 3600:
        return last_butler
    if not sender(token, chat_id, do_butler(vault, cfg)):
        return last_butler
    atomic_write_text(state_file, str(now_epoch))
    return now_epoch


# ---------------------------------------------------------------- 메인 루프

def serve(vault: Path, cfg: dict) -> None:
    token = os.environ.get("JARVIS_TELEGRAM_TOKEN", "")
    if not token:
        raise JarvisConfigError("JARVIS_TELEGRAM_TOKEN is required")
    parse_telegram_token(token)
    whitelist = set(parse_telegram_user_ids(cfg["telegram_user_ids"]))
    if not whitelist:
        log("⚠️ 화이트리스트가 비어 있음 — 모든 메시지를 폐기하며, 발신자 ID만 콘솔에 표시합니다.")
    started = time.time()
    qa_times: deque[float] = deque()
    qa_attempt_ids: set[int] = set()
    state_dir = state_dir_for(vault, token, STATE_ROOT)
    migrate_legacy_state(STATE_ROOT, state_dir, state_dir.name)
    offset_file = state_dir / "offset"
    offset = read_int_state(offset_file, default=0)
    butler_file = state_dir / "last_butler"
    last_butler = read_float_state(butler_file, default=0.0)
    brief_slots = cfg["_briefing_slots"]
    brief_file = state_dir / "last_brief"
    briefing_state = load_briefing_state(
        brief_file, brief_slots, datetime.now())
    log(f"jarvis 브리지 시작 — vault={vault}, whitelist={sorted(whitelist) or '(비어있음)'}, "
        f"브리핑 {['%02d:%02d' % s for s in brief_slots]}")

    while True:
        # --- 스케줄 ---
        now = datetime.now()
        if whitelist:
            first = sorted(whitelist)[0]
            briefing_state = send_due_briefings(
                vault, cfg, token, first, now, brief_slots, briefing_state,
                brief_file, tg_send)
            last_butler = send_butler_if_due(
                vault, cfg, token, first, last_butler, butler_file,
                time.time(), tg_send)
        # --- 수신 ---
        try:
            resp = tg_call(token, "getUpdates", http_timeout=65, offset=offset + 1,
                           timeout=50, allowed_updates='["message"]')
        except (TelegramAPIError, urllib.error.URLError, OSError,
                json.JSONDecodeError) as error:
            log(f"getUpdates 오류(재시도): {_telegram_error_label(error)}")
            time.sleep(5)
            continue
        offset, complete = process_update_batch(
            resp.get("result", []), offset,
            lambda update: process_update(
                vault, cfg, token, whitelist, update, started, qa_times,
                qa_attempt_ids, tg_send),
            lambda committed: atomic_write_text(offset_file, str(committed)),
            lambda update: qa_attempt_ids.discard(update["update_id"]))
        if not complete:
            log("수신 업데이트 처리 미완료 — offset 유지 후 재시도")
            time.sleep(5)
            continue


# ---------------------------------------------------------------- self-test

def self_test() -> int:
    results: list[tuple[str, str]] = []

    def check(name: str, ok: bool, warn: bool = False):
        results.append((name, "PASS" if ok else ("WARN" if warn else "FAIL")))

    with tempfile.TemporaryDirectory() as td:
        tv = Path(td)
        # ① config 판정
        check("config: 볼트 아님 → None", load_jarvis_config(tv) is None)
        (tv / "00-meta").mkdir()
        cfgp = tv / "00-meta" / "vault-config.json"
        cfgp.write_text(json.dumps({"vault_name": "t"}), encoding="utf-8")
        check("config: jarvis 블록 없음 → None", load_jarvis_config(tv) is None)
        cfgp.write_text(json.dumps({"jarvis": {"enabled": False}}), encoding="utf-8")
        check("config: enabled=false → None", load_jarvis_config(tv) is None)
        cfgp.write_text(json.dumps({"language": "ko", "deny_zones": ["90-assets"],
                                    "jarvis": {"enabled": True, "telegram_user_ids": [111]}}),
                        encoding="utf-8")
        loaded = load_jarvis_config(tv)
        check("config: 정상 → 파싱·기본값 병합", bool(loaded) and loaded["qa_hourly_limit"] == 6
              and loaded["telegram_user_ids"] == [111])
        # ② 라우팅
        check("route: '기억해: X' → capture", route("기억해: 우유 사기") == ("capture", "우유 사기"))
        check("route: '메모 회의 3시' → capture", route("메모 회의 3시") == ("capture", "회의 3시"))
        check("route: 'Remember: buy milk' → capture", route("Remember: buy milk") == ("capture", "buy milk"))
        check("route: '/brief' → brief", route("/brief") == ("brief", ""))
        check("route: '/status' → status", route("/status") == ("status", ""))
        check("route: 일반 질문 → qa", route("어제 뭐 했지?") == ("qa", "어제 뭐 했지?"))
        # ③ 캡처
        name = do_capture(tv, "테스트 본문", "selftest")
        written = (tv / "10-inbox" / "jarvis" / name).read_text(encoding="utf-8")
        check("capture: 파일 생성·내용 일치", written.startswith("테스트 본문"))
        # ④ Telegram HTML 변환
        html = md_to_telegram_html("## 제목\n**굵게** `코드` [[노트]] a<b")
        check("html: 헤더 → <b>", "<b>제목</b>" in html)
        check("html: 굵게·코드·위키링크·이스케이프",
              "<b>굵게</b>" in html and "<code>코드</code>" in html
              and "<i>노트</i>" in html and "a&lt;b" in html)
        check("html: 줄단위 분할", len(_chunks_by_line("x\n" * 3000)) >= 2)
        tbl = md_to_telegram_html("| 순위 | 방식 |\n|---|---|\n| 1위 | CLI 사내 |")
        check("html: 표 → 불릿 변환·구분선 제거",
              "• 순위 · 방식" in tbl and "• 1위 · CLI 사내" in tbl and "---" not in tbl)
        # ⑤ 환경 (실패 아닌 경고)
        check("env: claude CLI 탐지", shutil.which(DEFAULTS["claude_cmd"]) is not None, warn=True)
        check("env: JARVIS_TELEGRAM_TOKEN 설정", bool(os.environ.get("JARVIS_TELEGRAM_TOKEN")), warn=True)
        # ⑤ 로그 쓰기
        try:
            STATE_ROOT.mkdir(parents=True, exist_ok=True)
            probe = STATE_ROOT / ".write-probe"
            probe.write_text("ok")
            probe.unlink()
            check("log: 상태 디렉토리 쓰기", True)
        except OSError:
            check("log: 상태 디렉토리 쓰기", False)

    width = max(len(n) for n, _ in results)
    print("\n=== jarvis_bridge self-test ===")
    for n, s in results:
        mark = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[s]
        print(f" {mark} {n.ljust(width)} {s}")
    fails = sum(1 for _, s in results if s == "FAIL")
    print(f"결과: PASS {sum(1 for _, s in results if s == 'PASS')} / "
          f"WARN {sum(1 for _, s in results if s == 'WARN')} / FAIL {fails}")
    return 1 if fails else 0


# ---------------------------------------------------------------- 진입점

def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description="agentic-vault Jarvis Telegram bridge")
    ap.add_argument("--vault", default=".", help="볼트 루트 경로")
    ap.add_argument("--self-test", action="store_true", help="네트워크 없는 자체 검증")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())
    try:
        vault = Path(args.vault).resolve()
        cfg = load_jarvis_config(vault)
        if cfg is None:
            log(f"jarvis 비활성(볼트 아님 / jarvis 블록 없음 / enabled=false): {vault} — 침묵 종료")
            sys.exit(0)
        serve(vault, cfg)
    except JarvisConfigError as error:
        log(f"configuration error: {error}")
        sys.exit(2)


if __name__ == "__main__":
    main()
