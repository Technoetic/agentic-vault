# Can the next worker finish the interrupted task?

This checkout-only experiment checks the file produced after a handoff. A participant starts counting accepted units in batch A. One run continues straight through. In the other, a worker handles a different batch before a fresh participant resumes A from a written handoff. Both A results face the same four checks against a hand-calculated answer. The interruption has its own answer, so a pair also needs evidence that B was completed.

## Run the scripted controls

```console
python scripts/evaluate_handoff_resume.py demo
```

The Python 3.10+ standard-library demo creates temporary directories and starts six Python interpreters. The uninterrupted worker completes A in one process. A second process handles the first row of A and writes a checkpoint. A third processes B in the same directory. Three fresh receivers each start with a separate copy of the resulting workspace. They run identical worker code, receive no original briefing, and differ only in the saved A handoff.

| Handoff given to the receiver | Expected observation |
|---|---|
| Original A checkpoint | All four A checks pass |
| B checkpoint in place of A | Task, source, accepted IDs and total fail |
| A checkpoint naming an earlier revision | Task matches. Source, accepted IDs and total fail |

The first row is identical in both A revisions. The stale-source control therefore resumes from a valid first-row subtotal and still produces the wrong final result. Each receiver must produce an artifact for its control to count. The process exits 0 when the controls behave as expected. The report records `evidence: scripted_subprocesses`. The workers follow fixed code. This run measures neither participant ability nor time saved by the plugin.

## Run a participant pair

```console
python scripts/evaluate_handoff_resume.py prepare trial-01
```

Choose a new directory under an existing parent. The command creates a pair of identical working directories, `uninterrupted/` and `resumed/`, and two coordinator-only briefings, `brief-a.json` and `brief-b.json`. Each workspace contains three synthetic source snapshots. Give a participant access only to the workspace and the briefing or handoff specified for that step. For a blind comparison use separate sessions or copies, keep this evaluator and its answers outside the participant's workspace, and record participant type and host separately from the score.

Give every participant this common output instruction. For the requested task, count units from rows with `accepted` equal to `true`. Return a JSON object containing the exact task ID (`task`), selected filename (`source`), accepted row IDs in source order (`accepted_ids`) and integer sum of their units (`total`). Use `focus-result.json` for A and `interrupt-result.json` for B.

1. **Uninterrupted arm.** Give the worker `brief-a.json` and the `uninterrupted/` workspace. Ask it to process the first row and then finish A in the same session.
2. **Switched arm, first worker.** Give a fresh worker the identical A briefing and `resumed/`. Ask it to handle the first row, leave a written handoff for the rest of A in that workspace and end the session. A human or a host using the plugin can choose its normal handoff format.
3. **Interruption.** Give a worker the B briefing and the same switched workspace. Have it complete B and save its result.
4. **Resumption.** Open a fresh session with the switched workspace and the common output instruction. Ask it to resume the unfinished A task from the handoff. Withhold the original A briefing, prior transcripts, evaluator and other arm's result.
5. **Score.** Keep the result filenames above. Run the scorer from the coordinating checkout.

```console
python scripts/evaluate_handoff_resume.py score trial-01
```

To record elapsed seconds, time each arm independently with the same start and stop rule. For example, start with the A briefing and stop when the A result file is saved. This rule includes B and any waiting time in the switched arm. To measure active A time, pause the switched clock during B and record that convention with the run. Supply any measured arm using `--uninterrupted-seconds 24.8` or `--resumed-seconds 71.5`. An omitted time appears as `null`.

The scorer checks task ID, source filename, accepted IDs and total in the submitted files. Blank or malformed JSON receives `invalid`; an absent artifact receives `missing`. A submitted pair returns exit 0 if all three artifacts pass and exit 1 if any misses a check. Reported times are operator observations. The score establishes artifact content only. It cannot establish who produced an artifact, which file the participant actually read, or whether the experimental protocol was followed. A single synthetic pair cannot estimate general handoff performance.

## Research basis

Switch-SWE compares matched uninterrupted and interleaved work with a common executable verifier across repository tasks. Its published task package keeps the same native agent session and scores the final focus checkout. The experiment here adapts that comparison to a fresh receiver and a small synthetic batch. It uses original fixtures and adds a separate B check and two deliberately damaged handoffs. [Switch-SWE paper](https://arxiv.org/abs/2611.02017v1) · [Pinned release README](https://github.com/Miraclecsc/Switch-SWE/blob/1d9a9ab0b8bf5fec9b730dfc810cfdbe159233ec/README.md#L30)
