# Capabilities

An honest map of what Cutroom can do today, what it can do partially, and what it cannot
do yet, with the plan for each gap. Updated at the end of every phase.

Last updated: Phase 1 (perception and transcript-driven cutting).

## Can do (built and tested)

| Capability | Command(s) | Notes |
|---|---|---|
| Verify the toolchain end to end; pick the speech model by measurement | `doctor`, `doctor --select-asr` | Speed, memory, word accuracy, timing, stumble retention |
| Ingest footage safely | `ingest` | Copy + hash, read-only copies, de-duplication, bad files reported |
| Proxies and analysis audio | `proxy` | 540p CFR proxies; 16 kHz WAV + 48 kHz FLAC |
| Local transcription with word timestamps | `transcribe` | Verbatim mode, hotwords, confidence marks |
| **Edit-ready word timing** | `analyze` (words.json) | Pause edges refined against the audio: 17 ms mean / 70 ms worst error on ground truth (was 214 / 429 ms) |
| Audio report | `analyze` | Integrated loudness, LRA, true peak, noise floor, speech/noise ratio, clipping runs, silences |
| Visual report | `analyze` | Shots (incl. the source's own camera changes), faces and framing, face-based exposure, sharpness, colour cast, contact sheets |
| Pacing map | `analyze` | Words/min, speech coverage, per-second energy, slowest stretches |
| Footage-type guess | `analyze` | talking head / podcast / screen recording / vlog / mixed, with evidence (agent confirms by looking) |
| Topic map (agent-in-the-loop) | `topics`, `topics --check` | Task file → agent writes topics.json → schema + coverage check |
| Visual evidence on demand | `frames` | Labelled grids at source or timeline times |
| Versioned timeline | `assemble`, `versions`, `undo`, `checkout`, `snapshot`, `validate`, `diff`, `show` | Immutable versions; undo branches; outline is the agent's view of the edit |
| Remove silence | `remove-silence` | Natural pauses kept (longer after sentences), possible dramatic pauses protected, breaths never split |
| Remove fillers | `remove-fillers` | um/uh always; discourse markers optional and only when set off by commas; untranscribed voiced sounds in pauses |
| Retakes, stumbles, false starts | `detect-retakes` | Keeps the last complete take; protects parallel structure, lists, spelled names, interjections; number corrections left to a person |
| Manual edits | `cut` (range or `--sentences`), `restore`, `move` (reorder, e.g. cold open) | Refuses mid-word cuts; cuts snap to quiet points and to the source's camera changes |
| One-command first pass | `rough-cut` | assemble → silence → fillers → retakes → preview, one version per step |
| Render | `render --mode preview|final` | Frame-exact, sample-accurate, click-free joins, YouTube-compliant MP4 (no edit lists), self-checks (duration, frames, clicks, flash shots) |
| Escape hatch | `ffmpeg` | Any FFmpeg command inside the job, logged, sources protected |
| Plugins | `src/cutroom/tools/*.py` | Manifest + auto-registration + required tests |

## Partially

| Capability | Gap | Plan |
|---|---|---|
| Flash shots where the source's own camera change falls mid-speech | Detected and reported, not fixed (2 in the 10-minute test) | L-cuts and punch-ins (Phases 3–4) |
| Speaker identification | Off-mic voices hinted by level (⟪quiet⟫); interviewer questions identified by the agent from content | Diarization (speaker embeddings, non-gated free model) in Phase 5 |
| Filler/stumble transcription | Whisper drops some; acoustic detection catches voiced sounds in pauses, not fillers fused into words | Measure on more real footage; optional second verbatim pass |
| Footage-type detection | Heuristic; screen-recording detection untested on real screen captures | Test on real samples; add OCR text density |
| Windows / macOS / NVIDIA GPU | Written to official docs, not yet run on real hardware | Your first `setup` + `doctor` run |
| Jump-cut disguise | Mid-sentence jump cuts are left as plain cuts | Punch-ins (Phase 3) |

## Cannot do yet

| Capability | Phase |
|---|---|
| Voice chain (denoise, EQ, compression, de-ess), loudness normalisation to −14 LUFS | 2 |
| Animated captions (3 styles), SRT/VTT caption files | 2 |
| Motion-graphics library (lower thirds, callouts, kinetic text, charts, transitions, CTAs) | 3 |
| Automatic review loop with scorecard and autonomous fixes | 4 |
| Shorts: moment selection, 9:16 reframing with face tracking, safe-zone captions | 5 |
| B-roll, music, SFX, thumbnails | 6 |
| Client styles, revision notes → timeline edits, OTIO/FCPXML export, delivery package | 7 |
| Batch queue, crash resume, golden regression suite | 8 |
| Speed ramps, stabilisation, colour matching, multicam sync, green screen, PiP, split screen, cursor-following zooms, sponsor segments, A/B thumbnails | Scheduled across Phases 2–7 |
