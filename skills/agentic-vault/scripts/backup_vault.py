# -*- coding: utf-8 -*-
"""agentic-vault 로컬 2차 백업 — 볼트 전체 미러 + git 이력 번들 (크로스 플랫폼).

목적: git이 커버하지 않는 바이너리(에셋, .gitignore 제외물)까지 포함한
      볼트 전체의 사고 방어 — 실수 삭제, 파일 손상, 미추적 파일 소실.

동작:
  1. 볼트 감지: --vault 인자 → CLAUDE_PROJECT_DIR → 현재 디렉토리 순.
     00-meta/vault-config.json이 없으면 볼트가 아니므로 정중히 무동작(exit 0).
  2. config의 backup_target(또는 --target 인자)이 비어 있으면 안내 후 exit 0.
  3. 미러: <target>/mirror 로 전체 미러(삭제 동기화 포함).
     - Windows: robocopy /MIR (종료코드 0~7 성공, 8+ 실패)
     - macOS/Linux: rsync -a --delete
     - 둘 다 없으면 표준 라이브러리(shutil) 폴백 미러.
     config의 exclude_dirs(이름 기준, 모든 깊이)와 Thumbs.db·desktop.ini는 제외.
  4. 볼트가 git repo면 <target>/bundles/ 에 `git bundle --all`을 날짜명으로 누적.
  5. <target>/backup-log.txt 에 결과 1줄 기록.

사용법: python backup_vault.py [--vault <볼트경로>] [--target <백업경로>]
표준 라이브러리만 사용. 권장 주기: 세션 종료 시 또는 큰 작업 단위 후.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CONFIG_REL = "00-meta/vault-config.json"
DEFAULT_EXCLUDE_DIRS = [
    "node_modules", ".venv", "venv", "__pycache__", ".git", ".trash", "step_archive",
]
EXCLUDE_FILES = ["Thumbs.db", "desktop.ini"]


def _utf8_stdout() -> None:
    """Windows cp949 콘솔에서도 한국어 출력이 깨지지 않도록 stdout을 UTF-8로 재설정."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def find_vault(cli_vault: str | None) -> Path | None:
    """볼트 루트 결정: --vault → CLAUDE_PROJECT_DIR → cwd. 볼트가 아니면 None."""
    candidates = []
    if cli_vault:
        candidates.append(Path(cli_vault))
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path.cwd())
    for cand in candidates:
        try:
            if (cand / CONFIG_REL).is_file():
                return cand.resolve()
        except OSError:
            continue
    return None


def load_config(vault: Path) -> dict:
    try:
        config = json.loads((vault / CONFIG_REL).read_text(encoding="utf-8-sig"))
        return config if isinstance(config, dict) else {}
    except (OSError, ValueError):
        return {}


def mirror_robocopy(src: Path, dst: Path, exclude_dirs: list[str]) -> tuple[str, int, bool]:
    """Windows robocopy 미러. 반환: (방식, 종료코드, 성공여부)."""
    cmd = [
        "robocopy", str(src), str(dst), "/MIR",
        "/XD", *exclude_dirs,
        "/XF", *EXCLUDE_FILES,
        "/R:1", "/W:1", "/NFL", "/NDL", "/NP",
    ]
    rc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    return ("robocopy", rc, rc < 8)  # robocopy: 0~7 성공 계열, 8+ 실패


def mirror_rsync(src: Path, dst: Path, exclude_dirs: list[str]) -> tuple[str, int, bool]:
    """macOS/Linux rsync 미러. 반환: (방식, 종료코드, 성공여부)."""
    cmd = ["rsync", "-a", "--delete"]
    for name in exclude_dirs + EXCLUDE_FILES:
        cmd.append(f"--exclude={name}")
    cmd += [str(src) + "/", str(dst) + "/"]
    rc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    return ("rsync", rc, rc in (0, 24))  # 24 = 전송 중 파일 소실(경고 취급)


def mirror_shutil(src: Path, dst: Path, exclude_dirs: list[str]) -> tuple[str, int, bool]:
    """표준 라이브러리 폴백 미러: 신규·변경 복사 후 원본에 없는 항목 삭제."""
    excl = set(exclude_dirs)
    excl_files = set(EXCLUDE_FILES)
    errors = 0

    # 1) 복사 단계 — 부재하거나 (mtime·size 기준) 변경된 파일만 복사
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in excl]
        rel = os.path.relpath(root, src)
        troot = dst if rel == "." else dst / rel
        try:
            os.makedirs(troot, exist_ok=True)
        except OSError:
            errors += 1
            continue
        for name in files:
            if name in excl_files:
                continue
            s = Path(root) / name
            t = Path(troot) / name
            try:
                if (not t.exists()
                        or abs(s.stat().st_mtime - t.stat().st_mtime) > 2
                        or s.stat().st_size != t.stat().st_size):
                    shutil.copy2(s, t)
            except OSError:
                errors += 1

    # 2) 삭제 단계 — 원본에 더 이상 없는 파일·디렉토리 제거(제외 이름은 건드리지 않음)
    for root, dirs, files in os.walk(dst, topdown=False):
        rel = os.path.relpath(root, dst)
        parts = [] if rel == "." else rel.split(os.sep)
        if any(p in excl for p in parts):
            continue
        sroot = src if rel == "." else src / rel
        for name in files:
            if name in excl_files:
                continue
            if not (sroot / name).exists():
                try:
                    os.remove(Path(root) / name)
                except OSError:
                    errors += 1
        for name in dirs:
            if name in excl:
                continue
            dpath = Path(root) / name
            if not (sroot / name).exists() and dpath.is_dir():
                try:
                    shutil.rmtree(dpath)
                except OSError:
                    errors += 1

    return ("shutil", errors, errors == 0)


def make_bundle(vault: Path, bundles_dir: Path) -> str:
    """볼트가 git repo면 전체 이력 번들 생성. 로그용 결과 문자열 반환."""
    if not (vault / ".git").exists():
        return "skipped(not-a-git-repo)"
    if shutil.which("git") is None:
        return "skipped(git-not-found)"
    try:
        bundles_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return "skipped(mkdir-failed)"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", vault.name).strip("-") or "vault"
    bundle_file = bundles_dir / f"{stem}-{datetime.now():%Y%m%d-%H%M}.bundle"
    rc = subprocess.run(
        ["git", "-C", str(vault), "bundle", "create", str(bundle_file), "--all"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode
    return bundle_file.name if rc == 0 else f"failed(rc={rc})"


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="agentic-vault 로컬 2차 백업 (미러 + git 번들)")
    parser.add_argument("--vault", help="볼트 루트 경로 (기본: CLAUDE_PROJECT_DIR → 현재 디렉토리)")
    parser.add_argument("--target", help="백업 대상 경로 (기본: vault-config.json의 backup_target)")
    args = parser.parse_args()

    vault = find_vault(args.vault)
    if vault is None:
        print("[agentic-vault] 볼트(00-meta/vault-config.json)가 아니므로 백업을 건너뜁니다.")
        return 0

    config = load_config(vault)
    target_raw = (args.target or config.get("backup_target", "") or "").strip()
    if not target_raw:
        print("[agentic-vault] backup_target이 설정되지 않아 백업을 건너뜁니다.")
        print("  → 00-meta/vault-config.json의 \"backup_target\"에 백업 경로를 지정하거나 --target 인자를 사용하세요.")
        return 0

    target = Path(target_raw)
    if not target.is_absolute():
        target = (vault / target).resolve()
    # 안전장치: 백업 대상이 볼트 내부면 자기 자신을 재귀 복사하게 되므로 거부
    try:
        target.relative_to(vault)
        print(f"[agentic-vault] backup_target({target})이 볼트 내부입니다. 볼트 밖 경로를 지정하세요.")
        return 1
    except ValueError:
        pass

    exclude_dirs = config.get("exclude_dirs") or DEFAULT_EXCLUDE_DIRS
    if not isinstance(exclude_dirs, list):
        exclude_dirs = DEFAULT_EXCLUDE_DIRS
    exclude_dirs = [str(d) for d in exclude_dirs]

    mirror_dir = target / "mirror"
    bundles_dir = target / "bundles"
    try:
        mirror_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[agentic-vault] 백업 대상 생성 실패: {target} ({exc})")
        return 1

    # 1) 전체 미러
    if os.name == "nt" and shutil.which("robocopy"):
        method, rc, ok = mirror_robocopy(vault, mirror_dir, exclude_dirs)
    elif shutil.which("rsync"):
        method, rc, ok = mirror_rsync(vault, mirror_dir, exclude_dirs)
    else:
        method, rc, ok = mirror_shutil(vault, mirror_dir, exclude_dirs)

    # 2) git 전체 이력 번들 — 날짜명 누적(단일 파일 복원용)
    bundle_result = make_bundle(vault, bundles_dir)

    # 3) 백업 로그
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} | mirror={method} rc={rc} | bundle={bundle_result}"
    try:
        with open(target / "backup-log.txt", "a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except OSError:
        pass
    print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
