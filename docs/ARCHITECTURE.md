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
| Versioned | Every meaningful change writes a new immutable timeline version (`versions/v001.json`, `v002.json`, …) with a changelog line, so any step can be undone or diffed. [built] |
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
| `graphics/` | Remotion package (React): captions and the motion-graphics component library [built]; `render.mjs` renders a composition to transparent PNGs (or listed stills) |
| `styles/` | `default.style.yaml` (editing parameters, caption rules and 4 caption styles, graphics design tokens and rules [built]) and `clients/<client>.style.yaml` (e.g. `demo-bold`) |
| `templates/` | JSON specs for the motion-graphics templates (parameters, facts, placement zones) [built] |
| `assets/` | Fonts, SFX, LUTs, music: each with recorded licence and source. `samples/` holds the synthetic test clip. |
| `prompts/` | The agent playbook: `00`–`04` [built]; `05`–`10` [P2–P7] |
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
analysis/S01/     audio_16k.wav, audio_48k.flac, transcript.{json,txt,srt}, words.json (refined),
                  envelope.npz, audio_report.json, visual_report.json, faces.json, pacing.json, sheets/*.jpg
analysis/         summary.md (read first), topics.task.md → topics.json, retakes.json
versions/         v001.json … immutable timeline versions, HEAD, CHANGELOG.md
renders/          vNNN_preview.mp4 / vNNN_final.mp4 + a .json report of the render's self-checks
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
- **Cut placement** [built]: cuts sit in pauses between refined word edges (with padding);
  where two words touch, at the quietest point between them (±40 ms); near one of the source's
  own camera changes, on that change (so the edit hides in it). At render time video edges snap
  to the output frame grid and audio uses exactly the same edges, with an 8 ms raised-cosine fade
  on both sides of every join, so joins never click and sync never drifts.

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
| Word-timing refinement | Word edges that border a pause are re-placed on the pause found in a 5 ms energy envelope (end = first silence after the word, start = last silence before it). Ground truth: 214/429 ms mean/max error → 17/70 ms. Forced alignment kept in reserve. | **[built]** |
| Filler detection | Verbatim ASR (transcribed fillers) plus audible regions inside pauses with no word, classified voiced (likely um/uh, by pitch periodicity) or unvoiced (breath) | **[built]** |
| Speakers | Multi-track recordings: one track per speaker (free and exact). Single track: speaker embeddings + clustering with a non-gated free model (e.g. sherpa-onnx), see §11 | [built] `cutroom speakers`: pyannote community-1 (gated, free, CC-BY-4.0) run locally with telemetry off; speaker per sentence by majority of time; used by `show`, `cut --speaker`, review and Shorts (CHARTER §5) |
| Audio report | `ebur128` (LUFS, LRA, true peak), envelope-based noise floor, speech level, SNR, clipping runs, silences | **[built]**; per-speaker levels and speech/music segmentation [P2] |
| Visual report | FFmpeg `scdet` shots; YuNet face detection via OpenCV (bounding box, mouth landmarks) on 1 fps samples; framing (headroom, centring, size); exposure judged on the face; sharpness; colour cast; contact sheets | **[built]**; OCR for screen recordings [P4/P5] |
| Pacing map | Per-second words, speech time, energy; smoothed boring score; slowest stretches | **[built]** |
| Topic map | Agent-in-the-loop task (see §4.8): numbered sentences + schema in `topics.task.md`; the agent writes `topics.json`; `cutroom topics --check` validates schema and coverage | **[built]** |
| Frame sampler | `cutroom frames <job> --at 12.4 --at 15.0 [--source S01]`: labelled grids at source or timeline times | **[built]** |

**Honest limit.** The agent perceives video through samples and reports, not continuous
playback. It can miss things that happen between samples (a one-frame glitch, a brief
gesture, subtle lip-sync drift). The review layer compensates with dense sampling around
every cut, graphic entrance/exit and transition, plus signal-level checks that look at
every frame (black/freeze/flash detection, audio discontinuities). What still cannot be
verified automatically is listed in every review report.

### 4.3 Project model [built]

One canonical JSON timeline per version, validated by pydantic (and exported as JSON
Schema in `docs/TIMELINE_SCHEMA.md`): `meta`, `sources[]`, `tracks[]`, `clips[]` (with
`reason`, `confidence`, `locked`), `graphics[]`, `captions[]`, `audio`, `markers[]`,
`outputs[]` (long-form and each Short derive their own timeline). Commands:
`validate`, `diff vA vB` (human-readable change summary), `show --format outline`
(timestamps, text, cuts with reasons, quiet/off-mic and low-confidence marks: the agent's main view
of the edit), `versions`, `undo`, `checkout`, `snapshot`. Field reference: `docs/TIMELINE_SCHEMA.md`
(generated); machine-readable schema: `docs/timeline.schema.json`.

### 4.4 Tool layer

- One Typer CLI (`cutroom …`) over a Python API. Every command: `--help`, `--json`
  (one object on stdout; progress on stderr), `--dry-run` where it changes anything,
  a changelog/log entry, idempotent where possible. Refuses destructive or invalid
  operations (e.g. cutting mid-word) unless `--force`.
- `docs/TOOLS.md` is generated from the CLI's own help (`cutroom docs`), and a test fails
  if it is stale, so the reference never drifts.
- **Plugins** [built]: a tool is a module in `src/cutroom/tools/` with a manifest (name,
  inputs, outputs, tests) that registers itself with the CLI and the docs generator; a test
  fails if a manifest names a test file that does not exist.
- **Escape hatches**: `cutroom ffmpeg …` passthrough [built] (runs in the job folder, logged,
  refuses to write to sources or outside the job), and custom graphics compositions when no
  template fits [P3] (added to the library with a spec and test).

### 4.5 Render layer [built]

- **Video**: clips are grouped into *runs* (consecutive clips of one source in increasing
  source order). Each run is one FFmpeg pass: conform to the output frame rate, then keep exact
  frame-number ranges (`select=between(n,a,b)`): one decode, no frame buffering, no drift.
  Runs are concatenated without re-encoding. Tested frame-exact by encoding frame numbers in
  pixel brightness.
- **Audio**: assembled sample-accurately in Python from the 48 kHz FLAC, with an 8 ms fade on
  both sides of every join; voice chain and loudness come in Phase 2. Sync tested to ≤ 3 ms.
- **YouTube-compliant MP4**: YouTube asks for no edit lists. Video uses negative composition
  offsets; the AAC encoder's 1024 priming samples are compensated by trimming the same amount
  from the (always silent) head of the audio. Moov atom at the front.
- **Self-checks** in every render's JSON report: duration and frame count match the timeline,
  no clicks at any join (linear-prediction error test), no edit lists, a list of *flash shots*
  (< 0.4 s); with a voice chain, loudness within ±1 LU of target and true peak under the ceiling
  (measured on the output file); with captions, every drawn caption pixel inside the safe zone
  (measured on the overlay frames) and caption appearance within 80 ms of speech onsets; with
  graphics, no head covered (≤ 2 %) and every graphic pixel inside the graphics safe area.
- **Captions and graphics** [built]: Python plans what to draw (props JSON); Remotion
  (React, `graphics/`) draws transparent PNG frames in headless Chrome (`graphics/render.mjs`,
  bundle cached by source hash); FFmpeg overlays them inside each video run, so no extra encode.
  FFmpeg stays the single master compositor; the graphics engine only draws. One "Overlay"
  composition draws every graphic (sorted by z) with captions on top; graphics that hide
  captions (kinetic text, title cards) trim the cues they overlap. Fully opaque frames are
  rewritten as RGBA so FFmpeg's input format never changes mid-stream.
- **Graphics** [built]: a graphic is `{template, variant, params, start, end, placement,
  facts, fact_check, attach, backing}` in the timeline. `graphic-add` validates parameters
  against the template spec, verifies every number against its references (transcript span or
  client document), chooses the anchor off the face and above the caption band, enforces the
  reading time and the 2-at-once rule, measures footage luminance for backing panels, and
  attaches the graphic to its source moment (ripple/move re-map it). After rendering, head
  coverage and safe-area containment are measured on the drawn pixels.
- **Reframing** [built]: per-clip punch-ins and pushes (`clips[].reframe`) are one FFmpeg
  `zoompan` per run with per-frame expressions, cropped from the full-resolution frame before
  the single scale (same aspect) or after it (letterboxed sources).
- **Audio polish** [built]: the voice chain stored in the timeline (`audio.voice_chain`, planned
  by `cutroom audio`) runs on the assembled edit audio before muxing: chain → linear gain to the
  loudness target → 4× oversampled limiter → measured; the final file is measured again.
- **Range renders** [built]: `render --range A-B` renders any slice (captions and graphics re-timed), for review.
- **Encoders**: previews use a working hardware encoder if `doctor` found one
  (NVENC / VideoToolbox / QSV / AMF / VAAPI), otherwise libx264 `veryfast`. Finals use
  libx264 (`slow`, CRF ~16–18, High profile, yuv420p, BT.709) because it gives the best
  quality per bit; YouTube re-encodes anyway, so a clean high-bitrate master matters more
  than encode speed. Trade-off: finals take longer on slow machines.
- **Previews** burn in timecode and clip labels so frames in review reports can be traced.
- **Resume and timings** [built, P8]: a render works in `renders/.tmp_vNNN_<mode>/` with a
  `state.json` keyed to a hash of the exact plan (the timeline without its change log, mode,
  range, encoder, captions, voice chain, Cutroom version and a hash of the Remotion sources).
  Finished stages are recorded as they complete (edit audio and mix, overlay frames, each
  encoded run). If the render fails or is interrupted the folder is kept and the same command
  resumes: audio is reused, overlay frames continue from the first missing one (the last one
  found is redrawn, it may be truncated), encoded runs are skipped. Any other plan, or a
  corrupt state file, starts clean. The folder is removed only after a successful mux. Every
  report carries `timings_s` per stage.

### 4.6 Review layer [built]

`cutroom review <job> [--version vNNN]`: preview render (reused when that exact version was
rendered: versions are immutable) → automatic checks (`review/checks.py`: audio, cuts, captions,
graphics, video, structure; thresholds from the style file) → contact sheets (a frame every 4 s
plus a frame at every issue) that the agent opens and judges → `review/vNNN/report.md` and
`review.json` with issues by severity (blocker/major/minor/polish), evidence and a fix command
→ scorecard (0–10 per category; ready = no blocker and no major).
Checks reuse the editing tools' own planners on a copy of the timeline (e.g. "pauses left" =
what `remove-silence` would really remove now), so a check and its fix never disagree.
`review --fix` (`review/fixes.py`) applies the safe fixes as ordinary commands in dependency
order (pauses and fillers → flash shots recomputed on the new edit → punch-ins → graphics →
captions → audio), each a new version, and repeats up to 4 rounds; judgement calls (a flash shot
holding a word, a weak opening, a graphic without a source) are left with a manual fix command.

### 4.6b Shorts [built]

A Short is a derived job `<job>.short-NN` (`shorts/derive.py`): its manifest names the parent,
its sources point at the parent's media, its `analysis/` is hard-linked from the parent, and its
timeline (1080x1920, output `youtube_short`) is the parent's kept material inside a source range,
in speech order. Framing (`shorts/reframe.py`) is stored per clip: crop keyframes (`reframe`,
crop centre x) planned per source shot from the face track (still / speed-limited follow / jump at
camera changes), or the `vertical_fill` effect (whole frame fitted over a blurred copy). The
renderer turns a run's keyframes into one `crop` filter with per-frame expressions, and a fill run
into a split/blur/overlay graph; runs break where the layout changes. Moments come from
`shorts/find.py` (whole-sentence windows scored with reasons), the hook from `short-hook`
(headline graphic, fill opening when needed), publishing text from an agent task checked by
`shorts/publish.py`. Graphics placement and the review's face checks map face boxes through each
clip's crop (`output_face_mapper`), and use the Shorts safe area.

### 4.6c Media: b-roll, music, effects, thumbnails [built]

`assets/library/<kind>/` holds licence-tracked stock (sidecar per file; allowlist in
`assets/library.py`; Commons fetcher). `assets/synth.py` generates music beds and effects.
Timeline: `broll[]` (cutaways with Ken Burns motion, attached to content) and
`audio.music[]` / `audio.sfx[]` (`AudioBed`). Render: cutaways are pre-rendered to exact frames
and overlaid in the runs under the overlay; beds are decoded, levelled against the measured
speech, ducked with a look-ahead envelope built from the word timings, and mixed between the
voice chain and mastering (`audio/mix.py`); the report carries the measured speech-to-music
ratio. `thumbnails.py` picks and recomposes face frames and checks the Remotion-drawn result.
`reference.py` turns a reference video's measurements into a style file.

### 4.7 Style layer [built]

`styles/*.style.yaml` holds editing parameters [built] (pause lengths, pads, filler policy,
retake thresholds, join fades, pacing and loudness targets), caption styles, and graphics design
tokens [built] (colours, fonts, weights, sizes, radius, spacing, shadow, motion timing, safe area)
plus graphics rules (reading time, simultaneity, punch-in scale, backing threshold). Client styles
deep-merge over the default; `cutroom style --client-style` switches an edit's theme.
`analyze-reference` turns a reference video's measured pauses and pacing into a style file [built].
`docs/EDITING_STANDARDS.md` is the human-readable, cited rationale behind the defaults [built].

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

### 4.9 Export, publishing, revisions, delivery [built]

- **NLE export** (`export/nle.py`): OTIO via OpenTimelineIO 0.17 (Apache-2.0; V1, V2 b-roll, A1
  dialogue, A2 music, A3 effects, markers for chapters and graphics with their parameters);
  FCPXML 1.10 and CMX3600 EDL written directly (OTIO's FCP/EDL adapters are separate plugins).
  All on the renderer's frame grid; each file is read back and compared with the timeline
  (clips, source ranges, total frames) before it is reported. Premiere: the EDL (picture and
  dialogue) or the OTIO via Resolve.
- **Publishing** (`publish.py`): chapters proposed from the topic map; the agent writes
  `publish.json`; the check enforces YouTube's chapter and length rules, sourced numbers and
  credits; chapters become timeline markers; `delivery/description.txt`.
- **Revisions** (`revise.py`): notes → times mapped from the watched version through the source
  to the current edit, with context → the agent's plan (commands or responses per note) → applied
  as versioned edits → note-by-note report.
- **Delivery** (`deliver.py`): readiness check (final render checks, review, publishing text,
  thumbnail, every Short), package with manifest, report and checksums, AWAITING APPROVAL until a
  person runs `approve`. `cutroom next` reads a job and names the next stage.

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
- Resume after crash [built, P8]: analysis stages are idempotent and recorded in `job.json`;
  renders resume their own finished parts (§4.5); a batch file (`cutroom batch`) records each
  line's outcome next to the file and skips finished lines when run again.
- Child processes (FFmpeg; Node and the Chrome it starts for Remotion) run in their own process
  group; an interrupted or failed command kills the whole group, so nothing keeps writing a file
  that a resumed command is about to rewrite.
- Command log: every job command (any registered tool whose first argument is the job) is
  recorded in `logs/commands.jsonl` with status and seconds; `cutroom report` turns it, the
  render timings and the review into `logs/report.md` with the cost ($0) and network use.

## 8. Security, privacy, licensing

- No uploads of client material, ever. Network use: package registries, HuggingFace model
  downloads (pinned revisions), FFmpeg/Node installers, free-licensed assets.
- Per-client separation: jobs never read another client's footage, transcripts or style.
- Every dependency, model, font and asset has a licence entry in `docs/LICENSES.md`;
  assets carry per-file licence metadata [P6].

## 9. Extensibility (§15 of the brief)

Plugin manifest + auto-registration [P1], `cutroom ffmpeg` passthrough [P1], new graphics
templates (a component + a JSON spec + a golden image, `templates/README.md`) [P3], the self-extension rule (any new tool written during a job is
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
| 8 Hardening | Batch queue, crash resume, profiling, cost report, golden regression suite | Interrupted renders resume frame-exact; a raw clip reaches a package awaiting approval through the CLI and matches the golden fingerprint |

## 11. Changes to the brief and decisions

1. **Graphics engine: Remotion (decided 2026-11-07).** You work as an individual, so
   Remotion's free licence applies. The graphics layer stays behind a narrow interface
   (template id + props in, transparent overlay file out), so if the business grows past
   3 employees it can be swapped for a free equivalent (React/HTML rendered in headless
   Chromium via Playwright, Apache-2.0) without touching the rest of the system.
2. **"LLM-assisted" steps run inside the agent session** (§4.8), not through an API.
3. **Speaker diarization without gated models.** pyannote's models are free but require a
   HuggingFace account and accepting terms per model. Default plan: separate tracks when
   available, otherwise a non-gated free model; pyannote stays optional. **Status after
   Phase 8: not built** (CHARTER 4.11). **Then built (2026-11-08)** with pyannote's gated
   community-1 model once you created the account and accepted its terms; validated on a
   two-voice dialogue with known answers and on the interview's off-mic question.
