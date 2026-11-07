# Cutroom architecture

Cutroom is a video editing system whose primary user is an AI agent (Claude), working
autonomously from raw footage and a short brief to a client-ready YouTube video and
Shorts. A human approves the cut plan for new clients and the final result.

The central design principle: **every fact about an edit is inspectable text or data**
that the agent can read, change, diff, version and re-render. The agent cannot watch
video the way a person does, so the system gives it reliable perception (transcripts,
audio metrics, sampled frames, structural reports) and a closed review loop
(render → inspect → critique → fix → repeat).

Status legend used below: **[built]** exists and is tested; **[Pn]** is planned for phase *n*.

---

## 1. Principles

| Principle | What it means in practice |
|---|---|
| Non-destructive | Source files are never modified. Ingest copies them read-only into the job (or references them in place with `--reference`). Every edit lives in the project file. |
| Versioned | Every meaningful change writes a new immutable timeline version (`versions/v001.json`, `v002.json`, …) with a changelog line, so any step can be undone or diffed. [P1] |
| Deterministic | Same project file + same assets + same machine profile = same render. Python deps, speech-model revisions and render settings are pinned. |
| Proxy-first | Analysis and previews use 540p constant-frame-rate proxies. Only final renders read full-resolution sources. |
| Logged | Each job has `log.md` (human-readable decisions with reasons and confidence) and `logs/commands.jsonl` (every command, arguments, status, timing). |
| Fail loudly | Every expected failure raises `CutroomError(message, fix)`. The CLI prints both and exits non-zero. No silent fallbacks without a log line. |
| Human gates | The agent iterates freely on drafts; client delivery, new-client cut plans, sensitive content and any non-free install need explicit approval. |
| Private and free | Client footage never leaves the machine. The network is used only to install free tools/models and to download free-licensed assets. No paid services. |

## 2. System overview

```
 raw footage + brief.md + references/
        │
        ▼
 ┌──────────────┐   ┌───────────────────┐   ┌────────────────────────────────────┐
 │ 1 Ingest     │──▶│ 2 Perception      │──▶│ 3 Project model (timeline JSON vN) │◀─┐
 │ probe, hash, │   │ transcript, audio,│   │ single source of truth, versioned  │  │
 │ proxy, audio │   │ visual, pacing,   │   └──────────────┬─────────────────────┘  │
 └──────────────┘   │ topics, frames    │                  │ read/modify via        │
                    └───────────────────┘                  ▼                        │
 ┌─────────────────────┐   ┌───────────────────────────────────────────┐            │
 │ 7 Style layer       │──▶│ 4 Tool layer: small deterministic commands│────────────┘
 │ style.yaml tokens,  │   │ (CLI + Python API, plugins, ffmpeg escape)│
 │ EDITING_STANDARDS   │   └──────────────────────┬────────────────────┘
 └─────────────────────┘                          ▼
 ┌─────────────────────┐   ┌───────────────────────────────────────────┐
 │ 8 Orchestration     │   │ 5 Render: FFmpeg assembly + audio,        │
 │ prompts/ playbook   │   │   graphics/captions overlay layers        │
 │ (the agent's loop)  │   └──────────────────────┬────────────────────┘
 └─────────┬───────────┘                          ▼
           │               ┌───────────────────────────────────────────┐
           └──────────────▶│ 6 Review: automatic checks + dense frame  │──▶ fixes as commands
                           │   sampling + report + scorecard           │
                           └──────────────────────┬────────────────────┘
                                                  ▼ (human approval)
                                     delivery/ package + NLE export
```

## 3. Files and conventions

### 3.1 Repository

| Path | Contents |
|---|---|
| `src/cutroom/` | Python package: `cli.py`, `ingest/`, `perception/`, `timeline/`, `tools/`, `render/`, `review/`, `export/`, `system/` |
| `graphics/` | Motion-graphics package (Remotion/React or alternative, see §11) [P3] |
| `styles/` | `default.style.yaml` and `clients/<client>.style.yaml` [P2] |
| `templates/` | JSON specs for reusable motion-graphics templates [P3] |
| `assets/` | Fonts, SFX, LUTs, music: each with recorded licence and source. `samples/` holds the synthetic test clip. |
| `prompts/` | The agent playbook, `00_orchestrator.md` … `10_delivery.md` [P1–P7] |
| `docs/` | Charter, this file, standards, schema, generated tool reference, licences, capabilities |
| `config/machine.json` | Per-machine profile written by `cutroom doctor` (not committed) |
| `models/` | Downloaded speech/vision models (not committed; `CUTROOM_MODELS_DIR`) |
| `jobs/` | Per-job workspaces (not committed; `CUTROOM_JOBS_DIR`) |
| `tools/` | Portable binaries setup may download, e.g. FFmpeg without admin rights (not committed) |

### 3.2 Job workspace (`jobs/<job-id>/`)

```
job.json          manifest: sources (id, hash, probe, proxy, audio, transcript), stage status
brief.md          the brief (template created by `cutroom new`)
log.md            decisions in plain language, newest at the bottom
logs/commands.jsonl
source/           read-only copies of the footage (S01_name.mp4, …)
proxy/            S01.mp4 … (540p short side, CFR, 1 s GOP)
analysis/S01/     audio_16k.wav, audio_48k.flac, transcript.{json,txt,srt}, and later
                  audio_report.json, visual_report.json, contact sheets, pacing.json
versions/         v001.json … immutable timeline versions + changelog [P1]
renders/          previews and finals [P1+]
review/vNNN/      report.md, scorecard.json, frame grids [P4]
delivery/         the client package [P7]
```

Job ids are 3–64 characters of `a-z 0-9 . _ -`. Source ids are `S01`, `S02`, … and never
change; timelines refer to sources only by id, so footage can be relinked by hash.

### 3.3 Time

- **Source time**: seconds (float) from the start of the source container. Transcript word
  times are in source time (audio-stream offset applied).
- **Timeline time**: seconds from the start of an output timeline.
- **Frame rate**: stored as the snapped nominal rate (29.97, 25, …) and passed to FFmpeg as an
  exact rational (`30000/1001`). Variable-frame-rate phone footage is conformed to constant
  frame rate in proxies and renders so audio stays in sync.
- **Cut placement** [P1]: cuts are computed on word boundaries with a few ms of padding,
  then snapped to the nearest frame of the output rate at render time. Audio is cut
  sample-accurately with short crossfades so joins never click.

### 3.4 Paths

All code uses `pathlib`. Manifests store paths job-relative (forward slashes) when inside the
job and absolute otherwise, so a job folder can move between machines and operating systems.

## 4. Layers

### 4.1 Ingest [built]

- `probe`: ffprobe → typed `MediaInfo` (display size after rotation/SAR, nominal and
  average fps with a VFR flag, HDR detection, bit depth, per-stream start offsets, timecode).
- `ingest`: probe first (bad files are rejected before any copy), then copy + hash in one
  read pass (BLAKE2b-128), mark the copy read-only, de-duplicate by hash, log everything.
  Folders expand recursively to media files. Failures on one file do not abort the rest.
- `proxy`: 540p short side (never upscaled), CFR at the exact source rate, H.264 CRF 23
  `veryfast`, 1-second GOP for exact seeking, BT.709 tags, HDR tone-mapped to SDR, AAC audio.
  Idempotent: skipped when the source hash and recipe version are unchanged.
- Audio extraction (one decode, two outputs): 16 kHz mono WAV (exactly what the speech
  model hears) and 48 kHz FLAC (lossless, for loudness/noise/silence analysis).

### 4.2 Perception

| Artifact | Approach | Status |
|---|---|---|
| Transcript | faster-whisper (CTranslate2) locally, word timestamps, Silero VAD, verbatim prompt to keep stumbles, hotwords from the client glossary. Model chosen per machine by benchmark. | **[built]** (glossary [P1]) |
| Word-timing refinement | Snap word edges to energy minima; forced alignment (wav2vec2 via torchaudio, CPU wheel) if measurements show >80 ms error | [P1/P2] |
| Filler detection | Verbatim ASR plus acoustic check of voiced gaps between words | [P1] |
| Speakers | Multi-track recordings: one track per speaker (free and exact). Single track: speaker embeddings + clustering with a non-gated free model (e.g. sherpa-onnx), see §11 | [P1/P5] |
| Audio report | `ebur128` (LUFS, LRA, true peak), `astats` (noise floor, clipping), `silencedetect`, per-speaker levels, speech/music segmentation | [P1/P2] |
| Visual report | PySceneDetect shots; MediaPipe face detection/landmarks (headroom, centring, mouth region); OpenCV blur/exposure/white balance; OCR for screen recordings (Tesseract or RapidOCR) | [P1/P4/P5] |
| Pacing map | Per-second speech rate, pause lengths, vocal energy, "boring score" | [P1] |
| Topic map | Agent-in-the-loop task (see §4.8): the tool chunks the transcript and writes a task file; the agent writes `topics.json`, which is schema-validated | [P1] |
| Frame sampler | `cutroom frames <job> --at 12.4 15.0 --grid 3x3`: labelled contact sheets from proxies | [P1] |

**Honest limit.** The agent perceives video through samples and reports, not continuous
playback. It can miss things that happen between samples (a one-frame glitch, a brief
gesture, subtle lip-sync drift). The review layer compensates with dense sampling around
every cut, graphic entrance/exit and transition, plus signal-level checks that look at
every frame (black/freeze/flash detection, audio discontinuities). What still cannot be
verified automatically is listed in every review report.

### 4.3 Project model [P1]

One canonical JSON timeline per version, validated by pydantic (and exported as JSON
Schema in `docs/TIMELINE_SCHEMA.md`): `meta`, `sources[]`, `tracks[]`, `clips[]` (with
`reason`, `confidence`, `locked`), `graphics[]`, `captions[]`, `audio`, `markers[]`,
`outputs[]` (long-form and each Short derive their own timeline). Commands:
`validate`, `diff vA vB` (human-readable change summary), `show --format outline`
(timestamps, speaker, text, active graphics: the agent's main view of the edit), `undo`, `snapshot`.

### 4.4 Tool layer

- One Typer CLI (`cutroom …`) over a Python API. Every command: `--help`, `--json`
  (one object on stdout; progress on stderr), `--dry-run` where it changes anything,
  a changelog/log entry, idempotent where possible. Refuses destructive or invalid
  operations (e.g. cutting mid-word) unless `--force`.
- `docs/TOOLS.md` is generated from the CLI's own help (`cutroom docs`), and a test fails
  if it is stale, so the reference never drifts.
- **Plugins** [P1]: a tool is a module in `src/cutroom/tools/` with a manifest (name,
  inputs, outputs, tests) that registers itself with the CLI and the docs generator.
- **Escape hatches** [P1]: `cutroom ffmpeg …` passthrough (logged in the job), and custom
  graphics compositions when no template fits (added to the library with a spec and test).

### 4.5 Render layer [P1–P3]

- **Assembly in FFmpeg**: the timeline compiles to an FFmpeg filter graph. Long timelines
  render in segments that are then concatenated, to bound memory on 16 GB machines.
  Audio is rendered as its own sample-accurate pass (cuts with micro-crossfades, the voice
  chain, music ducking, loudness normalisation) and muxed at the end.
- **Graphics and animated captions** render as transparent overlay layers (e.g. ProRes 4444
  or PNG sequences) composited by FFmpeg. FFmpeg stays the single master compositor; the
  graphics engine only draws graphics.
- **Encoders**: previews use a working hardware encoder if `doctor` found one
  (NVENC / VideoToolbox / QSV / AMF / VAAPI), otherwise libx264 `veryfast`. Finals use
  libx264 (`slow`, CRF ~16–18, High profile, yuv420p, BT.709) because it gives the best
  quality per bit; YouTube re-encodes anyway, so a clean high-bitrate master matters more
  than encode speed. Trade-off: finals take longer on slow machines.
- **Previews** burn in timecode and clip labels so frames in review reports can be traced.

### 4.6 Review layer [P4]

`cutroom review <job> [--version vNNN]`: preview render → automatic checks (audio, cuts,
captions, graphics, video, structure; thresholds come from the style file) → dense
frame sampling into annotated contact sheets that the agent opens and judges →
`review/vNNN/report.md` with prioritised issues (blocker/major/minor/polish), evidence and a
proposed fix as a concrete command → scorecard. The orchestrator applies fixes and repeats
up to 4 rounds or until no blockers/majors remain, then stops and reports.

### 4.7 Style layer [P2–P3]

`styles/*.style.yaml` holds design tokens (colours, fonts, easing, spacing, safe zones) and
editing parameters (silence thresholds, pacing targets, caption style, loudness target).
`analyze-reference` turns reference videos into a style profile [P6].
`docs/EDITING_STANDARDS.md` is the human-readable rationale behind the defaults [P1].

### 4.8 Orchestration layer

The agent (Claude, in a Claude Code session) is the only intelligence in the loop: no
external LLM API is called, because the budget is zero. Steps that need judgement use an
**agent-in-the-loop task** pattern:

1. A tool prepares the data and writes a task file (e.g. `analysis/topics.task.md`) with
   instructions, the relevant transcript chunk and the expected output schema.
2. The agent reads it, reasons, and writes the structured answer (e.g. `topics.json`).
3. `cutroom validate` checks it against the schema before anything uses it.

This keeps every judgement inspectable, reproducible (the answer is a file) and reviewable.
The playbook in `prompts/` tells a future session exactly which commands to run, in which
order, with which quality gates and when to stop and ask.

### 4.9 Export [P7]

OpenTimelineIO (Apache-2.0) as the interchange model, with FCPXML (DaVinci Resolve, Final
Cut), CMX 3600 EDL, and Premiere XML adapters, so a human can finish the edit in Resolve
(free) or Premiere. Each adapter's licence and round-trip fidelity are checked before use.

## 5. Hardware strategy

`cutroom doctor` detects the machine and writes `config/machine.json`; every command reads it.

| Decision | Rule |
|---|---|
| Memory budget | `min(60% of RAM, RAM − 4 GB)`; 9.4 GB on a 16 GB machine |
| Speech model (NVIDIA ≥ 8 GB VRAM) | large-v3, float16 |
| Speech model (NVIDIA 4–8 GB) | large-v3-turbo, int8_float16 |
| Speech model (CPU / Apple Silicon) | Benchmark `small.en` and `large-v3-turbo` on this machine; pick the more accurate one whose processing time is ≤ 0.6× audio duration and whose words/timings pass the test clip |
| ASR safety | Refuse to load a model if free RAM < its need + 1 GB, with a fix message (never OOM) |
| Render concurrency | `min(physical cores / 2, budget / 3 GB)`; 2 on 4 cores/16 GB |
| Proxies | 540p short side, libx264 (cheap at 540p, identical everywhere) |
| Hardware encoders | Compiled-in is not enough: each candidate is test-encoded; only working ones are used, for previews |
| GPU libraries | On NVIDIA machines setup installs pinned cuBLAS/cuDNN pip wheels; doctor falls back to CPU if CUDA does not work |

## 6. Reproducibility

- Python 3.12 only; `requirements.lock` pins every transitive dependency for all platforms
  (regenerate with `scripts/lock.sh`).
- Speech models are pinned to exact HuggingFace commit revisions (`perception/models.py`).
- Transcripts record engine, model, revision, device, compute type and all options.
- Renders [P1+] record the FFmpeg version and full command line. x264 output is
  byte-identical on the same machine profile and visually identical across machines.
- Recipe versions (`PROXY_VERSION`, `AUDIO_VERSION`) make derived files rebuild when the
  recipe changes and only then.

## 7. Errors, logging, recovery

- `CutroomError` subclasses: `ToolMissingError` (exit 3), `MediaError` (4), `JobError` (5),
  `RefusedError` (6). Each carries a `fix` string. FFmpeg failures map stderr patterns to
  fixes (missing file, corrupt media, missing encoder, disk full, out of memory).
- All job files are written atomically (temp file + rename): a crash never leaves a
  half-written manifest, transcript or proxy (`.partial` files are discarded).
- Resume after crash [P8]: stages are idempotent and recorded in `job.json`, so re-running
  a command continues where it stopped.

## 8. Security, privacy, licensing

- No uploads of client material, ever. Network use: package registries, HuggingFace model
  downloads (pinned revisions), FFmpeg/Node installers, free-licensed assets.
- Per-client separation: jobs never read another client's footage, transcripts or style.
- Every dependency, model, font and asset has a licence entry in `docs/LICENSES.md`;
  assets carry per-file licence metadata [P6].

## 9. Extensibility (§15 of the brief)

Plugin manifest + auto-registration [P1], `cutroom ffmpeg` passthrough [P1], custom
graphics compositions [P3], the self-extension rule (any new tool written during a job is
added to the library with a test and a line in `docs/TOOLS.md`), and `docs/CAPABILITIES.md`
as the honest map of can / partially / cannot-yet.

## 10. Phase plan

| Phase | Scope | Acceptance |
|---|---|---|
| 0 Foundations | Detection, charter, scaffold, setup scripts, doctor with test transcription | `doctor` passes; sample ingests, proxies and transcribes correctly |
| 1 Perception + transcript cutting | Timeline schema, validate/diff/show, analyze, remove-silence/fillers, detect-retakes, rough-cut render, click-free audio, EDITING_STANDARDS.md | 10-min talking head → clean rough cut with full change log; cuts on word boundaries; no clicks |
| 2 Audio + captions | Voice chain, loudness, 3 caption styles, SRT/VTT | Loudness on target; caption sync ≤ 80 ms; safe zones respected |
| 3 Motion graphics | Themeable component library, JSON specs, CLI hooks | Style switch changes the whole look; visual tests pass; no face overlap |
| 4 Review loop | Checks, dense sampling, report, autonomous fixes | Seeded defects all detected and fixed without regressions |
| 5 Shorts | Moment selection, reframing, hooks, captions | 5 self-contained, face-centred Shorts with safe captions |
| 6 B-roll, music, SFX, thumbnails | Free library + procedural fallbacks, ducking | Music ducks; b-roll aligned; licence log complete |
| 7 Orchestrator, styles, revisions, NLE export | End-to-end run, revision notes → timeline edits, OTIO/FCPXML | One instruction → full delivery package; revisions change only what they should |
| 8 Hardening | Batch queue, crash resume, profiling, cost report, golden regression suite | |

## 11. Proposed changes to the brief (need your decision)

1. **Graphics engine and the Remotion licence.** Remotion's licence (LICENSE.md, checked
   2026-11-07) is free for individuals, for-profit companies with **up to 3 employees**, and
   non-profits; larger companies need a paid Company License. Government bodies are not
   mentioned. If you qualify, Remotion is the best tool for this layer. If you do not (or are
   unsure), I propose an equivalent built from free parts: React/HTML/CSS graphics rendered
   frame-by-frame in headless Chromium via Playwright (Apache-2.0), composited by FFmpeg.
   That is roughly what Remotion does internally; it costs about one extra week of Phase 3
   work and loses Remotion's preview Studio.
2. **"LLM-assisted" steps run inside the agent session** (§4.8), not through an API.
3. **Speaker diarization without gated models.** pyannote's models are free but require a
   HuggingFace account and accepting terms per model. Default plan: separate tracks when
   available, otherwise a non-gated free model; pyannote stays optional.
