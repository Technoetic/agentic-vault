#!/usr/bin/env python3
"""Run all unittest modules in tests/ in parallel worker subprocesses (stdlib-only).

Why this exists:
    A single-process ``python -m unittest discover -s tests`` run grew past
    55 minutes on ``windows-latest / Python 3.10`` (approaching the 60-minute
    job limit). Running each ``tests/test_*.py`` module in its own isolated
    Python subprocess across a bounded worker pool preserves unittest semantics
    and fail-closed exit codes while keeping CI runs well inside the timeout.

    Running modules as ``python -m unittest tests.<module>`` also ensures the
    ``tests`` package (including Windows extended-length cleanup in
    ``tests/__init__.py``) is always imported in every worker process.

Usage:
    python scripts/run_unittest_parallel.py [--workers N] [--list] [--modules M ...]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
ENV_GUARD_MODULE = "test_000_environment"
RAN_RE = re.compile(r"^Ran (\d+) tests? in ([0-9.]+)s$", re.MULTILINE)
SKIPPED_RE = re.compile(r"\bskipped=(\d+)\b")
DEFAULT_WORKERS = 4


def discover_modules(tests_dir: Path = TESTS_DIR) -> list[str]:
    """Return all tests/test_*.py module stems in deterministic order."""
    return sorted(
        path.stem
        for path in tests_dir.glob("test_*.py")
        if path.is_file()
    )


def schedule_order(modules: list[str], tests_dir: Path = TESTS_DIR) -> list[str]:
    """Place heavier modules first so long subprocess-heavy suites start at t=0."""
    def sort_key(stem: str) -> tuple[int, str]:
        path = tests_dir / f"{stem}.py"
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return (-size, stem)

    return sorted(modules, key=sort_key)


def _parse_counts(stderr: str) -> tuple[int, int, bool]:
    ran_matches = RAN_RE.findall(stderr)
    if not ran_matches:
        return 0, 0, False
    ran = int(ran_matches[-1][0])
    tail = stderr[stderr.rfind("Ran "):]
    skip_match = SKIPPED_RE.search(tail)
    skipped = int(skip_match.group(1)) if skip_match else 0
    return ran, skipped, True


def run_module(
    module: str,
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> dict[str, object]:
    """Execute one ``tests.<module>`` in a clean child interpreter."""
    started = time.monotonic()
    flags = subprocess._args_from_interpreter_flags()
    cmd = [sys.executable, *flags, "-m", "unittest", f"tests.{module}"]
    child_env = dict(os.environ if env is None else env)
    child_env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=child_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.monotonic() - started, 2)
        return {
            "module": module,
            "returncode": 124,
            "ran": 0,
            "skipped": 0,
            "parsed": False,
            "elapsed_s": elapsed,
            "stdout": "",
            "stderr": f"TIMEOUT after {timeout}s: {exc}",
            "ok": False,
        }
    elapsed = round(time.monotonic() - started, 2)
    ran, skipped, parsed = _parse_counts(proc.stderr)
    ok = proc.returncode == 0 and parsed
    return {
        "module": module,
        "returncode": proc.returncode,
        "ran": ran,
        "skipped": skipped,
        "parsed": parsed,
        "elapsed_s": elapsed,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "ok": ok,
    }


def _print_result_line(res: dict[str, object]) -> None:
    status = "OK" if res["ok"] else f"FAIL(rc={res['returncode']})"
    skip_note = f", skipped={res['skipped']}" if res["skipped"] else ""
    print(
        f"[{status}] tests.{res['module']}: {res['ran']} tests{skip_note} ({res['elapsed_s']:.2f}s)",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run agentic-vault unittest modules in parallel worker subprocesses."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        metavar="N",
        help=f"maximum parallel worker subprocesses (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="optional per-module timeout in seconds",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print discovered test modules and exit without running them",
    )
    parser.add_argument(
        "--modules",
        nargs="+",
        metavar="MODULE",
        help="explicit module stems (without 'tests.' prefix) to run instead of all discovered modules",
    )
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.timeout is not None and args.timeout < 1:
        parser.error("--timeout must be >= 1")

    discovered = discover_modules()
    if args.modules is not None:
        unknown = [m for m in args.modules if m not in discovered]
        if unknown:
            print(f"run_unittest_parallel: unknown test module(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        selected = list(dict.fromkeys(args.modules))
    else:
        selected = discovered

    if args.list:
        for mod in selected:
            print(mod)
        return 0

    if not selected:
        print("run_unittest_parallel: no test modules found", file=sys.stderr)
        return 1

    wall_start = time.monotonic()
    results: list[dict[str, object]] = []

    # Run the environment/long-TEMP guard first so a misconfigured run fails immediately.
    remaining = list(selected)
    if ENV_GUARD_MODULE in remaining:
        remaining.remove(ENV_GUARD_MODULE)
        guard_res = run_module(ENV_GUARD_MODULE, timeout=args.timeout)
        results.append(guard_res)
        _print_result_line(guard_res)
        if not guard_res["ok"]:
            if guard_res["stdout"]:
                print(guard_res["stdout"], file=sys.stderr)
            if guard_res["stderr"]:
                print(guard_res["stderr"], file=sys.stderr)
            print(
                f"FAILED: guard module tests.{ENV_GUARD_MODULE} failed (rc={guard_res['returncode']})",
                file=sys.stderr,
            )
            return 1

    ordered = schedule_order(remaining)
    worker_count = min(args.workers, max(1, len(ordered)))
    if ordered:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
            future_map = {
                pool.submit(run_module, mod, timeout=args.timeout): mod
                for mod in ordered
            }
            for future in concurrent.futures.as_completed(future_map):
                res = future.result()
                results.append(res)
                _print_result_line(res)

    wall_elapsed = time.monotonic() - wall_start
    total_tests = sum(int(r["ran"]) for r in results)
    total_skipped = sum(int(r["skipped"]) for r in results)
    failed = sorted(
        (r for r in results if not r["ok"]),
        key=lambda r: str(r["module"]),
    )

    if failed:
        print("\n=== FAILED MODULES ===", file=sys.stderr)
        for res in failed:
            print(
                f"\n--- tests.{res['module']} (rc={res['returncode']}, parsed={res['parsed']}) ---",
                file=sys.stderr,
            )
            if res["stdout"]:
                print(res["stdout"], file=sys.stderr)
            if res["stderr"]:
                print(res["stderr"], file=sys.stderr)
        print(
            f"\nRan {total_tests} tests across {len(results)} modules in {wall_elapsed:.2f}s (workers={worker_count})",
            file=sys.stderr,
        )
        print(
            f"FAILED (failed_modules={len(failed)}, skipped={total_skipped})",
            file=sys.stderr,
        )
        return 1

    skip_part = f" (skipped={total_skipped})" if total_skipped else ""
    print(
        f"\nRan {total_tests} tests across {len(results)} modules in {wall_elapsed:.2f}s (workers={worker_count})",
        flush=True,
    )
    print(f"OK{skip_part}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
