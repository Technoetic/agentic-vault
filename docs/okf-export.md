# Selected-note OKF export

`vault_okf.py` exports explicitly selected allowed Markdown notes to a new directory outside the vault:

```text
python skills/agentic-vault/scripts/vault_okf.py --vault PATH --note relative/note.md --note other.md --output ABSOLUTE_PATH
```

`export_bundle(vault: Path, notes: list[str], destination: Path) -> dict` returns the manifest. Invalid or unavailable inputs raise `ExportError` with a non-sensitive code; the CLI prints JSON and exits 2. The output parent must exist and contain no symlink or reparse point. Existing destinations are refused. Linux, macOS, and Windows use native atomic no-clobber publication; platforms without the required primitive fail closed. The macOS branch is covered by a mocked API test but has not been exercised on macOS hardware in this work. Selected notes and configuration are re-read before publication; concurrent mutation after that point remains possible.

The original, modest producer profile `agentic-vault-selected-concepts-0.2` targets OKF 0.2 at SPEC commit `ad30107c31c06aec8a7d5636e0d1058118604e6f`. It is not full specification certification. The root index declares the version, and each mapped concept has a non-empty type. Original frontmatter is retained as a JSON-escaped YAML string under `x-agentic-vault-original-frontmatter`: it is not parsed or promoted into OKF status, review, dates, or generation metadata. Missing frontmatter is reported. UTF-8 BOM is accepted; source hashes include all original bytes.

Selected wiki links of the form `[[name]]`, `[[name|label]]`, `[[name#fragment]]`, or a selected vault-relative path are converted to URL-encoded relative Markdown links. Name resolution is case-insensitive for basenames and requires a unique selected match. Unresolved or ambiguous links and embeds remain literal and appear in `losses`; their targets are not read. Fenced blocks and simple inline backtick spans remain literal. Existing local Markdown links to selected notes are preserved; links to unselected paths, embeds, and unsafe schemes are reported as unconverted; consumers must review them before rendering or sharing. This limited syntax is not a full Markdown parser, and other Markdown constructs or metadata semantics can be lost or interpreted differently.

The manifest records `profile`, `spec_commit`, `sources`, `outputs`, `path_mapping`, and `losses`. Output hashes cover index and concept files; the manifest does not hash itself. Hashes are integrity signals, not signatures, review, approval, or authorization to share. Selection is limited to 64 files, 64 MiB per file, 256 MiB total, and 32 path components. No network or new runtime dependency is used.
