"""Independent local snapshots, SHA-256 verification, and new-directory restore.

Create:  backup_vault.py [--vault VAULT] [--target BACKUP_ROOT]
Verify:  backup_vault.py --verify SNAPSHOT
Restore: backup_vault.py --restore SNAPSHOT --destination NEW_DIRECTORY

Completed snapshots are never updated or pruned. Failed private staging directories
are retained for inspection; a process killed while holding .backup.lock requires
manual lock removal after confirming that no backup writer is running. Restore
exports working files; history.bundle remains in the snapshot for `git clone`.
Use a quiescent vault: this is a checked file copy, not a filesystem point-in-time
snapshot. Hashes detect accidental corruption, not malicious manifest replacement.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid


CONFIG_REL = "00-meta/vault-config.json"
DEFAULT_EXCLUDE_DIRS = [
    "node_modules", ".venv", "venv", "__pycache__", ".git", ".trash", "step_archive",
]
EXCLUDE_FILES = ["Thumbs.db", "desktop.ini"]
CHUNK_SIZE = 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class BackupError(ValueError):
    """Unsafe input, changed files, invalid snapshot, or failed Git operation."""


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


def _safe_path(raw: Path) -> Path:
    """Check lexical components before resolving so junctions cannot disappear."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if _is_link(info):
            raise BackupError(f"symlink or reparse point is not supported: {current}")
    return path.resolve()


def _nonoverlap(first: Path, second: Path):
    if first.is_relative_to(second) or second.is_relative_to(first):
        raise BackupError("source and destination paths overlap")


def _relative_name(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise BackupError("invalid relative snapshot path")
    for part in value.split("/"):
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise BackupError("invalid relative snapshot path")
    return value


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupError("duplicate JSON key")
        result[key] = value
    return result


def _read_json(path, limit):
    path = _safe_path(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise BackupError("expected a regular JSON file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise BackupError("JSON file exceeds size limit")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_json_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BackupError("invalid JSON file") from exc


def load_config(vault: Path) -> dict:
    config = _read_json(vault / CONFIG_REL, 1024 * 1024)
    if not isinstance(config, dict):
        raise BackupError("vault configuration must be an object")
    if "backup_target" in config and not isinstance(config["backup_target"], str):
        raise BackupError("backup_target must be a string")
    excluded = config.get("exclude_dirs", DEFAULT_EXCLUDE_DIRS)
    if not isinstance(excluded, list):
        raise BackupError("exclude_dirs must be a list of directory names")
    for name in excluded:
        _relative_name(name)
        if "/" in name:
            raise BackupError("exclude_dirs entries must be directory names")
    return config


def find_vault(cli_vault: str | None) -> Path | None:
    candidates = ([cli_vault] if cli_vault else
                  [os.environ.get("CLAUDE_PROJECT_DIR", ""), str(Path.cwd())])
    for candidate in candidates:
        if candidate:
            vault = _safe_path(Path(candidate))
            if (vault / CONFIG_REL).exists():
                return vault
    return None


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)


def _inventory(root: Path, excluded=(), exclude_files=()):
    """Scan even excluded directories for unsafe links; never follow a link."""
    files, directories = {}, []
    root = _safe_path(root)
    if not root.is_dir():
        raise BackupError("expected a directory")
    pending = [(root, False)]
    while pending:
        directory, omitted = pending.pop()
        _safe_path(directory)
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = directory / entry.name
                info = entry.stat(follow_symlinks=False)
                if _is_link(info):
                    raise BackupError(f"symlink or reparse point is not supported: {path}")
                rel = path.relative_to(root).as_posix()
                if stat.S_ISDIR(info.st_mode):
                    skip = omitted or entry.name in excluded
                    pending.append((path, skip))
                    if not skip:
                        directories.append(_relative_name(rel))
                elif stat.S_ISREG(info.st_mode):
                    if not omitted and entry.name not in exclude_files:
                        files[_relative_name(rel)] = _fingerprint(info)
                else:
                    raise BackupError(f"unsupported non-regular file: {path}")
    return files, sorted(directories)


def _digest(path: Path):
    path = _safe_path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise BackupError("expected a regular file")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        if _fingerprint(os.fstat(stream.fileno())) != _fingerprint(before):
            raise BackupError("file changed before reading")
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(block)
            size += len(block)
    if _fingerprint(_safe_path(path).stat()) != _fingerprint(before):
        raise BackupError("file changed while reading")
    return {"size": size, "sha256": digest.hexdigest()}


def _copy_file(source: Path, destination: Path):
    source = _safe_path(source)
    destination = _safe_path(destination)
    before = source.stat()
    if not stat.S_ISREG(before.st_mode):
        raise BackupError("expected a regular source file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    with source.open("rb") as src, destination.open("xb") as dst:
        if _fingerprint(os.fstat(src.fileno())) != _fingerprint(before):
            raise BackupError("source changed before copying")
        for block in iter(lambda: src.read(CHUNK_SIZE), b""):
            dst.write(block)
            digest.update(block)
            size += len(block)
        dst.flush()
        os.fsync(dst.fileno())
    if _fingerprint(_safe_path(source).stat()) != _fingerprint(before):
        raise BackupError("source changed while copying")
    shutil.copystat(source, destination, follow_symlinks=False)
    return {"size": size, "sha256": digest.hexdigest()}


@contextmanager
def _writer_lock(target):
    lock = target / ".backup.lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BackupError("backup writer lock exists; confirm no writer is running before manual removal") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(f"pid={os.getpid()}\n")
        yield
    finally:
        lock.unlink()


def _make_bundle(vault, stage):
    git_dir = vault / ".git"
    if not git_dir.exists():
        return None
    if not git_dir.is_dir():
        raise BackupError("Git worktree/submodule .git files are unsupported for history backup")
    git = shutil.which("git")
    if git is None:
        raise BackupError("Git is required to back up this repository's history")
    result = subprocess.run(
        [git, "-C", str(vault), "bundle", "create", str(stage / "history.bundle"), "--all"],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise BackupError(f"Git history bundle failed (exit {result.returncode})")
    return _digest(stage / "history.bundle")


def _record(value):
    if (not isinstance(value, dict) or set(value) != {"size", "sha256"}
            or type(value["size"]) is not int or value["size"] < 0
            or not isinstance(value["sha256"], str)
            or not re.fullmatch("[0-9a-f]{64}", value["sha256"])):
        raise BackupError("invalid manifest file record")


def verify_snapshot(snapshot: Path) -> dict:
    """Return a validated manifest; raise before accepting any corrupt snapshot."""
    snapshot = _safe_path(snapshot)
    manifest = _read_json(snapshot / "manifest.json", MAX_MANIFEST_BYTES)
    if (not isinstance(manifest, dict)
            or set(manifest) != {"version", "created_utc", "files", "directories", "git_bundle"}
            or type(manifest["version"]) is not int or manifest["version"] != 1
            or not isinstance(manifest["created_utc"], str)
            or not isinstance(manifest["files"], dict)
            or not isinstance(manifest["directories"], list)):
        raise BackupError("unsupported or malformed snapshot manifest")
    names = list(manifest["files"]) + manifest["directories"]
    seen = set()
    for name in names:
        _relative_name(name)
        if name.casefold() in seen:
            raise BackupError("duplicate or case-colliding manifest paths")
        seen.add(name.casefold())
    directory_set = set(manifest["directories"])
    for name in names:
        parent = Path(name).parent
        while parent != Path("."):
            if parent.as_posix() not in directory_set:
                raise BackupError("manifest omits a parent directory")
            parent = parent.parent
    expected_top = {"files", "manifest.json"}
    if manifest["git_bundle"] is not None:
        _record(manifest["git_bundle"])
        expected_top.add("history.bundle")
    if {item.name for item in snapshot.iterdir()} != expected_top:
        raise BackupError("unexpected or missing snapshot contents")
    files, directories = _inventory(snapshot / "files")
    if set(files) != set(manifest["files"]) or directories != sorted(manifest["directories"]):
        raise BackupError("snapshot inventory does not match manifest")
    for name, record in manifest["files"].items():
        _record(record)
        if _digest(snapshot / "files" / name) != record:
            raise BackupError(f"snapshot file checksum mismatch: {name}")
    if manifest["git_bundle"] is not None:
        if _digest(snapshot / "history.bundle") != manifest["git_bundle"]:
            raise BackupError("Git bundle checksum mismatch")
    return manifest


def create_backup(vault: Path, target: Path) -> Path:
    """Publish one verified snapshot with an exclusive per-target writer lock."""
    vault = _safe_path(vault)
    target = _safe_path(target if Path(target).is_absolute() else vault / target)
    _nonoverlap(vault, target)
    config = load_config(vault)
    excluded = set(config.get("exclude_dirs", DEFAULT_EXCLUDE_DIRS)) | {".git"}
    target.mkdir(parents=True, exist_ok=True)
    with _writer_lock(target):
        snapshots = _safe_path(target / "snapshots")
        snapshots.mkdir(exist_ok=True)
        identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
        stage = target / (".staging-" + identifier)
        stage.mkdir(mode=0o700)
        (stage / "files").mkdir()
        before, directories = _inventory(vault, excluded, EXCLUDE_FILES)
        manifest = {"version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                    "files": {}, "directories": directories, "git_bundle": None}
        for name in directories:
            (stage / "files" / name).mkdir(parents=True, exist_ok=True)
        for name in sorted(before):
            source = vault / name
            manifest["files"][name] = _copy_file(source, stage / "files" / name)
            if _digest(source) != manifest["files"][name]:
                raise BackupError("source changed during backup")
        manifest["git_bundle"] = _make_bundle(vault, stage)
        if _inventory(vault, excluded, EXCLUDE_FILES) != (before, directories):
            raise BackupError("vault changed during backup; retry with a quiescent vault")
        with (stage / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        verify_snapshot(stage)
        published = snapshots / identifier
        if published.exists():
            raise BackupError("snapshot identifier collision")
        stage.rename(published)
        return published


def restore_snapshot(snapshot: Path, destination: Path) -> Path:
    """Validate completely before creating an exclusive new destination directory.

    I/O failure during export leaves the new directory partially populated for
    inspection. Existing directories are never merged with or overwritten.
    """
    snapshot, destination = _safe_path(snapshot), _safe_path(destination)
    _nonoverlap(snapshot, destination)
    if destination.exists():
        raise BackupError("restore destination must be a new directory")
    manifest = verify_snapshot(snapshot)
    destination.mkdir(parents=True, exist_ok=False)
    for name in manifest["directories"]:
        (destination / name).mkdir(parents=True, exist_ok=True)
    for name, record in manifest["files"].items():
        if _copy_file(snapshot / "files" / name, destination / name) != record:
            raise BackupError("snapshot changed during restore")
        if _digest(destination / name) != record:
            raise BackupError("restored file checksum mismatch")
    return destination


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", help="vault root (default: CLAUDE_PROJECT_DIR or cwd)")
    parser.add_argument("--target", help="backup root (default: config backup_target)")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--verify", type=Path, metavar="SNAPSHOT")
    operations.add_argument("--restore", type=Path, metavar="SNAPSHOT")
    parser.add_argument("--destination", type=Path, help="new directory for restored working files")
    args = parser.parse_args()
    if (args.verify or args.restore) and (args.vault or args.target):
        parser.error("--vault and --target apply only to snapshot creation")
    if bool(args.restore) != bool(args.destination):
        parser.error("--restore and --destination must be used together")
    try:
        if args.verify:
            verify_snapshot(args.verify)
            print(f"Verified: {args.verify}")
        elif args.restore:
            print(f"Restored: {restore_snapshot(args.restore, args.destination)}")
        else:
            vault = find_vault(args.vault)
            if vault is None:
                if args.vault:
                    raise BackupError("explicit --vault has no vault configuration")
                return 0
            config = load_config(vault)
            target = (args.target or config.get("backup_target", "")).strip()
            if not target:
                print("[agentic-vault] No backup_target configured; backup skipped.")
                return 0
            print(f"Snapshot: {create_backup(vault, Path(target))}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"[agentic-vault] Backup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
