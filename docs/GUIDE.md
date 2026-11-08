# Running a job: a guide for people

Cutroom is operated by an AI agent; you write the brief, answer its questions, and approve the
result. This guide covers what you do, what you get, and how to send notes. The commands are in
`docs/TOOLS.md`; the agent's own procedure is `prompts/00_orchestrator.md`.

## 1. Start a job

```bash
cutroom new spring-launch --client acme --title "Spring launch interview"
```

Fill in `jobs/spring-launch/brief.md`: who the video is for, what it must keep or cut, the
target length, names and terms to spell correctly, the credit line, and how many Shorts. Put
footage anywhere and tell the agent the path (it copies or references it; your files are never
changed). Optional:

- **Reference videos** you like in `references/`: the agent measures their pacing and makes a style
  from them (`cutroom analyze-reference`).
- **A client style** with your brand colours and fonts: `cutroom client-style acme` writes
  `styles/clients/acme.style.yaml` to edit.
- **Documents** with facts you want on screen (numbers, titles) in `jobs/<id>/inputs/`. On-screen
  numbers and claims come only from what is said in the footage, the brief or these documents.
- **Music or images** you have a licence for: `cutroom asset-add` records the file with its licence.

## 2. When the agent stops to ask

The agent works on its own and stops only at these points, with numbered questions and a
recommended answer for each (reply like "1 yes, 2 b"):

| Gate | Why it stops |
|---|---|
| Cut plan, new client | Your first video together: you approve the story before it is finished |
| Sensitive content | Medical, financial or legal claims; people named; private information; profanity policy |
| Facts | The speaker corrects a number; a name or brand is unclear in the recording |
| Anything not free | Never installed or used without you (the budget is zero) |
| Final approval | Always: nothing is delivered without you |

Open questions are also written to `jobs/<id>/questions.md` and appear at the top of the
delivery report.

## 3. Watch, then approve or send notes

When the edit is ready the agent prepares a package and stops:

```
jobs/<id>/delivery/<id>_v012/
  <id>.mp4             the video (1080p or 4K, -14 LUFS)
  <id>.srt / .vtt      captions
  thumbnail.jpg, title.txt, description.txt (chapters and credits)
  shorts/              each Short with captions and its own title and description
  nle/                 the edit for Resolve (OTIO, FCPXML), Final Cut (FCPXML), Premiere/Avid (EDL)
  REPORT.md            open questions, checks, review scores, accepted issues, licences
  APPROVAL.md          what to do next
  MANIFEST.json, checksums.sha256
```

Watch the video and the Shorts in full; read the title, description and REPORT.md. Then either:

```bash
cutroom approve <id> --package <id>_v012 --by "Your Name"
```

or write notes as you would to any editor, one per line, with the times you saw:

```
- 0:19 the name card should say "Dr."
- the music at the start is too loud
- 8:06 cut the part about the parking
```

and give them to the agent: `cutroom revise <id> --notes notes.txt --seen v012`. Each note is
mapped to the same moment in the current edit (even if cuts were made since), answered (done,
a question back to you, or declined with the reason) and traced to a new version in
`review/revisions/rNN/report.md`. Cutroom never sends anything anywhere: you upload the package.

## 4. Where to look

| You want | Look at |
|---|---|
| What the footage contains | `analysis/summary.md`, the contact sheets in `analysis/<source>/sheets/` |
| Why something was cut | `cutroom show <id>` (every cut with its reason), `log.md` |
| What changed between versions | `cutroom versions <id>`, `cutroom diff <id> v010 v012` |
| How good the edit is | `review/vNNN/report.md` and its contact sheets |
| Where time went, what it cost | `cutroom report <id>` (logs/report.md): always $0.00 |
| What is left to do | `cutroom next <id>` |

Every edit is a new version; `cutroom undo <id>` steps back to the previous one, and undone versions stay on disk.

## 5. Overnight and interrupted work

- Put commands in a text file, one per line, and run `cutroom batch overnight.txt`. Each line runs
  on its own; if the machine stops, run the same batch again: finished lines are skipped.
  `approve` cannot be in a batch.
- A render that was interrupted (power, crash, Ctrl-C) keeps its finished parts; the same
  `cutroom render` command continues from there. A render of a changed edit starts clean.

## 6. If something fails

Every error ends with a `Fix:` line saying what to do. `cutroom doctor` checks the machine
(FFmpeg, speech model, graphics package, disk, memory) and prints a fix for each problem. Renders
check themselves (frames, sync, loudness, clicks, captions, graphics placement); a failed check
stops delivery until it is fixed or explained.
