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

## Phase 1: Perception and transcript-driven cutting (2026-11-07)

**Built**

- Timeline model (`cutroom.timeline/1`) with immutable versions, HEAD/undo/checkout/snapshot,
  validate, diff and the outline view; schema docs generated from the code.
- Perception: word-boundary refinement against the audio; untranscribed voiced sounds (likely
  fillers) and breaths; audio report (EBU R128 loudness, true peak, noise floor, SNR, clipping,
  silences); visual report (shots, faces, framing, face-based exposure, sharpness, colour cast,
  contact sheets); pacing map; footage-type guess; topic map as an agent task; frame sampler;
  `cutroom analyze` writes a one-page `analysis/summary.md`.
- Cutting: `remove-silence`, `remove-fillers`, `detect-retakes`, `cut` (ranges or whole
  sentences), `restore`, `move` (reorder, e.g. cold opens), `rough-cut` (one command, one version per step).
- Render: frame-exact video runs, sample-accurate click-free audio, YouTube-compliant MP4
  (no edit lists, priming compensated), self-checks in every render report.
- Tool plugin registry, logged FFmpeg passthrough, `docs/EDITING_STANDARDS.md` (cited),
  playbook prompts 00–04, default style file.

**Acceptance run** (10-min talking-head interview: "Dan Odess – Trackways Interview", National
Park Service, public domain, 1080p, two-camera program cut). Followed `prompts/00`–`04` as the editor:

| Step | Result |
|---|---|
| `analyze` | 1,489 words; 278 pause edges refined; 20 untranscribed voiced sounds; 27 shots (wide/close alternating); footage type talking_head (0.8); audio −20.6 LUFS, peak −5.8 dBTP, SNR 41.9 dB; no visual flags after face-based exposure |
| Topic map | 11 topics written by the agent, validated (`topics --check`) |
| `rough-cut` | 70 long pauses shortened (53.9 s), 6 acoustic fillers, 12 stumbles/false starts (5.9 s); nothing meaningful cut |
| Editorial cuts | Reply to off-camera prompt, name spelling + interviewer question, closing chatter (`cut --sentences`) |
| Cold open | Key finding moved to the start (`move --sentences 8-9 --to 0`); the join lands on a camera change |
| Result | 10:16.0 → 9:07.8; 84 clips; `validate` passes (no mid-word cuts) |
| Preview render | 72 s; 16,417/16,417 frames; 0 clicks at 83 joins; no edit lists |
| Final render (1080p, x264 slow CRF 17) | 10 min 41 s on 4 CPU cores (≈1.2× real time); 243 MB; all checks pass |
| Visual inspection | Frames around the cold-open join and its hole checked by eye; found flash shots → fixed by snapping cuts to camera changes (4 → 2) |

**Tested**: 115 automated tests (all passing), including frame-exact rendering (frame numbers encoded in
brightness), A/V sync ≤ 12 ms (measured ≤ 3 ms), click detection (hard cuts detected, fades
remove them), editorial retake rules from real speech, version store, plugins, playbook commands.

**Known limits**

- 2 flash shots remain where the source's own camera change falls mid-speech: fix needs an L-cut or punch-in (Phases 3–4).
- No loudness normalisation yet (the rough cut keeps the source's −20.6 LUFS; Phase 2 brings it to −14).
- Speakers are not separated (interviewer questions found by reading; diarization in Phase 5).
- Footage-type detection is heuristic; screen-recording detection untested on real captures.
- Windows/macOS/GPU paths still unrun on real hardware.

**Next: Phase 2** (audio polish and captions): voice chain, loudness normalisation, three
animated caption styles with Remotion, SRT/VTT export. Acceptance: loudness on target, caption
sync ≤ 80 ms, no caption in unsafe zones.

## Phase 2: Audio polish and captions (2026-11-07)

**Built**

- Remotion graphics package (`graphics/`, pinned, fonts bundled offline) and its bridge: Python
  plans, Remotion draws transparent frames in headless Chrome, FFmpeg composites them inside the
  normal video pass (no extra encode). You chose Remotion over libass for captions.
- Caption engine with 4 styles (`clean`, `bold-karaoke`, `word-highlight`, `minimal`): karaoke
  word highlight, word pop, emphasis (≤ 1 per caption), entrance animations, automatic contrast
  scrim, placement off the mouth, long-form and Shorts safe zones.
- Caption planner: optimal sense-based phrasing per sentence (no dangling function words or
  pronouns; names and numbers kept with their words; breaks before function words; mid-phrase
  pauses do not split phrases), clean verbatim (fillers out, sentence starts capitalised).
- `.srt` / `.vtt` export; range renders (`render --range A-B`).
- Voice chain planned from measurements (`cutroom audio`): high-pass, denoise only when needed,
  gentle EQ, compression from the measured speech level, de-ess; loudness by linear gain and a
  4× oversampled limiter with encoder headroom; `cutroom loudness`.
- Render self-checks: loudness on target, true peak, captions inside the safe zone (measured on
  drawn pixels), caption sync against speech onsets.
- Setup installs the graphics package and Chrome Headless Shell; doctor renders test captions.
- Playbooks 05 (audio) and 06 (captions).

**Bugs found and fixed on the way** (all now have regression tests)

- Timeline time drifted from rendered time by up to 142 ms after 98 clips (frame rounding
  accumulated): the timeline now lays clips out on the render's frame grid (error 0.000 ms).
- Whisper reported no gap where there was a 0.26 s pause: refinement now finds hidden pauses.
- Dynamic loudness normalisation + AAC gave −0.5 dBTP: replaced by gain + oversampled limiter.

**Acceptance run** (the 10-minute NPS interview, edited end to end from raw footage):

| Check | Target | Result |
|---|---|---|
| Loudness | −14 LUFS ±1 | **−14.2 LUFS** |
| True peak | ≤ −1 dBTP | **−1.4 dBTP** |
| Caption sync | within 80 ms | **97.7 %** of 86 measurable captions; median 18.6 ms, p95 42 ms (15 with an inaudibly quiet first word and 3 whose onset is an edit join are reported as unmeasurable) |
| Captions in safe zone | none outside | **0 of 1,180** checked frames outside |
| Lines ending on a dangling word | 0 | **0** of 178 captions |
| Frames / clicks / edit lists | exact / 0 / none | 15,658 of 15,658; 0 clicks at 133 joins; none |
| Render time (9 min, 540p preview with captions) | | 6 min 50 s on 4 cores |

**Known limits**

- Reading speed: this speaker talks fast; 66 of 178 captions exceed 17 chars/s (median 15.7).
  Captions follow speech; condensing them would need agent-edited text (planned option).
- Remotion draws every frame: captions add ~4–5 minutes to a 9-minute render on 4 cores.
- EQ is a gentle fixed curve; no hum removal or music separation yet.

**Next: Phase 3** (motion graphics library).
