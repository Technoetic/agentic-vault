#!/usr/bin/env python3
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
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

# Windows 콘솔(cp949 등)에서 한국어 출력이 깨지지 않도록 UTF-8 재설정 (실패해도 무해)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

CONFIG_RELPATH = "00-meta/vault-config.json"

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
}

WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:[#|][^\]]*)?\]\]")
# 프런트매터에서 따옴표 없이 시작하는 위키링크 값 (YAML 중첩 배열로 오파싱 → 붕괴)
UNQUOTED_FM_LINK_RE = re.compile(r"^\s*(?:[\w_]+:|-)\s*\[\[")
# 코드펜스/인라인 코드 내부의 위키링크·사실 값은 렌더링/표기 대상이 아니므로 제외
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
# 로그 엄격 형식: '- YYYY-MM-DD HH:MM | 행위자 | 본문'
LOG_ENTRY_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2})\s+[\d:]+\s*\|[^|]*\|\s*(.*)$")
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


def main() -> int:
    ap = argparse.ArgumentParser(description="볼트 무결성 검증 엔진 (agentic-vault)")
    ap.add_argument("--vault",
                    default=os.environ.get("CLAUDE_PROJECT_DIR") or ".",
                    help="볼트 루트 경로 (기본: $CLAUDE_PROJECT_DIR, 없으면 cwd)")
    ap.add_argument("--output", default=None,
                    help="리포트 출력 경로 (기본: config의 health_report)")
    args = ap.parse_args()

    vault = Path(args.vault).resolve()

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
        if not isinstance(user_cfg, dict):
            raise ValueError("최상위가 JSON 객체가 아님")
    except (OSError, ValueError) as e:
        print(f"[vault-healthcheck] 오류: {CONFIG_RELPATH} 파싱 실패 — {e}", file=sys.stderr)
        return 1
    cfg = {**DEFAULT_CONFIG, **user_cfg}
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

    for p in targets:
        rel = rel_posix(p, vault)
        text = read_text(p)
        fm, _body = split_frontmatter(text)

        if fm is None:
            missing_fm.append(rel)
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

    # --- 집계 ------------------------------------------------------------------
    total_issues = (len(missing_fm) + len(missing_keys) + len(enum_violations)
                    + len(unquoted_links) + len(oversized_fm)
                    + len(dead_links) + len(orphans) + len(semi_orphans)
                    + len(unindexed) + len(stale) + len(fact_conflicts)
                    + len(log_tag_missing) + len(log_tag_unknown))
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
    lines += [""]

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        print(f"[vault-healthcheck] 오류: 리포트 쓰기 실패({out}) — {e}", file=sys.stderr)
        return 1

    print(f"[vault-healthcheck] '{vault_name}' 노트 {len(targets)}개 검사, "
          f"이슈 {total_issues}건(치명 {critical}건) → {out}")
    return 1 if critical > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
