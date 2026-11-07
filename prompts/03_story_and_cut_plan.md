# 03 Story and cut plan

## Purpose
Decide what the video is before cutting it: hook, structure, what goes, what stays, target
length. For a new client this plan is approved by the human before the edit is final (gate a).

## Inputs
`analysis/summary.md`, transcript, `transcript_notes.md`, the brief, `docs/EDITING_STANDARDS.md` §2–4,
pacing map (`analysis/<src>/pacing.json`), retake candidates (`cutroom detect-retakes <job>`, no `--apply`).

## Procedure
1. **Topic map.** Open `analysis/topics.task.md`, follow it, write `analysis/topics.json`, run
   `cutroom topics <job> --check` until valid. Judge usefulness honestly (1 = cut candidate).
2. **Hook candidates.** Pick 2–3 lines that would make a stranger keep watching: a surprising claim,
   a stake, a question, a vivid image. Prefer lines that work without context. Note timecodes.
3. **Structure.** Hook → context → body (topics ordered by value and logic) → payoff → one CTA.
   Reordering is allowed when it serves clarity, never to change meaning.
4. **Cuts.** List, with reasons:
   - mechanical: silence, fillers, stumbles and confident retakes (the rough-cut tools will do these);
   - editorial: interviewer questions, off-topic tangents, repeated points, low-usefulness topics, end chatter;
   - retake candidates below 0.75 confidence: decide each one (keep or cut) and say why.
5. **Target length** from the brief; if none, "everything useful".
6. **Visual plan (draft).** Where jump cuts will need disguise (Phase 3 punch-ins), where b-roll or graphics
   would help (Phases 3, 6). Mark only; do not invent facts for graphics.
7. Write `jobs/<id>/cut_plan.md` (template below). For a new client, send it to the human and wait for approval
   before treating the edit as final; you may proceed with the rough cut meanwhile (versions are cheap).

## Template: cut_plan.md
```
# Cut plan: <job>
Target: <length> long-form; <n> Shorts
Hook: "<line>" (S01 mm:ss) — why
Structure: 1) … 2) … 3) …
Editorial cuts:
- S01 mm:ss–mm:ss "<first words…>": interviewer question (answer stands alone)
- …
Retake decisions (below auto threshold): r00x keep/cut because …
Risks / questions for the client:
```

## Decision rules
- Cut for the viewer, not for length alone. Keep moments with emotion or a clear example even if a sentence is imperfect.
- Never cut a qualifier that changes a claim ("in some cases", "we think").
- When two takes are close, keep the one with clearer delivery and better energy (check the audio level and pace in the outline).

## Outputs
`analysis/topics.json` (valid), `cut_plan.md`, message to the human for new clients.

## Failure handling
- `topics --check` fails: fix the reported gaps/overlaps; every sentence belongs to exactly one topic.

## Checklist
- [ ] Topic map valid
- [ ] Hook chosen with reasons
- [ ] Every editorial cut has a timecode and reason
- [ ] Low-confidence retakes decided
- [ ] Plan sent for approval if new client
