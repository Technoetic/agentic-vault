#!/usr/bin/env python3
"""agentic-vault Jarvis bridge — Telegram 단일 채널 상시 데몬 (stdlib-only).

역할: 롱폴링 수신 → 화이트리스트 필터 → 라우팅(캡처/브리핑/상태/Q&A) → 스케줄(브리핑·집사).
쓰기 경로는 10-inbox/jarvis/ 결정론적 저장뿐이며, LLM 호출은 전부 읽기 전용
`claude -p --allowedTools Read Grep Glob` 세션이다.

사용:
  python jarvis_bridge.py --vault D:/NS            # 상시 실행
  python jarvis_bridge.py --vault D:/NS --self-test # 네트워크 없는 자체 검증

비밀: 봇 토큰은 env JARVIS_TELEGRAM_TOKEN 로만 전달한다. 볼트·리포에 저장 금지.
설정: <vault>/00-meta/vault-config.json 의 "jarvis" 블록. 없거나 enabled=false면 침묵 종료.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, date
from pathlib import Path

API_BASE = "https://api.telegram.org/bot{token}/{method}"
STATE_DIR = Path.home() / ".vault-jarvis"
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


# ---------------------------------------------------------------- 로그

def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        logfile = STATE_DIR / "jarvis.log"
        if logfile.exists() and logfile.stat().st_size > LOG_MAX_BYTES:
            logfile.replace(STATE_DIR / "jarvis.log.1")
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 로그 실패가 브리지를 죽여선 안 된다


# ---------------------------------------------------------------- 설정

def load_jarvis_config(vault: Path) -> dict | None:
    """볼트가 아니거나 jarvis 블록이 없거나 enabled=false면 None."""
    cfg_path = vault / "00-meta" / "vault-config.json"
    if not cfg_path.is_file():
        return None
    try:
        vault_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    block = vault_cfg.get("jarvis")
    if not isinstance(block, dict) or not block.get("enabled"):
        return None
    cfg = {**DEFAULTS, **block}
    cfg["telegram_user_ids"] = [int(x) for x in cfg.get("telegram_user_ids", [])]
    # Q&A 가드에 필요한 볼트 수준 키를 함께 전달
    cfg["_deny_zones"] = vault_cfg.get("deny_zones", [])
    cfg["_language"] = vault_cfg.get("language", "ko")
    cfg["_hot_note"] = vault_cfg.get("hot_note", "00-meta/hot.md")
    cfg["_handoff_note"] = vault_cfg.get("handoff_note", "")
    cfg["_log_note"] = vault_cfg.get("log_note", "00-meta/log.md")
    cfg["_health_report"] = vault_cfg.get("health_report", "00-meta/health-report.md")
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

def do_capture(vault: Path, body: str, source: str) -> str:
    inbox = vault / "10-inbox" / "jarvis"
    inbox.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    name = now.strftime("%Y-%m-%d %H%M%S") + ".md"
    content = f"{body}\n\n---\n수신: {now.strftime('%Y-%m-%d %H:%M:%S')} · 채널: {source}\n"
    (inbox / name).write_text(content, encoding="utf-8")
    return name


def run_claude(vault: Path, cfg: dict, prompt: str) -> str:
    """읽기 전용 claude -p 세션. 실패·타임아웃 시 사람이 읽을 안내문 반환."""
    exe = shutil.which(cfg["claude_cmd"])
    if not exe:
        return "⚠️ claude CLI를 찾을 수 없습니다. PATH를 확인하세요."
    deny = ", ".join(cfg["_deny_zones"]) or "(없음)"
    guard = (
        "너는 이 옵시디언 볼트의 개인 비서다. 규칙: "
        f"(1) 탐색 순서 {cfg['_hot_note']} → 00-meta/index.md → Grep. "
        f"(2) 다음 경로는 절대 읽지 마라: {deny}, **/.env, 90-assets/. "
        f"(3) 볼트 내용만 근거로 {cfg['_language']} 언어로 간결히 답하고 근거 노트명을 인용하라. "
        "(4) 볼트에 근거가 없으면 없다고 답하라. 파일 생성·수정·삭제는 절대 하지 마라."
    )
    cmd = [exe, "-p", prompt,
           "--allowedTools", "Read", "Grep", "Glob",
           "--append-system-prompt", guard]
    try:
        r = subprocess.run(cmd, cwd=str(vault), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=cfg["qa_timeout_sec"])
    except subprocess.TimeoutExpired:
        return "⏱️ 응답 생성이 시간 초과됐습니다. 질문을 좁혀 다시 시도해 주세요."
    if r.returncode != 0:
        log(f"claude 실패 rc={r.returncode}: {r.stderr[:300]}")
        return "⚠️ 응답 생성에 실패했습니다. 로그를 확인하세요."
    return r.stdout.strip() or "(빈 응답)"


def do_qa(vault: Path, cfg: dict, question: str) -> str:
    return run_claude(vault, cfg, question)


def do_brief(vault: Path, cfg: dict) -> str:
    git_lines = _git(vault, "log", "--oneline", "--date=short", "-10") or "(git 이력 없음)"
    parts = [f"오늘({date.today().isoformat()}) 아침 브리핑을 만들어라.",
             f"다음 노트를 읽어라: {cfg['_hot_note']}"]
    if cfg["_handoff_note"]:
        parts.append(f"그리고 {cfg['_handoff_note']} (직전 세션 인계·NEXT 섹션 주목)")
    parts.append(f"그리고 {cfg['_log_note']} 최상단 10줄.")
    parts.append("아래는 브리지가 수집한 최근 git 활동이다(참고용 — 직접 git 실행 불가):\n" + git_lines)
    parts.append("형식: ① 지금 상태(2줄) ② 최우선 미결(최대 3개) ③ 오늘의 제안(1개). 전체 12줄 이내.")
    return run_claude(vault, cfg, "\n".join(parts))


def do_status(vault: Path, started: float) -> str:
    head = _git(vault, "rev-parse", "--short", "HEAD") or "(git 없음)"
    inbox = vault / "10-inbox"
    pending = sum(1 for p in inbox.rglob("*.md") if "_processed" not in p.parts) if inbox.is_dir() else 0
    up_h = (time.time() - started) / 3600
    return (f"🤖 상태\n· HEAD: {head}\n· 인박스 대기: {pending}건\n"
            f"· 브리지 가동: {up_h:.1f}시간")


def do_butler(vault: Path, cfg: dict) -> str:
    lines = ["🧹 집사 보고"]
    hc = Path(__file__).parent / "vault_healthcheck.py"
    if hc.is_file():
        r = subprocess.run([sys.executable, str(hc), "--vault", str(vault),
                            "--output", cfg["_health_report"]],
                           cwd=str(vault), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
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
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------- Telegram

def tg_call(token: str, method: str, http_timeout: int = 65, **params) -> dict:
    """params는 그대로 Telegram API로 간다 — urllib 타임아웃과 이름 충돌 금지."""
    url = API_BASE.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=http_timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def md_to_telegram_html(text: str) -> str:
    """claude 출력 마크다운을 Telegram HTML로 결정론 변환.
    지원: 헤더(#..)→굵게, **굵게**, `코드`, [[위키링크]]→기울임. 나머지는 이스케이프."""
    import re
    out_lines = []
    for line in text.splitlines():
        esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
        esc = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", esc)
        esc = re.sub(r"\[\[([^\]]+)\]\]", r"<i>\1</i>", esc)
        m = re.match(r"^(#{1,6})\s+(.*)$", esc)
        if m:
            esc = f"<b>{m.group(2)}</b>"
        out_lines.append(esc)
    return "\n".join(out_lines)


def _chunks_by_line(text: str, limit: int = 3500) -> list[str]:
    """태그가 조각나지 않도록 줄 단위로 분할."""
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > limit and cur:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    return chunks or [""]


def tg_send(token: str, chat_id: int, text: str) -> None:
    for chunk in _chunks_by_line(text):
        html = md_to_telegram_html(chunk)
        try:
            tg_call(token, "sendMessage", http_timeout=30, chat_id=chat_id,
                    text=html, parse_mode="HTML")
        except urllib.error.HTTPError as e:
            log(f"HTML 전송 실패({e.code}) — 플레인 폴백")
            try:
                tg_call(token, "sendMessage", http_timeout=30, chat_id=chat_id,
                        text=chunk)
            except (urllib.error.URLError, OSError) as e2:
                log(f"sendMessage 실패: {e2}")
                return
        except (urllib.error.URLError, OSError) as e:
            log(f"sendMessage 실패: {e}")
            return


# ---------------------------------------------------------------- 메인 루프

def serve(vault: Path, cfg: dict) -> None:
    token = os.environ.get("JARVIS_TELEGRAM_TOKEN", "")
    if not token:
        log("JARVIS_TELEGRAM_TOKEN 미설정 — 종료")
        sys.exit(1)
    whitelist = set(cfg["telegram_user_ids"])
    if not whitelist:
        log("⚠️ 화이트리스트가 비어 있음 — 모든 메시지를 폐기하며, 발신자 ID만 콘솔에 표시합니다.")
    started = time.time()
    qa_times: deque[float] = deque()
    offset_file = STATE_DIR / "offset"
    offset = int(offset_file.read_text()) if offset_file.is_file() else 0
    last_butler = 0.0
    brief_h, brief_m = map(int, cfg["briefing_time"].split(":"))
    # 기동 시각이 이미 브리핑 시각을 지났으면 오늘분은 발송하지 않는다(재기동 폭주 방지)
    _n = datetime.now()
    last_brief: date | None = _n.date() if (_n.hour, _n.minute) >= (brief_h, brief_m) else None
    log(f"jarvis 브리지 시작 — vault={vault}, whitelist={sorted(whitelist) or '(비어있음)'}")

    while True:
        # --- 스케줄 ---
        now = datetime.now()
        if whitelist:
            first = sorted(whitelist)[0]
            if last_brief != now.date() and (now.hour, now.minute) >= (brief_h, brief_m):
                last_brief = now.date()
                log("스케줄 브리핑 생성")
                tg_send(token, first, "🌅 아침 브리핑\n" + do_brief(vault, cfg))
            if time.time() - last_butler > cfg["butler_interval_hours"] * 3600:
                last_butler = time.time()
                tg_send(token, first, do_butler(vault, cfg))
        # --- 수신 ---
        try:
            resp = tg_call(token, "getUpdates", http_timeout=65, offset=offset + 1,
                           timeout=50, allowed_updates='["message"]')
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            log(f"getUpdates 오류(재시도): {e}")
            time.sleep(5)
            continue
        for upd in resp.get("result", []):
            offset = max(offset, upd["update_id"])
            offset_file.parent.mkdir(parents=True, exist_ok=True)
            offset_file.write_text(str(offset))
            msg = upd.get("message") or {}
            text = msg.get("text", "")
            frm = (msg.get("from") or {}).get("id")
            chat = (msg.get("chat") or {}).get("id")
            if not text or frm is None or chat is None:
                continue
            if frm not in whitelist:
                log(f"미등재 발신자 폐기: from={frm}")
                continue
            kind, body = route(text)
            log(f"수신[{kind}] from={frm}: {text[:80]}")
            if kind == "capture":
                name = do_capture(vault, body, "telegram")
                tg_send(token, chat, f"📝 적어뒀습니다 → 10-inbox/jarvis/{name}")
            elif kind == "status":
                tg_send(token, chat, do_status(vault, started))
            elif kind == "brief":
                tg_send(token, chat, "🌅 브리핑\n" + do_brief(vault, cfg))
            else:  # qa
                cutoff = time.time() - 3600
                while qa_times and qa_times[0] < cutoff:
                    qa_times.popleft()
                if len(qa_times) >= cfg["qa_hourly_limit"]:
                    tg_send(token, chat, f"⏳ 시간당 질의 한도({cfg['qa_hourly_limit']}회) 도달 — 잠시 후 다시.")
                    continue
                qa_times.append(time.time())
                tg_send(token, chat, do_qa(vault, cfg, body))


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
        # ⑤ 환경 (실패 아닌 경고)
        check("env: claude CLI 탐지", shutil.which(DEFAULTS["claude_cmd"]) is not None, warn=True)
        check("env: JARVIS_TELEGRAM_TOKEN 설정", bool(os.environ.get("JARVIS_TELEGRAM_TOKEN")), warn=True)
        # ⑤ 로그 쓰기
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            probe = STATE_DIR / ".write-probe"
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
    vault = Path(args.vault).resolve()
    cfg = load_jarvis_config(vault)
    if cfg is None:
        log(f"jarvis 비활성(볼트 아님 / jarvis 블록 없음 / enabled=false): {vault} — 침묵 종료")
        sys.exit(0)
    serve(vault, cfg)


if __name__ == "__main__":
    main()
