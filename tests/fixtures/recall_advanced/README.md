# Advanced recall fixture

Synthetic explicit dates reproduce a stale lexical top-one without changing the legacy ranker. The old Phoenix note is valid during 2026-01-01 <= date < 2026-09-15; the current note starts 2026-09-15. Neither source is real corporate data. Retrieval metrics do not certify claim truth or source authority.

The 16 English/Korean query labels include per-query advanced options. Current and historical dates, two-hop filenames, duplicate filename rejection, BM25 and safe sidecar rereads are measured separately. Retired-only `no_answer` labels measure returned sources, not certified truth or host abstention. The unknown-currentness cases deliberately return unverified sources. All provider snippets and approval fields are untrusted data; no external provider is installed or run.
