# 00 Orchestrator: the master editing loop

## Purpose
Take a job from raw footage and a brief to a delivered, client-ready video (and Shorts),
working autonomously, stopping only at the human gates. You are the editor: Cutroom's
commands are your hands and eyes; `docs/EDITING_STANDARDS.md` is your taste.

## Inputs
- `jobs/<id>/brief.md` (and the client's style file `styles/clients/<client>.style.yaml`, if any)
- Footage (already ingested, or paths to ingest)
- Reference videos in `references/` (optional)

## Before you start (every session)
1. `cutroom doctor --quick` must pass. If it fails, follow the printed fixes; do not edit around a broken toolchain.
2. Read `docs/EDITING_STANDARDS.md` sections 1–4 (and the sections for the current stage).
3. `cutroom next <job>` (every stage done or not, and the next command), `cutroom versions <job>`: know where the job is. Resume; never restart work that exists.

## Stages (in order)

| # | Stage | Playbook | Available |
|---|---|---|---|
| 1 | Intake: brief, questions, job plan | `01_intake.md` | now |
| 2 | Analyze: `cutroom analyze <job>`, read `analysis/summary.md`, open contact sheets | `01_intake.md` §Analyze | now |
| 3 | Transcript review: names, terms, sensitive content | `02_transcript_review.md` | now |
| 4 | Story and cut plan (topics, hook, structure, cuts) | `03_story_and_cut_plan.md` | now |
| 5 | Rough cut | `04_rough_cut.md` | now |
| 6 | Audio polish | `05_audio.md` | now |
| 7 | Captions and motion graphics (lower thirds, facts, punch-ins, transitions) | `06_graphics_and_captions.md` | now |
| 8 | B-roll, music, sound effects, thumbnail | `07_broll_and_music.md` | now |
| 9 | Review loop (render → checks → look at frames → fix, ≤ 4 rounds) | `09_review_loop.md` | now |
| 10 | Shorts (find, create, frame 9:16, hook, captions, review, publishing text) | `08_shorts.md` | now |
| 11 | Publishing text, final render, delivery package (human approval), revisions | `10_delivery.md` | now |

Until a stage's tools exist, skip it and say so in the job log and the final summary. Never fake a stage.

## Proceed autonomously when
- The brief is clear enough to choose a structure and a target length.
- Cuts are mechanical or high-confidence (silence, fillers, stumbles, confident retakes).
- A choice is reversible (every edit is a new version; `cutroom undo` exists).

## Stop and ask the human when
- **New client**: the cut plan needs approval before the rough cut is treated as final (gate a).
- **Final render before delivery** (gate b): `cutroom deliver` prepares the package AWAITING
  APPROVAL; a person watches it and runs `cutroom approve`. Never run `approve` yourself.
- **Anything that is not free** to install or use (gate c). Zero budget is absolute.
- **Sensitive content** flagged in transcript review: medical/financial/legal claims, defamation risk, private data, profanity policy unclear, third parties named (gate d).
- **Ambiguous brief**: target length, audience or must-keep content unclear and it changes the edit.
- **Low-confidence transcript on key terms** (names, brands, numbers) that a glossary cannot resolve.
- **Factual corrections** in the footage (speaker corrects a number): which version is right is never your call.
- **Missing assets** (logo, fonts, music the brief requires) with no free, licensed equivalent.

When you ask: one message, numbered questions, each with your recommended answer, so the human can reply "1 yes, 2 b".

## Rules that always apply
- Never modify source footage; never delete job files; never upload client material anywhere.
- Every judgement goes in a file (plan, topics, decisions) and in `log.md` with a reason.
- Prefer commands over hand edits. If no command exists, use `cutroom ffmpeg` or write a new tool
  (with a manifest and a test) and add it to the library (self-extension rule).
- Be honest in summaries: say what was verified, what was not, and what needs a human eye.
- Client notes become a revision round (`cutroom revise`, playbook 10): every note is answered
  (done, question, or declined with the reason) and traced to a version.

## Outputs
A versioned timeline, renders, and (from Phase 7) a delivery package; a short plain-language
status message to the human at each gate.

## Failure handling
- A command fails: read its `Fix:` line and follow it; if it repeats, stop and report the exact error.
- A render check fails (`checks` in the render JSON): do not deliver; investigate with `cutroom frames`, `cutroom show`, `cutroom diff`.
- You are unsure an edit is right: render a preview, look at frames around it, and if still unsure, leave it in and list it for the human.

## Checklist (end of every session)
- [ ] `cutroom validate <job>` passes on HEAD
- [ ] `log.md` explains every decision made this session
- [ ] Open questions for the human are written down in `questions.md`, numbered, with recommendations
      (`cutroom deliver` puts them at the top of the package report)
- [ ] Skipped stages are named with the reason
