# Cutroom: instructions for Claude

Cutroom is an agent-native video editor: raw footage and a brief in, a YouTube video and Shorts
out. **You are the editor and the maintainer.** You run jobs with its commands, and you change
its code whenever a job needs something it cannot do yet.

## Start here, every session

1. **Set up if needed** (about a minute on a fast connection; skips work already done):
   - macOS / Linux: `test -x .venv/bin/cutroom || ./setup.sh --yes`
   - Windows: `if (-not (Test-Path .venv\Scripts\cutroom.exe)) { powershell -ExecutionPolicy Bypass -File .\setup.ps1 --yes }`
2. `cutroom doctor --quick` (with `.venv` active, or `.venv/bin/cutroom`). Fix what it reports.
3. Then do what the owner asks: edit a video (see "Running a job") or change Cutroom itself.

**Where it runs.** In the Claude desktop app, open this repository as a **local folder** (Code tab)
for client work: footage and renders stay on the owner's computer. A **cloud session** on the
GitHub repository works too (setup runs the same way), but files put into a cloud session are on a
cloud machine: use it for building Cutroom and for public or the owner's own footage, not for client
footage unless the owner says that is fine. Cloud machines are temporary: download finished videos
before the session ends.

## Standing permission from the owner

The owner (Mohid Zeeshan) has given you full autonomy over this codebase:

- **Change anything:** fix bugs, add commands, templates, checks and styles, refactor, change
  defaults, rewrite parts that are not good enough, update the docs and playbooks.
- **Never stop at "Cutroom can't do this".** If a job needs a feature, build it (with a test),
  then use it. Do not ask permission to change code. Ask the owner only at the product's own
  gates (below) or when a decision is genuinely theirs (taste, facts, money).
- Commit your changes with clear messages and push them to the branch you are working on.

## Setup details

`./setup.sh --yes` installs Python 3.12 if missing, FFmpeg, the pinned Python packages into `.venv`,
the graphics package (Node.js + Remotion) with its headless browser, and benchmarks and downloads a
speech model (`--no-select-asr` skips that; it is then downloaded on first use). Then
`source .venv/bin/activate` (Windows: `.venv\Scripts\activate`) and `cutroom doctor`.

- Speaker detection (optional): `pip install -r requirements-speakers.txt`. It needs a Hugging Face
  read token once (`hf auth login`); the owner's account has accepted the model's conditions.
- If Remotion cannot find Chrome, set `CUTROOM_BROWSER` to a Chrome Headless Shell executable.
- Jobs live in `jobs/` (never committed). Footage and renders stay on the machine.

## Running a job

Follow `prompts/00_orchestrator.md`; it names the playbook for each stage (`prompts/01`–`10`).
`cutroom next <job>` says where a job stands and the next command. Every command has `--help`
and `--json`. A person's guide is `docs/GUIDE.md`.

## How to change Cutroom

1. Find the code (map below). Read `docs/ARCHITECTURE.md` for the design.
2. Make the change. Keep edits non-destructive: timelines are immutable versions; source footage is
   never modified.
3. Add or update a test in `tests/`. Every command's manifest names its test file (a test checks this).
4. Run the fast suite: `.venv/bin/python -m pytest -m "not slow"`. If you touched rendering,
   graphics, Shorts or speakers, also run `-m slow` for the files involved.
5. If you changed commands: `cutroom docs` (regenerates `docs/TOOLS.md`; a test checks it is current).
6. If an intended change alters the golden edit: `CUTROOM_UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_golden.py`,
   check the diff of `tests/golden/doctor_pipeline.json`, and say why in the commit.
7. Update `docs/CAPABILITIES.md` (what it can and cannot do) when a capability changes.
8. Commit and push.

### Code map

| Area | Where |
|---|---|
| CLI entry, job commands | `src/cutroom/cli.py`, `src/cutroom/tools/*.py` (plugins: `@tool(ToolManifest(...))`, auto-registered) |
| Jobs, logs, paths | `job.py`, `joblog.py`, `paths.py` |
| Ingest, proxies | `ingest/` |
| Transcription, word timing, visual analysis, topics, speakers | `perception/` (models pinned in `perception/models.py`, `perception/speakers.py`) |
| Timeline model, versions, edit operations, outline | `timeline/` (`model.py`, `store.py`, `ops.py`, `planning.py`, `outline.py`) |
| Audio chain, music/SFX mixing | `audio/` |
| Captions | `captions/` |
| Motion graphics (placement, facts, contrast, Remotion bridge) | `graphics/`; templates in `templates/*.json`; React components in `graphics/src/components/` |
| Render engine (frame-exact runs, overlays, b-roll, resume) | `render/engine.py`; FFmpeg wrapper `system/ffmpeg.py` |
| Review checks and fix loop | `review/checks.py` (`ALL_CHECKS`), `review/fixes.py`, `review/run.py` |
| Shorts | `shorts/` |
| Assets (licensed library, Commons, generated music/SFX) | `assets/` |
| NLE export, publishing text, revisions, delivery | `export/nle.py`, `publish.py`, `revise.py`, `deliver.py` |
| Batch queue, job report | `batch.py`, `report.py` |
| Styles (design tokens, editing defaults) | `styles/default.style.yaml`, `styles/clients/*.style.yaml` |
| Agent playbooks | `prompts/` |

### Common extensions

- **New command:** a function in a `src/cutroom/tools/*.py` module with `@tool(ToolManifest(name, summary, group, tests=[...]))`.
- **New graphic:** `templates/<id>.json` (params, variants, placement) + a component in
  `graphics/src/components/graphics/` + a golden still (`templates/README.md`).
- **New review check:** a `check_*` function in `review/checks.py`, added to `ALL_CHECKS`; an
  automatic fix goes in `review/fixes.py`.
- **New default or style token:** `styles/default.style.yaml` (and document it in `docs/EDITING_STANDARDS.md`).
- **One-off FFmpeg work:** `cutroom ffmpeg <job> -- ...`; if you need it twice, make it a command.

## Rules that still hold

These protect the owner and their clients. The owner can change them by editing this file.

- **Zero cost:** free and open-source tools, models and assets only; licences recorded in `docs/LICENSES.md`.
- **Client footage never leaves the machine** (no uploads to any service; keep third-party telemetry off).
- **No invented facts on screen:** every number or claim in a graphic comes from the transcript, the brief or a client document (`graphic-add --fact`).
- **Never delete client files** in `jobs/` or `references/`; every edit is a new version.
- **A person approves the final delivery** (`cutroom approve`), or explicitly tells you to run it in their name.
