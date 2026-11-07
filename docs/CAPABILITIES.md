# Capabilities

An honest map of what Cutroom can do today, what it can do partially, and what it cannot
do yet, with the plan for each gap. Updated at the end of every phase.

Last updated: Phase 0 (foundations).

## Can do (built and tested)

| Capability | Command | Notes |
|---|---|---|
| Check the whole toolchain on a machine, end to end | `cutroom doctor` | Includes a real ingest → proxy → transcribe run on a known clip |
| Pick the best speech model for the machine by measurement | `cutroom doctor --select-asr` | Speed, memory, word accuracy, timing accuracy, stumble retention |
| Inspect any media file | `cutroom probe` | Rotation, SAR, VFR, HDR, bit depth, stream offsets, timecode |
| Create and track jobs | `cutroom new`, `jobs`, `status` | Manifest, brief template, decision log, command log |
| Ingest footage safely | `cutroom ingest` | Copy + hash in one pass, read-only copies, de-duplication, bad files reported |
| Proxies and analysis audio | `cutroom proxy` | 540p CFR proxies (HDR tone-mapped), 16 kHz WAV + 48 kHz FLAC |
| Local English transcription with word timestamps | `cutroom transcribe` | Verbatim mode, hotwords, confidence marks, JSON + readable text + SRT |
| Manage speech models | `cutroom models list/pull` | Pinned revisions, all MIT |

## Partially

| Capability | Gap | Plan |
|---|---|---|
| Word timing | Errors up to ~400 ms (worst on the first word after a pause) | Phase 1: snap word edges to the audio envelope; forced alignment if needed. Target ≤ 80 ms for captions. |
| Filler/stumble transcription | Whisper drops "um/uh" and sometimes repetitions | Phase 1: acoustic detection of voiced, untranscribed audio; optional second verbatim pass |
| Windows / macOS / NVIDIA GPU | Code paths written but not yet run on real hardware | Verified on your first `setup` + `doctor` run |
| SRT output | Segment-level, not caption-phrased | Phase 2 caption engine |

## Cannot do yet

| Capability | Phase |
|---|---|
| Timeline model, validate/diff/show/undo | 1 |
| Silence/filler/retake removal, rough-cut render, click-free joins | 1 |
| Audio report, visual report, pacing map, topic map, frame sampler | 1 |
| Voice chain, loudness normalisation, animated captions (3 styles), SRT/VTT | 2 |
| Motion-graphics library (lower thirds, callouts, kinetic text, charts, transitions…) | 3 |
| Automatic review loop with scorecard | 4 |
| Shorts: moment selection, 9:16 reframing with face tracking | 5 |
| B-roll, music, SFX, thumbnails | 6 |
| Orchestrator, client styles, revision notes, OTIO/FCPXML export | 7 |
| Batch queue, crash resume, golden regression suite | 8 |
| Multi-camera sync, green screen, picture-in-picture, split screen, speed ramps, cursor-following zooms, stabilisation, colour matching, sponsor segments, A/B thumbnails | Scheduled across Phases 1–7; each gets a row here when built |
