# Phase reports

One short report per phase: what was built, how to run it, what was tested, known limits, next step.

## Phase 0: Foundations (2026-11-07)

**Built**

- Repository scaffold (layout per `ARCHITECTURE.md` §3), pinned dependencies
  (`pyproject.toml` + universal `requirements.lock`), licence and capability registers.
- Setup for all three OSes: `setup.sh` / `setup.ps1` → stdlib-only `setup/bootstrap.py`
  (FFmpeg via winget/Homebrew/apt/pacman or a portable no-admin build; Python 3.12 venv;
  NVIDIA cuBLAS/cuDNN wheels when an NVIDIA GPU is present; Node check; doctor).
- `cutroom doctor`: platform, Python, packages, FFmpeg filters/encoders, working hardware
  encoders, RAM/disk, GPU/CUDA, Node, speech-model benchmark and selection
  (`--select-asr`), and an end-to-end smoke test on a bundled clip with known ground truth.
  Writes `config/machine.json`.
- Commands: `doctor`, `machine`, `models list/pull`, `new`, `jobs`, `status`, `probe`,
  `ingest`, `proxy`, `transcribe`, `docs`, `version` (reference: `docs/TOOLS.md`).
- Test assets: a synthetic clip (`doctor_sample.mp4`) with exact sentence timings, pauses
  of 0.4–2.0 s, an in-sentence stumble and a full-sentence retake; a 13.5 s public-domain
  real-speech clip with a natural stumble.

**How to run**: `./setup.sh` (or `.\setup.ps1`), then `cutroom doctor`, then
`cutroom new <job>` → `ingest` → `proxy` → `transcribe`.

**Tested**

- 61 automated tests (unit + integration, including real transcription), all passing.
- `cutroom doctor --select-asr` passes in the build container: benchmarked small.en and
  large-v3-turbo; selected large-v3-turbo; smoke test WER 1.5%, worst sentence-timing
  error 429 ms.
- Setup run for real in a fresh copy of the repo (new venv from the lockfile via pip, then doctor).
- Demo on real footage (5 min 50 s, 1080p, public-domain talking head): ingest + proxy +
  audio extraction + transcription in 2 min 28 s on 4 CPU cores. Proxy 960x540 at
  23.976 fps with 0 ms duration drift; 944 words, 18 flagged low-confidence; the decision
  log records each step.

**Known limits**

- Word timing errors up to ~400 ms (worst after pauses); captions need ≤ 80 ms. Fix in Phase 1.
- Whisper does not reliably transcribe fillers or stumbles; acoustic detection in Phase 1.
- Windows, macOS and NVIDIA paths are written but have not run on real hardware yet.
- `transcript.srt` is segment-level, not caption-phrased (Phase 2).

**Next: Phase 1** (perception + transcript-driven cutting): `docs/EDITING_STANDARDS.md`;
word-boundary refinement; acoustic filler/stumble detection; audio and visual reports;
frame sampler; the timeline schema with validate/diff/show/undo; remove-silence,
remove-fillers, detect-retakes; rough-cut render with click-free joins; first playbook prompts.
Acceptance: a 10-minute talking head becomes a clean rough cut with a full change log.
