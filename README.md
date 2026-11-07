# Cutroom

An agent-native, non-destructive video editing system. Raw footage and a short brief go in;
a professional YouTube video and Shorts come out. The editor is an AI agent (Claude)
working through small, documented commands; a human approves the result.

Everything about an edit is inspectable text and data (transcripts, reports, a versioned
JSON timeline), so the agent can read, change, diff and re-render it, and a person can
read `log.md` to understand every decision.

**Status: Phase 1 (perception and transcript-driven cutting) complete.** See `docs/CAPABILITIES.md` for what works today and `docs/PHASES.md` for phase reports.

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

Setup finds or installs Python 3.12 and FFmpeg, installs pinned dependencies into `.venv`,
then runs `cutroom doctor --select-asr`, which benchmarks speech models on your machine,
downloads the best one, and transcribes a test clip end to end. Add `--yes` to accept
install prompts, or `--dry-run` to see what would happen.

## Use

```bash
source .venv/bin/activate            # Windows: .venv\Scripts\activate
cutroom doctor                       # verify the machine
cutroom new acme-launch --client acme
cutroom ingest acme-launch ~/footage/interview.mp4
cutroom analyze acme-launch          # transcript, audio/visual reports, contact sheets, summary.md
cutroom rough-cut acme-launch        # silence, fillers, retakes removed; preview rendered
cutroom show acme-launch             # read the edit: every clip, every cut and why
cutroom cut acme-launch --sentences 2-3 --reason "interviewer question"
cutroom move acme-launch --sentences 8-9 --to 0 --reason "cold open"
cutroom render acme-launch --mode final
```

Start with `jobs/<id>/analysis/summary.md`, then the outline (`cutroom show`). The decision log
is `jobs/<id>/log.md`; every version is in `jobs/<id>/versions/` (`cutroom versions`, `cutroom diff`,
`cutroom undo`). Every command has `--help` and `--json`. An agent should follow `prompts/00_orchestrator.md`.

## Where things are

| | |
|---|---|
| `docs/CHARTER.md` | Decisions, detected machine, open questions |
| `docs/ARCHITECTURE.md` | How the system works and the phase plan |
| `docs/EDITING_STANDARDS.md` | What "professional" means, with sources; the defaults behind the style file |
| `docs/TOOLS.md` | Every command (generated from the CLI) |
| `docs/TIMELINE_SCHEMA.md` | The project file, field by field (generated) |
| `prompts/` | The agent playbook |
| `docs/LICENSES.md` | Every dependency, model and asset, with its licence |
| `docs/CAPABILITIES.md` | What it can and cannot do yet |
| `jobs/<id>/` | One workspace per job (not committed) |

Environment variables: `CUTROOM_JOBS_DIR` (put jobs on a bigger drive), `CUTROOM_MODELS_DIR`,
`CUTROOM_FFMPEG_DIR` (use a specific FFmpeg).

## Develop

```bash
.venv/bin/python -m pytest            # all tests (slow ones need a downloaded model)
.venv/bin/python -m pytest -m "not slow"
cutroom docs                          # regenerate docs/TOOLS.md after changing commands
scripts/lock.sh                       # re-pin dependencies (needs uv)
```
