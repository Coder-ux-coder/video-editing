# 01 Intake and analysis

## Purpose
Understand the job before touching the edit: what the client wants, what the footage is,
and what could block delivery. Ask the human only what blocks progress.

## Inputs
`jobs/<id>/brief.md`, the client style file (if any), footage, `references/`.

## Procedure
1. **Workspace.** If the job does not exist: `cutroom new <job> --client <client>`. Ask the human to
   fill `brief.md` if it is still the template, or fill it yourself from what they told you and confirm.
2. **Ingest.** `cutroom ingest <job> <files or folders> [--role main|broll|music]`. Read every
   `failed` line; corrupt or partial files are reported, never silently skipped.
   Use `--reference` only when the disk is short of space (the job then depends on the original paths).
3. **Analyze.** `cutroom analyze <job>` (proxies, transcript, refined words, audio report, visual
   report, pacing, footage-type guess, topic task). On a 10-minute talking head expect a few minutes.
4. **Read** `analysis/summary.md` completely. Then **open** `analysis/<src>/sheets/overview.jpg` and at
   least one shot sheet per distinct camera angle. Confirm or correct the footage-type guess by looking.
5. **Note risks** from the reports:
   - audio flags (clipping, low speech-to-noise, very quiet, true peak) → affects Phase 2 work, tell the human if severe;
   - visual flags (face under/over-exposed, soft, colour cast, cut-off head);
   - multiple cameras (many alternating shots) → cut at camera changes where possible;
   - interviewer voice or off-mic chatter (⟪quiet: …⟫ in the outline, questions in the transcript).
6. **Job plan.** Write `jobs/<id>/plan.md` with: deliverables, target long-form length, number of
   Shorts, footage type and strategy (EDITING_STANDARDS §3), risks, questions, and a time estimate
   (analysis ≈ 0.5× footage length on a 4-core CPU; rough cut minutes; render ≈ 0.2× preview, longer for finals).
   Cost estimate is always €0; note disk use (`du -sh jobs/<id>`).

## Decision rules
- Ask only questions whose answer changes the edit. Everything else: decide, record, move on.
- Default target length when the brief is silent: keep everything useful, remove only waste (no content cuts) and ask.

## Outputs
Ingested sources, `analysis/*`, `plan.md`, a short message to the human (questions numbered, with recommendations).

## Failure handling
- Ingest `failed`: tell the human which file and why (the message includes a fix); continue with the rest.
- Transcription very slow: check `cutroom machine` (model choice); `cutroom doctor --select-asr` re-benchmarks.
- No face detected on a talking head: look at the sheets; low light or extreme angles defeat the detector.

## Checklist
- [ ] All footage ingested or explained
- [ ] `analysis/summary.md` read; contact sheets opened
- [ ] Footage type confirmed by looking
- [ ] `plan.md` written; blocking questions sent
