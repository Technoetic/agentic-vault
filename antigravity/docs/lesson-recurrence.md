# Lesson recurrence classes

`vault_lesson_clusters.py` is a read-only report over a Markdown mistakes note. It shows which entries the writers have
already tied together, how often each class came back, and whether a lesson ledger row covers it. The report is material
for a person to read. It writes nothing, it changes no counter, and a class does not prove a shared cause.

```text
python skills/agentic-vault/scripts/vault_lesson_clusters.py --vault PATH --mistakes RELATIVE.md
python skills/agentic-vault/scripts/vault_lesson_clusters.py --vault PATH --mistakes RELATIVE.md --ledger 00-meta/lessons.md --threshold 0.4 --id-prefix M
```

Both notes must be readable under the vault's deny zones and exclusions. A failure prints a fixed code and exits with 2.

## What it reads

An entry opens with a numbered heading or list item. These forms are read, because real notes drift between them:

```text
## M-901 · title (2026-09-01)
## M-902 — title
## M-903 - title
### M-904 재발 (2026-10-01) — title
- **M-905 title (2026-09-05):** text
- M-906 · title
- M-907: title
```

An h3 with the number of an existing entry joins that entry. A number used by two different h2 entries is listed in
`diagnostics.duplicate_ids`, and those entries are read as one. A list item that names a number of a heading entry stays
body text, and so does `- M-901과 같은 구조다`. Fenced code is ignored. HTML comment blocks are read like body text: a person
may have commented an entry out, so the report does not decide that for them. The cue compiler is stricter, because it
puts text into a live hook. `--id-prefix` sets the letters before the hyphen.

## How classes form

| Step | Signal | Result |
|---|---|---|
| 1 | Recurrence markers: `### M-901 재발`, `### 재발`, a line opening with `**재발` | Adds one occurrence to the entry |
| 2 | A clause with `같은 부류`, `같은 계열`, `같은 유형`, `같은 구조`, `same class`, `same family`, `same pattern`, or a number followed by `재발`, `계열` or `부류` | Joins the numbers in the clause to the entry (`status: explicit`) |
| 3 | An ordinal with a repeat word in one clause (`5번째 재발`) | Raises the class count to that number, never adds to it. `count_is_candidate` is true when this decided the count |
| 4 | The remaining entries: syllable-bigram Jaccard over the `hangul2` tokens of `vault_retrieval.py` | Groups in which every pair reaches the threshold; a score equal to it counts (`status: candidate`) |

`재발 방지` and `재발 조짐` are not recurrences. A negated clause (`같은 부류가 아니다`) joins nothing. Text after a `관련:` or
`참고:` label joins nothing and takes no part in the similarity: such links can tie most entries of a note into one
component. Numbers are never masked, because the numbers people wrote are the strongest signal in the file.

Step 4 has two filters. With 8 or more entries, a token found in more than half of them is dropped, and one found in
exactly half stays. An entry with fewer than 4 tokens left takes no part.

## Output

`classes` lists classes with a count of 2 or more, largest first. Entries that stay alone are only counted in
`singletons`.

| Field | Meaning |
|---|---|
| `status` | `explicit` (a writer's own marker or reference) or `candidate` (similarity only) |
| `ids`, `count` | Entry numbers, and entries plus recurrence markers |
| `first_seen`, `last_seen` | Dates from entry titles and marker lines, or else the first date in the entry body; `null` when there is none |
| `ledger_linked`, `ledger_lines` | Whether a ledger row names one of the numbers, and those rows. `null` when the ledger is missing or invalid (`ledger.state`) |
| `unledgered` | True for a count of 3 or more with no ledger row. False when the link is unknown |

`ledger.ignored_rows` counts ledger rows that the ledger parser leaves out. Such a row still counts as a link here, because
a person wrote it.

## Limits

- **Candidates need reading.** Automatic counts stay candidates. A hand reading of one private note found both a false
  positive and misses from ordinal and marker counting. That is an unquantified observation, and nothing in this
  repository reproduces it.
- **The default threshold is not calibrated.** No labelled sample ships with the engine, so the report says
  `default_uncalibrated`. Set `--threshold` from entries a person has grouped. Similarity has not been evaluated on Korean
  free text, and a long entry scores low against a short one.
- **Explicit classes are transitive.** Each link is a person's statement, but a chain of links can join entries that a
  reader would keep apart. A large explicit class is a reason to read it, not a finding.
- **The ledger link is an exact number match.** A row that describes the same lesson without a number does not count, so
  `unledgered` can be wrong in that direction. A person connects rows; the tool does not.

## Related changes

`vault_lesson_delta.py` no longer drops a ledger row silently. A row that opens like a ledger row but does not have the five
expected cells stays out of the inventory as before. The proposal now reports it in `ignored_rows` as line number and cell
count, without its text, and an `ADD` with the same text is refused as `duplicate_lesson_text`. A proposal without such a
row keeps its previous shape.

Cues for the optional tool-call hook are described in the
[lesson cue guide](../assets/adapters/lesson-cues/guide.md).

## Sources

The masking idea was weighed against [Drain3](https://github.com/logpai/Drain3) at v0.9.11 (commit
78d3cae4bcac329c4e4f275d416562e10c5c446e, MIT per its `LICENSE.txt`) and rejected for this input. No code was copied.
