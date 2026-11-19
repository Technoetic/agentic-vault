"""Create the disposable shelf-label project used in docs/first-handoff.md."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


def create_demo_vault(target: Path | None = None) -> Path:
    """Populate a fresh shelf-label demo vault and return its resolved path."""
    if target is None:
        vault = Path(tempfile.mkdtemp(prefix="av-first-handoff-")).resolve()
    else:
        vault = target.expanduser().resolve()
        if vault.exists():
            if not vault.is_dir():
                raise ValueError(f"target path exists and is not a directory: {vault}")
            if any(vault.iterdir()):
                raise ValueError(f"target directory is not empty: {vault}")
        else:
            vault.mkdir(parents=True, exist_ok=False)
    meta = vault / "00-meta"
    meta.mkdir()
    (meta / "vault-config.json").write_text(
        json.dumps({"handoff_note": "handoff.md"}) + "\n", encoding="utf-8"
    )
    (vault / "handoff.md").write_text(
        "# Shelf labels\n\n"
        "Decision: Use 14-point type for the shelf labels.\n"
        "Next: Print one label at actual size.\n",
        encoding="utf-8",
    )
    notes = vault / "notes"
    notes.mkdir()
    (notes / "label-trial.md").write_text(
        "# Comparing sample labels\n\n"
        "Reading trial: all three readers could read 14-point type at arm's length.\n",
        encoding="utf-8",
    )
    return vault


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the disposable shelf-label practice vault used in docs/first-handoff.md."
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="create the practice vault at DIR (must not exist or must be empty; default: new temp folder)",
    )
    args = parser.parse_args(argv)
    try:
        vault = create_demo_vault(args.dir)
    except (ValueError, OSError) as exc:
        print(f"first_handoff: {exc}", file=sys.stderr)
        return 2
    print(vault)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
