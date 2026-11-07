# Cutroom

An agent-native, non-destructive video editing system. Raw footage and a short brief go in;
a professional YouTube video and Shorts come out. The editor is an AI agent (Claude)
working through small, documented commands; a human approves the result.

Everything about an edit is inspectable text and data (transcripts, reports, a versioned
JSON timeline), so the agent can read, change, diff and re-render it, and a person can
read `log.md` to understand every decision.

**Status: Phase 0 (foundations) complete.** See `docs/CAPABILITIES.md` for what works today.

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

## Use (Phase 0)

```bash
source .venv/bin/activate            # Windows: .venv\Scripts\activate
cutroom doctor                       # verify the machine
cutroom new acme-launch --client acme
cutroom ingest acme-launch ~/footage/interview.mp4
cutroom proxy acme-launch            # 540p proxies + analysis audio
cutroom transcribe acme-launch       # word-level transcript
cutroom status acme-launch
```

The readable transcript is at `jobs/acme-launch/analysis/S01/transcript.txt`; the decision
log is `jobs/acme-launch/log.md`. Every command has `--help` and `--json`.

## Where things are

| | |
|---|---|
| `docs/CHARTER.md` | Decisions, detected machine, open questions |
| `docs/ARCHITECTURE.md` | How the system works and the phase plan |
| `docs/TOOLS.md` | Every command (generated from the CLI) |
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
