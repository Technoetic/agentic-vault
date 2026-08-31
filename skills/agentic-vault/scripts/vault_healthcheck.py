#!/usr/bin/env python3
# agentic-vault:healthcheck engine=0.8.2
"""범용 볼트 무결성 검증 엔진 — agentic-vault 플러그인 (표준 라이브러리만 사용, 의존성 0).

설계 원칙: 플러그인 = 엔진, 볼트 = 데이터.
모든 볼트 고유 규칙(필수 키·Enum·제외 구역·로그 태그·SSOT 사실 패턴)은
볼트 루트의 00-meta/vault-config.json 에서 읽는다. 이 파일이 없으면
"볼트 아님" 한 줄을 출력하고 조용히 종료한다(exit 0 — 에러 스팸 금지).

검사 섹션:
  1   프런트매터 누락                          (치명)
  2   필수 키 누락                             (치명)
  3   Enum 위반                                (치명)
  4   프런트매터 내 따옴표 없는 위키링크       (치명 — YAML 파싱 붕괴 위험)
  4b  프런트매터 최대 줄수 초과                (관리성)
  5   데드 링크 — 코드펜스·인라인코드 제외     (관리성)
  6   고아 / 6b 준고아                         (관리성)
  6c  인덱스 미등록                            (관리성)
  7   노화 문서 — config에 stale_days > 0 설정 시만 (관리성, 선택)
  8   SSOT 사실 모순 — config에 ssot_facts 설정 시만 (관리성, 선택)
  9   로그 연산 태그 누락/형식오류 — log_tag_epoch 이후만 (치명)
  10  rules 무결성 — .claude/rules/ 존재 시만 (관리성, v0.7):
        10a 파일 크기 상한 초과 (절차성 장문 유입 경고)
        10b 엔진 rules ↔ CLAUDE.md 규칙 제목 중복 (충돌 시 모델 임의선택 방지)

종료 코드(fail-closed):
  0 = 볼트 아님(조용히 통과) 또는 치명 이슈 0건 (관리성 이슈는 리포트만)
  1 = 치명 이슈 존재, 또는 설정/경로 오류(잘못된 config·검사 대상 소실)

사용법:
  python vault_healthcheck.py [--vault <볼트 루트>] [--output <리포트 경로>]
  --vault  기본값: 환경변수 CLAUDE_PROJECT_DIR, 없으면 현재 디렉토리
  --output 기본값: vault-config.json 의 health_report (비어 있으면 00-meta/health-report.md)
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# Windows 콘솔(cp949 등)에서 한국어 출력이 깨지지 않도록 UTF-8 재설정 (실패해도 무해)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

ENGINE_VERSION = "0.8.2"
CONFIG_RELPATH = "00-meta/vault-config.json"
DEFAULT_FRONTMATTER_ROOTS = (
    "00-meta", "20-knowledge", "30-journal", "40-people", "50-projects",
)
DEFAULT_FRONTMATTER_EXEMPT_PATHS = (
    "00-meta/scratch", "00-meta/scripts", "10-inbox",
)


class HealthcheckError(RuntimeError):
    """A policy or Git-index error that must fail the commit gate closed."""


@dataclass(frozen=True)
class VaultPolicy:
    """The staged-only path policy derived from a validated config mapping."""

    frontmatter_roots: tuple[str, ...]
    frontmatter_exempt_paths: tuple[str, ...]

    @classmethod
    def from_config(cls, config: dict) -> "VaultPolicy":
        roots = config.get("frontmatter_roots")
        if roots is None:
            roots = DEFAULT_FRONTMATTER_ROOTS
        exempt = config.get("frontmatter_exempt_paths")
        if exempt is None:
            exempt = config.get("fm_exempt_zones")
        if exempt is None:
            exempt = DEFAULT_FRONTMATTER_EXEMPT_PATHS
        return cls(tuple(roots), tuple(exempt))


@dataclass(frozen=True)
class StagedChange:
    status: str
    path: str
    old_path: str | None = None

# 표준 스키마 기본값 — vault-config.json 에 없는 키는 이 값으로 동작한다.
DEFAULT_CONFIG: dict = {
    "vault_name": "Vault",
    "language": "ko",
    "deny_zones": ["10-inbox/_processed", "20-knowledge/_archive",
                   "50-projects/_completed", "90-assets", ".obsidian"],
    "exclude_dirs": ["node_modules", ".venv", "venv", "__pycache__",
                     ".git", ".trash", "step_archive"],
    "required_keys": ["title", "type", "status", "ai_priority", "tags", "created", "updated"],
    "enums": {
        "type": ["concept", "guide", "reference", "tool", "pattern", "journal",
                 "person", "organization", "meeting", "decision", "project"],
        "status": ["active", "draft", "archive"],
        "ai_priority": ["high", "medium", "low", "archive"],
    },
    "frontmatter_max_lines": 16,
    "index_note": "00-meta/index.md",
    "log_note": "00-meta/log.md",
    "log_tags": ["ingest", "query", "lint", "build", "ops", "decision"],
    "log_tag_epoch": "2026-07-03",
    "hot_note": "00-meta/hot.md",
    "handoff_note": "",
    "ssot_note": "",
    "ssot_facts": [],
    "health_report": "00-meta/health-report.md",
    "backup_target": "",
    # --- 표준 스키마 외 선택 확장 키 (없으면 해당 검사는 기본 동작/생략) ---
    "stale_days": 0,      # >0 이면 updated 기준 노화 문서 검사를 켠다(관리성)
    "index_scopes": [],   # 인덱스 등록 검사 대상 최상위 폴더 목록. 비면 메타 폴더·journal 타입 제외 전체
    "rules_dir": ".claude/rules",   # 행동 계약 rules 디렉토리(v0.6+). 없으면 섹션 10 생략
    "rules_max_lines": 120,         # rules 파일 1개 권장 상한. 초과 시 절차성 장문 유입 경고(관리성)
    # 세션마다 주입되는 노트의 **토큰 예산** (2026-08-06 신설, 관리성).
    # SessionStart 훅이 handoff_note·hot_note를 **통째로** 읽어 주입한다(자르지 않는다).
    # 이 둘은 세션이 쌓일수록 커지는데 상한이 없었다 — 실측(NS): hot 1,785토큰 +
    # handoff 3,991토큰 = 매 세션 약 5,800토큰이 예산 없이 들어갔다.
    #
    # ⚠️단위를 토큰으로 잡는 이유(단어·글자 예산이 둘 다 실패한다):
    #   · 단어 수 — hot.md의 "500단어" 규약은 영어 기준 발상이다. 한국어 528단어가 1,785토큰이다.
    #   · 글자 수 — 언어에 따라 5배 넘게 어긋난다. 한글 1자 ≈ 1.375토큰(Claude 실측),
    #     영문은 대략 4자당 1토큰이라 1자 ≈ 0.25토큰. 같은 글자 수가 전혀 다른 비용이다.
    #   · 바이트 — UTF-8에서 한글은 3바이트라 글자 수와도 어긋난다.
    # 그래서 아래 estimate_tokens()로 문자 종류별 계수를 적용해 근사한다.
    # 0이면 해당 검사를 끈다.
    "hot_max_tokens": 2000,
    "handoff_max_tokens": 4000,
    # 세션 종료 누락 감지 (2026-08-14 신설, 관리성). handoff의 기준 커밋(anchor)은
    # /session-end만 갱신하므로, git HEAD가 anchor보다 이 값 이상 앞서 있으면
    # "세션이 닫혔는데 handoff·장기기억(remember)이 낡았다"는 신호다.
    # 실사례(NS): remember 미실행 3주 공백을 어떤 계기판도 잡지 못했다.
    # 1~2커밋은 활성 세션의 정상 누적이라 임계 기본 3. 0이면 검사를 끈다.
    # handoff_note가 비어 있으면 자동 생략.
    "anchor_drift_threshold": 3,
}

_PATH_KEYS = {
    "index_note", "log_note", "hot_note", "handoff_note", "ssot_note",
    "health_report", "rules_dir",
}
_PATH_LIST_KEYS = {
    "deny_zones", "exclude_dirs", "frontmatter_roots",
    "frontmatter_exempt_paths", "fm_exempt_zones", "index_scopes",
}
_STRING_LIST_KEYS = {"required_keys", "log_tags"}
_STRING_KEYS = {"vault_name", "language", "log_tag_epoch", "backup_target"}
_INTEGER_KEYS = {
    "frontmatter_max_lines", "stale_days", "rules_max_lines",
    "hot_max_tokens", "handoff_max_tokens", "anchor_drift_threshold",
}


def _normalize_relative_path(value: object, label: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise HealthcheckError(f"{label} must be a string path")
    raw = value.strip()
    if not raw:
        if allow_empty:
            return ""
        raise HealthcheckError(f"{label} must not be empty")
    if raw.startswith(("//", "\\\\")):
        raise HealthcheckError(f"{label} must stay inside the vault (UNC path rejected)")
    if re.match(r"^[A-Za-z]:", raw):
        raise HealthcheckError(f"{label} must stay inside the vault (drive path rejected)")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/"):
        raise HealthcheckError(f"{label} must stay inside the vault (absolute path rejected)")
    segments = normalized.split("/")
    if any(segment == "" for segment in segments):
        raise HealthcheckError(f"{label} contains an empty path segment")
    if any(segment == ".." for segment in segments):
        raise HealthcheckError(f"{label} must stay inside the vault ('..' rejected)")
    return normalized


def _validate_string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise HealthcheckError(f"{label} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise HealthcheckError(f"{label}[{index}] must be a string")
        if not item.strip():
            raise HealthcheckError(f"{label}[{index}] must not be empty")
        result.append(item)
    return result


def validate_config(raw: object) -> dict:
    """Validate untrusted JSON config and return a normalized independent mapping."""
    if not isinstance(raw, dict):
        raise HealthcheckError("vault config top level must be a JSON object")

    config = {**deepcopy(DEFAULT_CONFIG), **deepcopy(raw)}

    for key in _STRING_KEYS:
        value = config.get(key)
        if not isinstance(value, str):
            raise HealthcheckError(f"{key} must be a string")

    for key in _INTEGER_KEYS:
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise HealthcheckError(f"{key} must be an integer")

    for key in _STRING_LIST_KEYS:
        config[key] = _validate_string_list(config.get(key), key)

    for key in _PATH_KEYS:
        if key in config:
            config[key] = _normalize_relative_path(config[key], key, allow_empty=True)

    for key in _PATH_LIST_KEYS:
        if key not in config:
            continue
        values = config[key]
        if not isinstance(values, list):
            raise HealthcheckError(f"{key} must be a list")
        config[key] = [
            _normalize_relative_path(item, f"{key}[{index}]", allow_empty=False)
            for index, item in enumerate(values)
        ]

    enums = config.get("enums")
    if not isinstance(enums, dict):
        raise HealthcheckError("enums must be an object")
    normalized_enums: dict[str, list[str]] = {}
    for field, allowed in enums.items():
        if not isinstance(field, str) or not field.strip():
            raise HealthcheckError("enums keys must be non-empty strings")
        normalized_enums[field] = _validate_string_list(allowed, f"enums.{field}")
    config["enums"] = normalized_enums

    facts = config.get("ssot_facts")
    if not isinstance(facts, list):
        raise HealthcheckError("ssot_facts must be a list")
    normalized_facts: list[dict] = []
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            raise HealthcheckError(f"ssot_facts[{index}] must be an object")
        label = fact.get("label")
        pattern = fact.get("pattern")
        if not isinstance(label, str) or not label.strip():
            raise HealthcheckError(f"ssot_facts[{index}].label must be a non-empty string")
        if not isinstance(pattern, str) or not pattern.strip():
            raise HealthcheckError(f"ssot_facts[{index}].pattern must be a non-empty string")
        normalized_facts.append(deepcopy(fact))
    config["ssot_facts"] = normalized_facts

    if "jarvis" in config and not isinstance(config["jarvis"], dict):
        raise HealthcheckError("jarvis must be an object")

    if "frontmatter_exempt_paths" not in raw and "fm_exempt_zones" in raw:
        config["frontmatter_exempt_paths"] = list(config["fm_exempt_zones"])

    # Deliberately do not inject frontmatter_roots into legacy config mappings.
    # Full mode uses absence as the compatibility marker for "all active notes";
    # VaultPolicy supplies the five staged defaults without erasing that marker.
    return config


def _run_git_bytes(
    vault: Path,
    *args: str,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> bytes:
    command = ["git", "-c", "core.quotepath=false", *args]
    try:
        result = subprocess.run(
            command,
            cwd=vault,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise HealthcheckError(f"git command timed out: {' '.join(args)}") from exc
    except OSError as exc:
        raise HealthcheckError(f"git command could not run: {exc}") from exc
    if result.returncode not in allowed_returncodes:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        detail = f": {stderr}" if stderr else ""
        raise HealthcheckError(f"git {' '.join(args)} failed ({result.returncode}){detail}")
    return result.stdout


def _escape_git_glob(value: str) -> str:
    """Escape config text embedded in a Git `glob` pathspec."""
    return re.sub(r"([\\*?\[])", r"\\\1", value)


def staged_markdown_pathspecs(
    config: dict,
    *,
    exclude_generated_referrers: bool = False,
) -> tuple[str, ...]:
    """Return index-only Markdown pathspecs with policy zones pruned by Git."""
    if not isinstance(config, dict):
        raise HealthcheckError("validated config must be an object")

    pathspecs = [":(top,glob)**/*.md"]
    deny_zones = sorted(set(config.get("deny_zones") or ()))
    exclude_dirs = sorted(set(config.get("exclude_dirs") or ()))
    pathspecs.extend(
        f":(exclude,top,glob){_escape_git_glob(path)}/**"
        for path in deny_zones
    )
    pathspecs.extend(
        f":(exclude,glob)**/{_escape_git_glob(name)}/**"
        for name in exclude_dirs
    )

    if exclude_generated_referrers:
        effective_health_report = (
            config.get("health_report") or DEFAULT_CONFIG["health_report"]
        )
        generated = {
            config.get("log_note") or "",
            effective_health_report,
        }
        pathspecs.extend(
            f":(exclude,top,literal){path}"
            for path in sorted(generated)
            if path
        )
    return tuple(pathspecs)


def _decode_git_path(value: bytes, label: str) -> str:
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HealthcheckError(f"{label} is not valid UTF-8") from exc
    if not decoded:
        raise HealthcheckError(f"{label} must not be empty")
    # Git already returns repository-relative paths. Do not run config-path
    # normalization here: on POSIX a backslash is a valid literal filename byte.
    return decoded


def load_staged_config(vault: Path) -> dict:
    data = _run_git_bytes(vault, "show", f":{CONFIG_RELPATH}")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HealthcheckError(f"staged {CONFIG_RELPATH} is invalid: {exc}") from exc
    return validate_config(raw)


def list_staged_changes(vault: Path, config: dict) -> list[StagedChange]:
    if not isinstance(config, dict):
        raise HealthcheckError("validated config must be an object")
    data = _run_git_bytes(
        vault,
        "diff", "--cached", "--name-status", "-z", "--find-renames",
        "--diff-filter=ACMRD", "--", *staged_markdown_pathspecs(config),
    )
    if not data:
        return []
    records = data.split(b"\0")
    if records[-1] != b"":
        raise HealthcheckError("git name-status output is not NUL terminated")
    records.pop()

    changes: list[StagedChange] = []
    index = 0
    while index < len(records):
        try:
            raw_status = records[index].decode("ascii")
        except UnicodeDecodeError as exc:
            raise HealthcheckError("git name-status record has a non-ASCII status") from exc
        index += 1
        if not raw_status:
            raise HealthcheckError("git name-status record has an empty status")
        status = raw_status[0]
        if status not in "ACMRD":
            raise HealthcheckError(f"unsupported git name-status record: {raw_status}")
        needed = 2 if status in "CR" else 1
        if index + needed > len(records):
            raise HealthcheckError(f"truncated git name-status record: {raw_status}")
        if status in "CR":
            old_path = _decode_git_path(records[index], "old staged path")
            new_path = _decode_git_path(records[index + 1], "staged path")
            changes.append(StagedChange(status, new_path, old_path))
            index += 2
        else:
            path = _decode_git_path(records[index], "staged path")
            changes.append(StagedChange(status, path))
            index += 1
    return changes


def read_index_text(vault: Path, path: str) -> str:
    if not isinstance(path, str) or not path:
        raise HealthcheckError("index path must be a non-empty string")
    if "\0" in path:
        raise HealthcheckError("index path must not contain NUL")
    # `path` originates from Git's NUL-delimited index output. It is already
    # repository-relative, so preserve every filename character exactly.
    data = _run_git_bytes(vault, "show", f":{path}")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HealthcheckError(f"staged file is not valid UTF-8: {path}") from exc

HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")

# session-end 계약이 handoff 상단에 쓰는 앵커 줄 형식 (스킬 §anchor 참조)
ANCHOR_RE = re.compile(r"\*\*기준 커밋\(anchor\):\*\*\s*`([0-9a-fA-F]{7,40})`")


def _git(vault: Path, *args: str) -> str | None:
    """git 명령 1회 실행. 실패(비리포·미설치·해석불가·타임아웃)면 None —
    조용히 넘기지 않고 호출부가 리포트에 사유를 남긴다."""
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                           cwd=vault, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def scan_anchor_drift(vault: Path, handoff_rel: str,
                      threshold: int) -> tuple[str | None, list[str]]:
    """handoff anchor와 git HEAD의 커밋 거리로 /session-end 누락을 감지한다.
    반환 (콘솔 요약 | None, 리포트 라인들). 요약이 있으면 이슈 1건으로 집계된다.
    ⚠️한계: handoff만 손으로 갱신되고 remember만 빠진 경우는 못 잡는다."""
    if threshold <= 0 or not handoff_rel:
        return None, ["- 검사 생략 (anchor_drift_threshold=0 또는 handoff_note 미설정)"]
    handoff = vault / handoff_rel
    if not handoff.is_file():
        return None, [f"- 판정 불가: handoff 없음 ({handoff_rel})"]
    m = ANCHOR_RE.search(read_text(handoff))
    if not m:
        return ("handoff에 기준 커밋(anchor) 줄이 없음 — session-end 계약 위반",
                ["- ⚠️ handoff에 `**기준 커밋(anchor):**` 줄이 없다. "
                 "session-end가 갱신하는 줄이다."])
    anchor = m.group(1)
    if _git(vault, "rev-parse", "--is-inside-work-tree") is None:
        return None, ["- 판정 불가: git 리포가 아니거나 git 실행 실패"]
    count_s = _git(vault, "rev-list", "--count", f"{anchor}..HEAD")
    if count_s is None:
        return (f"anchor `{anchor}`를 git이 해석하지 못함 — 이력 재작성/오타 의심",
                [f"- ⚠️ anchor `{anchor}` 해석 불가. handoff가 가리키는 시점이 리포에 없다."])
    drift = int(count_s)
    anchor_date = _git(vault, "log", "-1", "--format=%cs", anchor) or "?"
    if drift >= threshold:
        return (f"anchor drift {drift}커밋 — session-end 누락 의심 "
                f"(anchor `{anchor}` {anchor_date} 이후 미갱신)",
                [f"- ⚠️ **HEAD가 anchor `{anchor}`({anchor_date})보다 {drift}커밋 앞선다** "
                 f"(임계 {threshold}).",
                 "- 의미: 세션이 session-end 없이 닫혔을 개연성 — "
                 "handoff·장기기억(remember)이 그만큼 낡았다.",
                 f"- 조치: session-end 실행(anchor 갱신 + 교훈 커밋). "
                 f"중간 확인: `git log --oneline {anchor}..HEAD`"])
    return None, [f"- 정상: anchor `{anchor}`({anchor_date}) 대비 HEAD {drift}커밋 "
                  f"(임계 {threshold} 미만)"]


def estimate_tokens(text: str) -> int:
    """문자 종류별 계수로 토큰 수를 근사한다(정확한 토크나이저 대체 아님).

    예산 경보용 근사치이므로 오차 10~20%는 허용된다. 계수 근거:
      · 한글 1자 ≈ 1.375토큰 (Claude gen5 count_tokens 실측)
      · 그 외(영문·숫자·공백·기호) ≈ 0.3토큰/자 (영어 약 4자/토큰 기준)
    언어에 무관하게 동작하는 것이 목적이다 — 글자 수·단어 수 예산은
    한국어와 영어에서 5배 넘게 어긋난다(DEFAULT_CONFIG 주석 참조).
    """
    hangul = len(HANGUL_RE.findall(text))
    other = len(text) - hangul
    return int(hangul * 1.375 + other * 0.3)


WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:[#|][^\]]*)?\]\]")
# 프런트매터에서 따옴표 없이 시작하는 위키링크 값 (YAML 중첩 배열로 오파싱 → 붕괴)
UNQUOTED_FM_LINK_RE = re.compile(r"^\s*(?:[\w_]+:|-)\s*\[\[")
# 코드펜스/인라인 코드 내부의 위키링크·사실 값은 렌더링/표기 대상이 아니므로 제외
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
# 로그 엄격 형식: '- YYYY-MM-DD [HH:MM] | 행위자 | 본문'
# ⚠️시각(HH:MM)은 **선택**이다. 2026-08-05 정정:
#   기존 정규식이 시각을 필수로 요구해, 날짜만 쓴 항목이 전부
#   '형식오류→태그 없음'으로 오탐되고 그대로 치명 등급에 합산됐다.
#   실측(운영 볼트): 치명 46건 중 44건이 이 오탐이었고, 태그는 멀쩡히 붙어 있었다.
#   그 결과 healthcheck가 매 실행마다 치명 44건을 외쳤고 리포트는 17일간 방치됐다
#   — 이 파일 하단의 "매번 exit 1이면 신호가 무의미해진다"가 경고한 바로 그 상태다.
#   소급 적용하려면 없는 시각을 지어내야 하므로 데이터 오염이기도 하다.
#   태그 규약의 목적은 grep 가능성이지 시각 기록이 아니다.
LOG_ENTRY_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2})(?:\s+[\d:]+)?\s*\|[^|]*\|\s*(.*)$")
# 넓은 게이트: 불릿 + 어딘가에 날짜 → 로그형 라인 후보 (fail-closed의 핵심 —
# 엄격 형식을 벗어난 로그형 라인이 조용히 감사를 회피하지 못하게 한다)
LOG_LIKE_RE = re.compile(r"^\s*[-*]\s.*?(\d{4}-\d{2}-\d{2})")
LOG_TAG_RE = re.compile(r"^\[([\w-]+)\]")  # 본문 맨 앞의 [op] 태그
# 확장자가 붙은 링크 대상 판별 (자산 임베드 [[image.png]] 등)
FILE_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")
# 볼트 노트 스키마의 적용 대상이 아닌 도구/리포 문서 (위치 불문 이름으로 제외)
SKIP_FILENAMES = {"claude.md", "agents.md", "readme.md"}


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def rel_posix(p: Path, vault: Path) -> str:
    return p.relative_to(vault).as_posix()


def norm_cfg_path(value) -> str:
    """config의 경로 값을 posix 상대경로로 정규화한다 (비면 '')."""
    return str(value or "").replace("\\", "/").strip().strip("/")


def build_deny_rules(deny_zones) -> tuple[list[str], set[str]]:
    """deny_zones 항목을 (경로 접두 목록, 이름 집합)으로 분해한다.
    '10-inbox/_processed' 처럼 /가 있으면 루트 기준 접두 매칭,
    '.obsidian' 처럼 단일 세그먼트면 어느 깊이든 디렉토리 이름 매칭."""
    prefixes: list[str] = []
    names: set[str] = set()
    for z in deny_zones or []:
        z = norm_cfg_path(z)
        if not z:
            continue
        if "/" in z:
            prefixes.append(z)
        else:
            names.add(z)
    return prefixes, names


def is_denied(rel: str, prefixes: list[str], names: set[str]) -> bool:
    if any(rel == pre or rel.startswith(pre + "/") for pre in prefixes):
        return True
    return any(part in names for part in rel.split("/")[:-1])


def collect_md_files(vault: Path, exclude_names: set[str]) -> list[Path]:
    """exclude_dirs(이름 매칭)를 걷기 단계에서 가지치기하며 .md 를 수집한다.
    deny_zones 는 여기서 제외하지 않는다 — 링크 해석 인덱스에는 포함돼야
    아카이브로 이동한 노트를 가리키는 링크가 데드 링크로 오탐되지 않는다."""
    results: list[Path] = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in exclude_names and d != ".git"]
        for f in files:
            if f.lower().endswith(".md"):
                results.append(Path(root) / f)
    return results


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """(frontmatter 원문, 본문)을 반환. 프런트매터가 없으면 (None, 전체)."""
    if not text.startswith("---"):
        return None, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def parse_simple_yaml_keys(fm: str) -> dict[str, str]:
    """최상위 'key: value' 쌍만 단순 추출한다 (완전한 YAML 파서 아님)."""
    keys: dict[str, str] = {}
    for line in fm.splitlines():
        m = re.match(r"^([\w_]+):\s*(.*)$", line)
        if m:
            keys[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return keys


def normalize_link_target(raw: str) -> str | None:
    """위키링크 대상을 노트 이름(stem)으로 정규화한다.
    - 경로형 [[folder/note]] → 'note'
    - .md 확장자 명시 → 제거
    - 그 외 확장자([[image.png]] 등 자산 임베드) → None (노트 링크 아님)"""
    base = raw.split("/")[-1].strip()
    if not base:
        return None
    if FILE_EXT_RE.search(base):
        if base.lower().endswith(".md"):
            return base[:-3].strip() or None
        return None
    return base


def _decode_nul_paths(data: bytes, label: str) -> list[str]:
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise HealthcheckError(f"{label} output is not NUL terminated")
    return [
        _decode_git_path(raw_path, label)
        for raw_path in data[:-1].split(b"\0")
    ]


def _index_markdown_paths(vault: Path, config: dict) -> list[str]:
    data = _run_git_bytes(
        vault,
        "ls-files", "-z", "--cached", "--",
        *staged_markdown_pathspecs(config),
    )
    return _decode_nul_paths(data, "staged Markdown path")


def _posix_markdown_stem(path: str) -> str:
    """Derive a stem without rewriting Git-originated path characters."""
    filename = path.rsplit("/", 1)[-1]
    if filename.lower().endswith(".md"):
        return filename[:-3]
    return filename


def _is_at_or_below(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _staged_schema_errors(path: str, text: str, config: dict) -> list[str]:
    policy = VaultPolicy.from_config(config)
    if not any(_is_at_or_below(path, root) for root in policy.frontmatter_roots):
        return []
    if any(_is_at_or_below(path, exempt) for exempt in policy.frontmatter_exempt_paths):
        return []

    fm, _body = split_frontmatter(text)
    if fm is None:
        return [f"스테이징 차단: {path} — 프런트매터 누락"]

    errors: list[str] = []
    keys = parse_simple_yaml_keys(fm)
    absent = [key for key in config.get("required_keys", ()) if key not in keys]
    if absent:
        errors.append(
            f"스테이징 차단: {path} — 필수 키 누락: {', '.join(sorted(absent))}"
        )
    for field, allowed in sorted((config.get("enums") or {}).items()):
        value = keys.get(field)
        if value and value not in allowed:
            errors.append(f"스테이징 차단: {path} — Enum 위반: {field}={value}")
    for line in fm.splitlines():
        if UNQUOTED_FM_LINK_RE.match(line):
            errors.append(
                f"스테이징 차단: {path} — 프런트매터 내 따옴표 없는 위키링크: "
                f"{line.strip()}"
            )
    return errors


def _grep_index_backlink_paths(vault: Path, config: dict, stem: str) -> list[str]:
    escaped_stem = re.escape(stem)
    pattern = (
        r"\[\[([^]|#]*/)*" + escaped_stem
        + r"(\.md)?([|#][^]]*)?\]\]"
    )
    data = _run_git_bytes(
        vault,
        "grep", "--cached", "-z", "-l", "-I", "-E", "-e", pattern,
        "--", *staged_markdown_pathspecs(config, exclude_generated_referrers=True),
        allowed_returncodes=(0, 1),
    )
    return _decode_nul_paths(data, "staged backlink path")


def validate_staged(vault: Path, config: dict) -> list[str]:
    """Validate only the staged Markdown change surface without writing a report."""
    if not isinstance(config, dict):
        raise HealthcheckError("validated config must be an object")

    changes = list_staged_changes(vault, config)
    errors: list[str] = []
    deleted_stems: set[str] = set()

    for change in changes:
        if change.status in "ACMR":
            text = read_index_text(vault, change.path)
            errors.extend(_staged_schema_errors(change.path, text, config))

        if change.status == "D":
            deleted_stems.add(_posix_markdown_stem(change.path))
        elif change.status == "R" and change.old_path is not None:
            old_stem = _posix_markdown_stem(change.old_path)
            if old_stem != _posix_markdown_stem(change.path):
                deleted_stems.add(old_stem)

    if deleted_stems:
        result_stems = {
            _posix_markdown_stem(path)
            for path in _index_markdown_paths(vault, config)
        }
        for stem in sorted(deleted_stems):
            if not stem or stem in result_stems:
                continue
            for referrer in _grep_index_backlink_paths(vault, config, stem):
                errors.append(
                    f"스테이징 차단: {referrer} — 삭제·개명된 노트 '{stem}' "
                    "백링크가 남아 있음"
                )

    return sorted(set(errors))


def scan_log_tag_gaps(log_path: Path, valid_ops: set[str],
                      epoch: date) -> tuple[list[str], list[str]]:
    """log 노트에서 (태그 누락/형식오류 항목, 미승인 태그 항목)을 반환한다.
    epoch(태그 도입일) 이후 항목만 검사한다 — 과거 로그는 소급 대상이 아니므로
    '도입일 이후 로그는 100% 태그됨'이 결정론적으로 검증 가능한 사실이 된다.
    fail-closed: 도입일 이후 로그형 라인이 엄격 형식을 벗어나면 조용히 넘기지 않고
    'malformed'로 색출한다(무태그 항목이 형식 변형으로 감사를 회피하는 것 방지)."""
    missing: list[str] = []
    unknown: list[str] = []
    # 헤더의 형식 예시 등 코드펜스 안의 라인은 실제 로그가 아니므로 제외
    text = CODE_FENCE_RE.sub("", read_text(log_path))
    for raw in text.splitlines():
        strict = LOG_ENTRY_RE.match(raw)
        excerpt = raw[:80]
        if strict:
            try:
                entry_d = datetime.strptime(strict.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            if entry_d < epoch:
                continue  # 도입일 이전 = 소급 대상 아님
            summary = strict.group(2).lstrip()
            tag_m = LOG_TAG_RE.match(summary)
            if not tag_m:
                missing.append(excerpt)
            elif tag_m.group(1) not in valid_ops:
                unknown.append(f"{excerpt}  (태그: [{tag_m.group(1)}])")
            continue
        # 엄격 패턴에 안 맞음 — 로그형(불릿+날짜) 후보인지 넓은 게이트로 재검사
        loose = LOG_LIKE_RE.match(raw)
        if not loose:
            continue  # 로그 항목이 아님(헤더·산문·표 등) → 검사 대상 아님
        try:
            entry_d = datetime.strptime(loose.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if entry_d < epoch:
            continue
        missing.append(f"{excerpt}  (형식오류: `- YYYY-MM-DD HH:MM | 행위자 |` 벗어남)")
    return missing, unknown


def compile_fact_patterns(ssot_facts, warnings: list[str]) -> list[tuple[str, re.Pattern]]:
    """ssot_facts([{"label","pattern"}])를 컴파일한다. 잘못된 항목은 경고로 표면화."""
    compiled: list[tuple[str, re.Pattern]] = []
    for i, item in enumerate(ssot_facts or []):
        if not isinstance(item, dict):
            warnings.append(f"ssot_facts[{i}] 형식 오류 — {{\"label\", \"pattern\"}} 객체가 아님. 건너뜀.")
            continue
        label = str(item.get("label", "")).strip()
        pat = str(item.get("pattern", "")).strip()
        if not label or not pat:
            warnings.append(f"ssot_facts[{i}] label/pattern 누락 — 건너뜀.")
            continue
        try:
            compiled.append((label, re.compile(pat)))
        except re.error as e:
            warnings.append(f"ssot_facts '{label}' 정규식 컴파일 실패({e}) — 건너뜀.")
    return compiled


RULE_HEADING_RE = re.compile(r"^#{1,3}\s+(.+?)\s*$")


def _norm_heading(h: str) -> str:
    """규칙 제목을 비교용으로 정규화 — 표기 차이(강조·번호·괄호주석)만 흡수하고
    제목의 실질 내용은 보존한다(콜론 뒤 대상어를 함부로 버리지 않는다 — 한국어에서
    콜론 뒤가 핵심인 경우가 많아 오판 위험)."""
    h = re.sub(r"[*_`]", "", h)                       # 강조 기호 제거
    h = re.sub(r"^\s*\d+(?:[.\-)]\d*)*[.\-)]?\s+", "", h)  # 선두 번호 접두사 제거 ("1. ", "2-3) " 등)
    h = re.sub(r"\s*\([^)]*\)", "", h)                # 괄호 주석 제거 (예: "(위반 시 …)")
    h = re.sub(r"\s*[—-]\s.*$", "", h)                # 대시 이후 부연 제거 ("— 절대 규칙" 등)
    return " ".join(h.split()).strip().lower()


def scan_rules_integrity(rules_dir: Path, claude_md: Path, max_lines: int
                         ) -> tuple[list[tuple[str, int]], list[str]]:
    """(크기 초과 rules 목록, CLAUDE.md와 제목이 겹치는 rules 목록)을 반환한다.
    - 크기: rules 파일이 max_lines 초과 → 절차성 장문 유입 신호(rules는 상시 로드라 얇아야 함).
    - 중복: 엔진 rules의 ## 제목이 CLAUDE.md 본문 제목과 겹치면, 같은 주제를 두 층이
      다르게 서술할 위험 — 충돌 시 우선순위 규칙이 없어 모델이 임의 선택한다(v0.7 근거)."""
    oversized: list[tuple[str, int]] = []
    dup_headings: list[str] = []
    rule_files = sorted(rules_dir.glob("*.md"))
    # CLAUDE.md 본문(마커/주석 제외)의 규칙 제목 집합
    claude_headings: set[str] = set()
    if claude_md.is_file():
        ctext = CODE_FENCE_RE.sub("", read_text(claude_md))
        for line in ctext.splitlines():
            m = RULE_HEADING_RE.match(line)
            if m:
                claude_headings.add(_norm_heading(m.group(1)))
    for rf in rule_files:
        rtext = read_text(rf)
        # 코드펜스 내부는 예시 인용일 뿐 실제 규칙/제목이 아니므로 크기·제목 판정에서 제외
        rclean = CODE_FENCE_RE.sub("", rtext)
        body_lines = [ln for ln in rclean.splitlines()
                      if not ln.strip().startswith("<!--")]  # 스탬프 주석 제외 실질 줄수
        n = len([ln for ln in body_lines if ln.strip()])
        if n > max_lines:
            oversized.append((rf.name, n))
        for line in rclean.splitlines():
            m = RULE_HEADING_RE.match(line)
            if m and _norm_heading(m.group(1)) in claude_headings:
                dup_headings.append(f"{rf.name} → `{m.group(1).strip()}` (CLAUDE.md와 중복)")
    return oversized, dup_headings


def main() -> int:
    ap = argparse.ArgumentParser(description="볼트 무결성 검증 엔진 (agentic-vault)")
    ap.add_argument("--vault",
                    default=os.environ.get("CLAUDE_PROJECT_DIR") or ".",
                    help="볼트 루트 경로 (기본: $CLAUDE_PROJECT_DIR, 없으면 cwd)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true",
                      help="Git index의 커밋 대상만 검사하고 리포트는 쓰지 않음")
    mode.add_argument("--output", default=None,
                      help="full 모드 리포트 출력 경로 (기본: config의 health_report)")
    args = ap.parse_args()

    vault = Path(args.vault).resolve()

    if args.staged:
        try:
            cfg = load_staged_config(vault)
            diagnostics = validate_staged(vault, cfg)
        except (OSError, ValueError, HealthcheckError) as e:
            print(f"[vault-healthcheck] staged 오류: {e}", file=sys.stderr)
            return 1
        for diagnostic in diagnostics:
            print(diagnostic, file=sys.stderr)
        return 1 if diagnostics else 0

    # --- 볼트 감지: 00-meta/vault-config.json 이 없으면 조용히·정중히 무동작 ---
    cfg_path = vault / CONFIG_RELPATH
    if not cfg_path.is_file():
        # stderr로 출력 — SessionStart 훅이 exit 0의 stdout을 컨텍스트에 주입하므로
        # 비볼트 프로젝트의 세션에 잡음이 들어가지 않게 한다(CLI 직접 실행 시에도 안내는 보임).
        print(f"[vault-healthcheck] 볼트 아님 — {vault} 에 {CONFIG_RELPATH} 이 없어 검사를 건너뜁니다.",
              file=sys.stderr)
        return 0
    try:
        user_cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        cfg = validate_config(user_cfg)
    except (OSError, ValueError, HealthcheckError) as e:
        print(f"[vault-healthcheck] 오류: {CONFIG_RELPATH} 파싱 실패 — {e}", file=sys.stderr)
        return 1
    warnings: list[str] = []

    # --- 설정 해석 -----------------------------------------------------------
    vault_name = str(cfg.get("vault_name") or "Vault")
    deny_prefixes, deny_names = build_deny_rules(cfg.get("deny_zones"))
    exclude_names = {str(d).strip() for d in (cfg.get("exclude_dirs") or []) if str(d).strip()}
    required_keys = [str(k) for k in (cfg.get("required_keys") or [])]
    enum_sets = {k: set(v) for k, v in (cfg.get("enums") or {}).items()
                 if isinstance(v, list) and v}
    try:
        fm_max_lines = int(cfg.get("frontmatter_max_lines") or 16)
    except (TypeError, ValueError):
        fm_max_lines = 16
        warnings.append("frontmatter_max_lines 값이 정수가 아님 — 기본값 16 사용.")
    try:
        stale_days = int(cfg.get("stale_days") or 0)
    except (TypeError, ValueError):
        stale_days = 0
    try:
        rules_max_lines = int(cfg.get("rules_max_lines") or 120)
    except (TypeError, ValueError):
        rules_max_lines = 120
        warnings.append("rules_max_lines 값이 정수가 아님 — 기본값 120 사용.")
    # 빈 문자열이면 섹션 10 끄기(다른 경로 옵션과 동일한 관례 — or 폴백 금지).
    # 키 자체가 없을 때만 기본값 .claude/rules 적용.
    rules_dir_rel = norm_cfg_path(
        cfg.get("rules_dir") if "rules_dir" in user_cfg else ".claude/rules")
    # 볼트 밖 스캔 방어: 절대경로·드라이브문자·상위참조는 무시(다른 경로 값과 달리
    # rules_dir은 디렉토리를 통째 순회하므로 볼트 이탈 시 임의 위치 노출 위험).
    if rules_dir_rel and (Path(rules_dir_rel).is_absolute()
                          or re.match(r"^[A-Za-z]:", rules_dir_rel)
                          or ".." in rules_dir_rel.split("/")):
        warnings.append(f"rules_dir 값('{rules_dir_rel}')이 볼트 루트 밖을 가리킴 — 무시하고 섹션 10 생략.")
        rules_dir_rel = ""

    index_rel = norm_cfg_path(cfg.get("index_note"))
    log_rel = norm_cfg_path(cfg.get("log_note"))
    ssot_rel = norm_cfg_path(cfg.get("ssot_note"))
    valid_ops = {str(t).strip() for t in (cfg.get("log_tags") or []) if str(t).strip()}

    epoch: date | None = None
    epoch_raw = str(cfg.get("log_tag_epoch") or "").strip()
    if epoch_raw:
        try:
            epoch = datetime.strptime(epoch_raw, "%Y-%m-%d").date()
        except ValueError:
            warnings.append(f"log_tag_epoch 형식 오류('{epoch_raw}') — "
                            f"전체 기간을 검사합니다(fail-closed).")
    if epoch is None:
        epoch = date.min  # 경계 미설정 = 전 기간 검사

    fact_patterns = compile_fact_patterns(cfg.get("ssot_facts"), warnings)

    out_rel = args.output or norm_cfg_path(cfg.get("health_report")) or "00-meta/health-report.md"
    out = vault / out_rel  # out_rel 이 절대경로면 pathlib 이 그대로 절대경로를 취한다
    try:
        out_resolved = out.resolve()
    except OSError:
        out_resolved = out

    # 허브/구조 파일(설정이 가리키는 노트)은 지식 그래프 노드가 아니므로 고아 판정에서 제외
    special_rels: set[str] = set()
    for key in ("index_note", "log_note", "hot_note", "handoff_note", "ssot_note", "health_report"):
        v = norm_cfg_path(cfg.get(key))
        if v:
            special_rels.add(v)
    hub_stems = {Path(v).stem for v in special_rels}

    # --- 파일 수집 ------------------------------------------------------------
    all_md = collect_md_files(vault, exclude_names)
    # 링크 해석용 인덱스는 deny zone 포함 전체(아카이브로 이동한 노트 링크는 유효),
    # 린트 대상은 deny zone·도구 문서·리포트 자신을 제외한 활성 영역만.
    # 주의: 옵시디언 frontmatter aliases 는 원시 위키링크를 해석하지 못한다 —
    # 파일명(stem)만 해석 대상이다.
    name_index = {p.stem for p in all_md}
    targets: list[Path] = []
    for p in all_md:
        rel = rel_posix(p, vault)
        if is_denied(rel, deny_prefixes, deny_names):
            continue
        if p.name.lower() in SKIP_FILENAMES:
            continue
        try:
            if p.resolve() == out_resolved:
                continue  # 리포트 자신은 검사하지 않는다
        except OSError:
            pass
        targets.append(p)

    if not targets and name_index:
        # 침묵 통과(silent green) 방지: 노트는 있는데 검사 대상이 0개면 제외 규칙 과잉
        print("[vault-healthcheck] 오류: .md 파일은 있으나 검사 대상이 0개입니다 — "
              "deny_zones/exclude_dirs 설정을 확인하세요.", file=sys.stderr)
        return 1

    # --- 본 검사 루프 ----------------------------------------------------------
    missing_fm: list[str] = []
    missing_keys: list[tuple[str, list[str]]] = []
    enum_violations: list[tuple[str, str, str]] = []
    unquoted_links: list[str] = []
    oversized_fm: list[tuple[str, int]] = []
    dead_links: list[tuple[str, str]] = []
    stale: list[tuple[str, str]] = []
    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, int] = {p.stem: 0 for p in targets}
    note_types: dict[str, str] = {}
    fact_hits: dict[str, dict[str, list[str]]] = {label: {} for label, _ in fact_patterns}

    # 프런트매터 면제 구역 — 2026-08-05 신설.
    # 구 버전이 하드코딩하던 면제('10-inbox/*', '00-meta/scratch/*' 등)가
    # config 방식으로 전환되며 통째로 누락됐다. 그 결과 원시 캡처(수집 대기열)와
    # 슬래시 명령 정의까지 '프런트매터 누락 — 치명'으로 잡혀 오탐이 쌓였다.
    # 실측(운영 볼트): 이 누락만으로 치명 22건이 발생했고, 전부 노트가 아닌 파일이었다.
    # config에 키가 없으면 아래 기본값을 쓴다(구 버전 동작과 호환).
    if "frontmatter_exempt_paths" in cfg:
        fm_exempt = cfg["frontmatter_exempt_paths"]
    elif "fm_exempt_zones" in cfg:
        fm_exempt = cfg["fm_exempt_zones"]
    else:
        fm_exempt = ["10-inbox", "00-meta/scratch", "00-meta/scripts", ".claude"]

    full_fm_roots = tuple(cfg["frontmatter_roots"]) if "frontmatter_roots" in user_cfg else None

    def is_fm_exempt(rel_path: str) -> bool:
        if any(rel_path == z or rel_path.startswith(z.rstrip("/") + "/")
               for z in fm_exempt):
            return True
        if full_fm_roots is not None:
            return not any(_is_at_or_below(rel_path, root) for root in full_fm_roots)
        return False

    for p in targets:
        rel = rel_posix(p, vault)
        text = read_text(p)
        fm, _body = split_frontmatter(text)

        exempt = is_fm_exempt(rel)

        if fm is None:
            if not exempt:
                missing_fm.append(rel)
        elif exempt:
            # 면제 구역에 프런트매터가 있어도 볼트 노트 스키마를 강요하지 않는다.
            # 예: .claude/commands/*.md 는 슬래시 명령 정의라 자체 스키마(description 등)를
            # 쓴다. 노트 필수 키(title·type·tags…)를 요구하면 전량 오탐이 된다(실측 9건).
            pass
        else:
            fm_lines = len(fm.splitlines())
            if fm_lines > fm_max_lines:
                oversized_fm.append((rel, fm_lines))
            keys = parse_simple_yaml_keys(fm)
            note_types[rel] = keys.get("type", "")
            absent = [k for k in required_keys if k not in keys]
            if absent:
                missing_keys.append((rel, absent))
            for field, allowed in enum_sets.items():
                v = keys.get(field)
                if v and v not in allowed:
                    enum_violations.append((rel, field, v))
            for line in fm.splitlines():
                if UNQUOTED_FM_LINK_RE.match(line):
                    unquoted_links.append(f"{rel} → `{line.strip()}`")
            if stale_days > 0:
                try:
                    upd_d = datetime.strptime(keys.get("updated", ""), "%Y-%m-%d").date()
                    if (date.today() - upd_d).days > stale_days:
                        stale.append((rel, keys.get("updated", "")))
                except ValueError:
                    pass  # 날짜 미기재/형식 오류는 필수 키 검사 쪽에서 다룸

        # 코드펜스·인라인코드 내부는 링크/사실 표기 분석에서 제외
        clean_text = INLINE_CODE_RE.sub("", CODE_FENCE_RE.sub("", text))

        links: set[str] = set()
        for raw_target in WIKILINK_RE.findall(clean_text):
            base = normalize_link_target(raw_target)
            if base is None:
                continue  # 자산 임베드([[img.png]] 등) — 노트 그래프 밖
            if base in incoming:
                incoming[base] += 1
            if base not in name_index and base not in links:
                # 같은 파일 안의 동일 데드링크는 1건으로 집계 (occurrence 중복 방지)
                dead_links.append((rel, raw_target.strip()))
            links.add(base)
        outgoing[p.stem] = links

        for label, rx in fact_patterns:
            vals = set()
            for m in rx.finditer(clean_text):
                vals.add(m.group(1) if m.groups() else m.group(0))
            for v in vals:
                fact_hits[label].setdefault(v, []).append(rel)

    # --- 고아 / 준고아 ---------------------------------------------------------
    orphans = [p for p in targets
               if not outgoing.get(p.stem) and incoming.get(p.stem, 0) == 0
               and p.stem not in hub_stems]
    semi_orphans = [p for p in targets
                    if incoming.get(p.stem, 0) == 0 and p.stem not in hub_stems
                    and p not in orphans]

    # --- 인덱스 미등록 ----------------------------------------------------------
    unindexed: list[str] = []
    index_skip_note: str | None = None
    index_path = (vault / index_rel) if index_rel else None
    if index_path is not None and index_path.is_file():
        idx_text = CODE_FENCE_RE.sub("", read_text(index_path))
        idx_links = set()
        for raw_target in WIKILINK_RE.findall(idx_text):
            base = normalize_link_target(raw_target)
            if base:
                idx_links.add(base)
        scopes = [norm_cfg_path(s) for s in (cfg.get("index_scopes") or []) if norm_cfg_path(s)]
        meta_top = index_rel.split("/")[0] if "/" in index_rel else ""
        for p in targets:
            rel = rel_posix(p, vault)
            if rel in special_rels:
                continue
            if scopes:
                if not any(rel == s or rel.startswith(s + "/") for s in scopes):
                    continue
            else:
                # 기본 스코프: 메타 폴더(index가 사는 최상위 폴더)와 journal 타입 제외 전체
                if meta_top and (rel == meta_top or rel.startswith(meta_top + "/")):
                    continue
                if note_types.get(rel) == "journal":
                    continue
            if p.stem not in idx_links:
                unindexed.append(rel)
    else:
        index_skip_note = "- 검사 생략 (config의 index_note 미설정 또는 파일 없음)"

    # --- SSOT 사실 모순: 같은 패턴에서 서로 다른 값이 2개 이상 = 결정론 누수 ------
    fact_conflicts: list[tuple[str, dict[str, list[str]]]] = [
        (label, vals) for label, vals in fact_hits.items() if len(vals) >= 2
    ]

    # --- 로그 연산 태그 --------------------------------------------------------
    log_tag_missing: list[str] = []
    log_tag_unknown: list[str] = []
    log_skip_note: str | None = None
    log_path = (vault / log_rel) if log_rel else None
    if log_path is not None and log_path.is_file() and valid_ops:
        log_tag_missing, log_tag_unknown = scan_log_tag_gaps(log_path, valid_ops, epoch)
    else:
        log_skip_note = "- 검사 생략 (config의 log_note/log_tags 미설정 또는 파일 없음)"

    # --- rules 무결성 (v0.7) — .claude/rules/ 존재 시만 ------------------------
    rules_oversized: list[tuple[str, int]] = []
    rules_dup: list[str] = []
    rules_skip_note: str | None = None
    rules_path = vault / rules_dir_rel if rules_dir_rel else None
    if rules_path is not None and rules_path.is_dir() and any(rules_path.glob("*.md")):
        rules_oversized, rules_dup = scan_rules_integrity(
            rules_path, vault / "CLAUDE.md", rules_max_lines)
    else:
        rules_skip_note = f"- 검사 생략 ({rules_dir_rel}/ 없음 — v0.6+ 3층 계약 미설치 볼트)"

    # --- 섹션 11: 세션 주입 예산 (2026-08-06 신설, 관리성) ------------------------
    # SessionStart 훅이 통째로 주입하는 노트가 예산 없이 커지는 것을 감시한다.
    # 글자 수 기준인 이유는 위 DEFAULT_CONFIG 주석 참조(한국어 토큰 비례).
    injection_over: list[tuple[str, int, int]] = []
    for cfg_key, budget_key in (("hot_note", "hot_max_tokens"),
                                ("handoff_note", "handoff_max_tokens")):
        rel = norm_cfg_path(cfg.get(cfg_key))
        if not rel:
            continue
        try:
            budget = int(cfg.get(budget_key) or 0)
        except (TypeError, ValueError):
            budget = 0
            warnings.append(f"{budget_key} 값이 정수가 아님 — 검사 생략.")
        if budget <= 0:
            continue
        note_path = vault / rel
        if not note_path.is_file():
            continue
        n_tokens = estimate_tokens(read_text(note_path))
        if n_tokens > budget:
            injection_over.append((rel, n_tokens, budget))

    # --- 섹션 12: 세션 종료 누락 의심 (anchor drift, 2026-08-14 신설, 관리성) -----
    try:
        drift_threshold = int(cfg.get("anchor_drift_threshold") or 0)
    except (TypeError, ValueError):
        drift_threshold = 0
        warnings.append("anchor_drift_threshold 값이 정수가 아님 — 검사 생략.")
    drift_summary, drift_lines = scan_anchor_drift(
        vault, norm_cfg_path(cfg.get("handoff_note")) or "", drift_threshold)

    # --- 집계 ------------------------------------------------------------------
    total_issues = (len(missing_fm) + len(missing_keys) + len(enum_violations)
                    + len(unquoted_links) + len(oversized_fm)
                    + len(dead_links) + len(orphans) + len(semi_orphans)
                    + len(unindexed) + len(stale) + len(fact_conflicts)
                    + len(log_tag_missing) + len(log_tag_unknown)
                    + len(rules_oversized) + len(rules_dup)
                    + len(injection_over)
                    + (1 if drift_summary else 0))
    # fail-closed 종료 코드: '시스템을 깨뜨리는 치명 위반'만 non-zero.
    #   프런트매터 붕괴/필수키/따옴표 없는 링크 → Dataview·YAML 붕괴
    #   Enum 위반 → type/status 오염
    #   로그 태그 누락·미승인·형식오류 → grep 데이터 계약 위반
    # 관리성(과대 프런트매터·데드링크·고아·미등록·노화·SSOT 대기)은 리포트만 남기고 0 —
    # 매번 exit 1 이면 신호가 무의미해진다.
    critical = (len(missing_fm) + len(missing_keys) + len(enum_violations)
                + len(unquoted_links) + len(log_tag_missing) + len(log_tag_unknown))

    # --- 리포트 작성 -------------------------------------------------------------
    today = date.today().isoformat()
    ssot_ref = f"[[{Path(ssot_rel).stem}]]" if ssot_rel else "SSOT 노트(config의 ssot_note)"
    lines = [
        "---",
        'title: "health-report — 볼트 무결성 리포트"',
        "type: reference",
        "status: active",
        "ai_priority: low",
        "tags: [healthcheck, report]",
        f"created: {today}",
        f"updated: {today}",
        "---",
        "",
        f"# {vault_name} 무결성 리포트 ({today})",
        "",
        f"- 검사 노트 수: {len(targets)} / 총 이슈: **{total_issues}** "
        f"(치명 {critical} / 관리성 {total_issues - critical})",
        "",
    ]
    if warnings:
        lines += [
            f"## 0. 설정 경고 ({len(warnings)})",
            *[f"- {w}" for w in warnings],
            "",
        ]
    lines += [
        f"## 1. 프런트매터 누락 — 치명 ({len(missing_fm)})",
        *([f"- {x}" for x in missing_fm] or ["- 없음"]),
        "",
        f"## 2. 필수 키 누락 — 치명 ({len(missing_keys)})",
        *([f"- {f} → 누락: {', '.join(ks)}" for f, ks in missing_keys] or ["- 없음"]),
        "",
        f"## 3. Enum 위반 — 치명 ({len(enum_violations)})",
        *([f"- {f} → {k}: `{v}`" for f, k, v in enum_violations] or ["- 없음"]),
        "",
        f"## 4. 프런트매터 내 따옴표 없는 위키링크 — 치명·YAML 붕괴 위험 ({len(unquoted_links)})",
        *([f"- {x}" for x in unquoted_links] or ["- 없음"]),
        "",
        f"## 4b. 프런트매터 과대 — {fm_max_lines}줄 초과 — 관리성 ({len(oversized_fm)})",
        *([f"- {f} ({n}줄)" for f, n in oversized_fm] or ["- 없음"]),
        "",
        f"## 5. 데드 링크 — 관리성 ({len(dead_links)})",
        *([f"- {f} → [[{t}]]" for f, t in dead_links] or ["- 없음"]),
        "",
        f"## 6. 고아 노드 — 관리성 ({len(orphans)})",
        *([f"- {rel_posix(p, vault)}" for p in orphans] or ["- 없음"]),
        "",
        f"## 6b. 준고아 — 어디서도 참조되지 않음 — 관리성 ({len(semi_orphans)})",
        *([f"- {rel_posix(p, vault)}" for p in semi_orphans] or ["- 없음"]),
        "",
        f"## 6c. 인덱스 미등록 — {index_rel or 'index'} 에 위키링크 없음 — 관리성 ({len(unindexed)})",
        *(([index_skip_note] if index_skip_note else None)
          or [f"- {x}" for x in unindexed] or ["- 없음"]),
        "",
        f"## 7. 노화 문서 — updated {stale_days}일 초과 — 관리성 ({len(stale)})",
        *(([f"- {f} (updated: {u})" for f, u in stale] or ["- 없음"]) if stale_days > 0
          else ["- 검사 생략 (config에 stale_days 미설정 — >0 으로 켤 수 있음)"]),
        "",
        f"## 8. SSOT 사실 모순 — 같은 식별자가 여러 값으로 표기 — 관리성 ({len(fact_conflicts)})",
    ]
    if fact_patterns:
        lines += [
            f"  (기준: {ssot_ref} — 결정론 누수. 충돌 값은 정합 후 SSOT 한 곳만 두고 "
            f"나머지는 위키링크로 참조)",
            *([line
               for label, vals in fact_conflicts
               for line in (
                   [f"- **{label}**: {len(vals)}개 값 충돌"]
                   + [f"    - `{val}` → {', '.join(notes)}" for val, notes in sorted(vals.items())]
               )] or ["- 없음"]),
        ]
    else:
        lines += ["- 검사 생략 (config에 ssot_facts 미설정)"]
    lines += [
        "",
        f"## 9. 로그 연산 태그 — {epoch_raw or '전 기간'} 이후 항목 — 치명 "
        f"(누락·형식오류 {len(log_tag_missing)} / 미승인태그 {len(log_tag_unknown)})",
    ]
    if log_skip_note:
        lines += [log_skip_note]
    else:
        lines += [
            f"  (규약: 요약 앞에 `[{'|'.join(sorted(valid_ops))}]` — grep 가능 데이터 유지. "
            f"도입일 이전 로그는 소급 대상 아님)",
            *([f"- 태그 없음: {x}" for x in log_tag_missing]
              + [f"- 미승인 태그: {x}" for x in log_tag_unknown] or ["- 없음"]),
        ]
    lines += [
        "",
        f"## 10. 행동 계약 rules 무결성 — {rules_dir_rel}/ — 관리성 "
        f"(크기초과 {len(rules_oversized)} / CLAUDE.md 중복 {len(rules_dup)})",
    ]
    if rules_skip_note:
        lines += [rules_skip_note]
    else:
        lines += [
            f"  (rules는 상시 로드되는 엔진 계약 — 얇게 유지({rules_max_lines}줄 이하). "
            f"긴 절차는 스킬/명령으로. CLAUDE.md와 같은 규칙 제목이 겹치면 충돌 시 모델 임의선택 위험)",
            f"### 10a. 크기 초과 ({len(rules_oversized)})",
            *([f"- {f} ({n}줄 — 절차성 장문 유입 의심)" for f, n in rules_oversized] or ["- 없음"]),
            f"### 10b. CLAUDE.md 제목 중복 ({len(rules_dup)})",
            *([f"- {x}" for x in rules_dup] or ["- 없음"]),
        ]
    lines += [
        "",
        f"## 11. 세션 주입 토큰 예산 초과 — 관리성 ({len(injection_over)})",
        "  (SessionStart 훅이 handoff·hot을 **통째로** 주입한다 — 자르지 않는다. "
        "세션마다 들어가므로 커질수록 매번 비용이다. 상세는 원문 노트에 두고 이 둘은 얇게 유지하라. "
        "⚠️단어·글자가 아니라 **토큰 추정** 기준 — 한글 1자 ≈ 1.375토큰이라 글자 예산은 언어에 따라 크게 어긋난다)",
        *([f"- {f} (약 {n:,}토큰 / 예산 {b:,} — {n - b:,} 초과)"
           for f, n, b in injection_over] or ["- 없음"]),
        "",
        f"## 12. 세션 종료 누락 의심 — anchor drift — 관리성 ({1 if drift_summary else 0})",
        "  (handoff의 기준 커밋(anchor)은 session-end만 갱신한다. HEAD가 anchor보다 "
        "임계 이상 앞서면 세션이 닫혔는데 handoff·장기기억이 낡았다는 신호. "
        "활성 세션 중간엔 커밋 누적이 정상이라 exit 코드에는 반영하지 않고 콘솔 요약에만 띄운다)",
        *drift_lines,
        "",
    ]

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        print(f"[vault-healthcheck] 오류: 리포트 쓰기 실패({out}) — {e}", file=sys.stderr)
        return 1

    drift_note = f" ⚠️{drift_summary}" if drift_summary else ""
    print(f"[vault-healthcheck] '{vault_name}' 노트 {len(targets)}개 검사, "
          f"이슈 {total_issues}건(치명 {critical}건) → {out}{drift_note}")
    return 1 if critical > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
