"""Create the disposable shelf-label project used in docs/first-handoff.md."""
import json
from pathlib import Path
import tempfile


def main():
    vault = Path(tempfile.mkdtemp(prefix="av-first-handoff-")).resolve()
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
    print(vault)


if __name__ == "__main__":
    main()
