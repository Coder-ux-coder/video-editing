# Cutroom

An agent-native, non-destructive video editing system. Raw footage and a short brief go in;
a YouTube video (16:9, 1080p or 4K) and Shorts (9:16) come out, with captions, motion graphics,
b-roll, music, a thumbnail, chapters and publishing text. The editor is an AI agent (Claude)
working through small, documented commands; a person approves the result before it goes anywhere.

Everything about an edit is inspectable text and data (transcripts, reports, a versioned JSON
timeline), so the agent can read, change, diff and re-render it, and a person can read `log.md`
to understand every decision. Source footage is never modified.

**Status: Phases 0–8 complete.** `docs/CAPABILITIES.md` says what works and what does not;
`docs/PHASES.md` has a report per phase with its acceptance run. A person's guide to running a
job is `docs/GUIDE.md`.

Free and local: every tool, model and asset is free and runs on your machine; nothing is billed
per use, and Cutroom contains no code that uploads footage (`cutroom report <job>` shows the cost,
$0.00, and what the network was used for).

## Set up

Needs about 5 GB of free disk for tools and models, plus room for footage. Everything installed is free.

macOS / Linux:

```bash
./setup.sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

Setup finds or installs Python 3.12 and FFmpeg, installs pinned dependencies into `.venv`, the
graphics package (Node.js, Remotion) and its headless browser, then runs `cutroom doctor
--select-asr`, which benchmarks speech models on your machine, downloads the best one, and
transcribes a test clip end to end. Add `--yes` to accept install prompts, or `--dry-run` to see
what would happen.

## Use

An agent runs a job by following `prompts/00_orchestrator.md`; `cutroom next <job>` always says
which stage is next. The main commands, in order:

```bash
source .venv/bin/activate            # Windows: .venv\Scripts\activate
cutroom new acme-launch --client acme            # then fill jobs/acme-launch/brief.md
cutroom ingest acme-launch ~/footage/interview.mp4
cutroom analyze acme-launch          # transcript, audio/visual reports, contact sheets, summary.md
cutroom rough-cut acme-launch        # silence, fillers, stumbles, retakes removed; preview rendered
cutroom show acme-launch             # read the edit: every clip, every cut and why
cutroom audio acme-launch            # voice chain, loudness to -14 LUFS
cutroom captions acme-launch
cutroom graphic-add acme-launch lower-third -p name="Ana Ruiz" -p title="Founder" -f brief
cutroom broll-suggest acme-launch; cutroom music acme-launch --generate calm --whole
cutroom review acme-launch --fix     # render, checks, contact sheets, automatic fixes (≤ 4 rounds)
cutroom shorts-find acme-launch      # then short-create / short-hook / short-publish
cutroom publish acme-launch          # title, description, chapters (checked)
cutroom thumbnails acme-launch --text "Three words here"
cutroom render acme-launch --mode final && cutroom review acme-launch
cutroom deliver acme-launch          # package AWAITING APPROVAL; a person runs `cutroom approve`
```

Client notes become a revision round: `cutroom revise <job> --notes notes.txt --seen v012`.
Long unattended work goes in a batch file: `cutroom batch overnight.txt` (re-run to resume).
An interrupted render resumes when the same command is run again.

Start with `jobs/<id>/analysis/summary.md`, then the outline (`cutroom show`). The decision log
is `jobs/<id>/log.md`; every version is in `jobs/<id>/versions/` (`cutroom versions`, `cutroom diff`,
`cutroom undo`). Every command has `--help` and `--json`.

## Where things are

| | |
|---|---|
| `docs/GUIDE.md` | How a person runs a job, approves it and sends notes |
| `docs/CHARTER.md` | Decisions, detected machine, open questions |
| `docs/ARCHITECTURE.md` | How the system works and the phase plan |
| `docs/EDITING_STANDARDS.md` | What "professional" means, with sources; the defaults behind the style file |
| `docs/TOOLS.md` | Every command (generated from the CLI) |
| `docs/TIMELINE_SCHEMA.md` | The project file, field by field (generated) |
| `prompts/` | The agent playbook (00 orchestrator to 10 delivery) |
| `styles/` | Style files: defaults, per client (`styles/clients/`), learned from references |
| `templates/`, `graphics/` | Motion graphics templates and their Remotion components |
| `docs/LICENSES.md` | Every dependency, model and asset, with its licence |
| `docs/CAPABILITIES.md` | What it can and cannot do |
| `jobs/<id>/` | One workspace per job (not committed) |

Environment variables: `CUTROOM_JOBS_DIR` (put jobs on a bigger drive), `CUTROOM_MODELS_DIR`,
`CUTROOM_FFMPEG_DIR` (use a specific FFmpeg), `CUTROOM_BROWSER` (a Chrome Headless Shell for
Remotion, if not the one setup installed).

## Develop

```bash
.venv/bin/python -m pytest -m "not slow"   # fast suite (no speech model needed)
.venv/bin/python -m pytest                 # everything: renders with graphics, golden end-to-end, transcription
CUTROOM_UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_golden.py   # after an intended change to the edit
cutroom docs                               # regenerate docs/TOOLS.md after changing commands
scripts/lock.sh                            # re-pin dependencies (needs uv)
```

New commands are plugins: a module in `src/cutroom/tools/` with a manifest and a test
(`docs/ARCHITECTURE.md`, "Plugins"). CI (`.github/workflows/tests.yml`) runs only when
started by hand from the Actions tab.
