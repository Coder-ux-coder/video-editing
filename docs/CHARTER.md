# Cutroom project charter

*Working name: "Cutroom". Tell me if you want a different name; it is used in the CLI
command, the Python package and the docs.*

Last updated: 2026-11-07 (Phase 2).

## 1. Mission

An agent-native video editing system. You (a founder, not an editing specialist) bring raw
footage and a short brief; the agent (Claude) produces a professional, client-ready YouTube
long-form video plus Shorts, iterating on its own drafts; you approve the final result.
Professional editing standards are encoded in the system (`docs/EDITING_STANDARDS.md`,
style files, automatic QA), not supplied by you.

## 2. Fixed decisions (from the brief) and how they are implemented

| # | Decision | Implementation |
|---|---|---|
| 1 | OS unknown: detect and adapt | `setup.sh` (macOS/Linux) and `setup.ps1` (Windows) find or install Python 3.12, then a stdlib-only `setup/bootstrap.py` installs FFmpeg (winget / Homebrew / apt / pacman, or a portable build without admin rights), creates `.venv`, installs pinned dependencies and runs `cutroom doctor`. All code uses `pathlib`; manifests store forward-slash relative paths; Windows long-path support is checked; every data location can move to another drive via environment variables. |
| 2 | 16 GB RAM, GPU unknown | `cutroom doctor` detects CPU, RAM, GPU (NVIDIA via `nvidia-smi`, Apple Silicon, Intel/AMD), test-encodes each hardware encoder, benchmarks speech models, and writes `config/machine.json`. Memory budget = min(60% of RAM, RAM − 4 GB) = 9.4 GB on 16 GB. Speech models refuse to load if free RAM is short (clear message instead of an out-of-memory crash). |
| 3 | English only | English-only Whisper models where they help (`small.en`), `language="en"` always, English text normalisation and number spelling for accuracy checks. Filler/pacing/caption rules are English (Phase 1–2). |
| 4 | All footage types, detected | `cutroom analyze` guesses talking head / podcast / screen recording / vlog / mixed from speech coverage, face presence, multiple faces, shot rate and sharpness, with the evidence; the agent confirms by looking. Per-type strategy in `EDITING_STANDARDS.md` §3. |
| 5 | YouTube only: 16:9 long-form (1080p, 4K if source allows) and 9:16 Shorts 1080x1920 | Output presets in Phase 1–5; thumbnails 1280x720, chapters, titles/descriptions, captions files. |
| 6 | Zero budget, local models only | Every dependency so far is free and permissively licensed (`docs/LICENSES.md`). Speech recognition runs locally. No cloud services. Claude is the only intelligence in the loop (agent-in-the-loop tasks, `ARCHITECTURE.md` §4.8). |
| 7 | Export to Resolve/Premiere | OpenTimelineIO + FCPXML/EDL adapters in Phase 7. |
| 8 | Folder layout: my call | As in the brief, documented in `ARCHITECTURE.md` §3. |
| 9 | Reference videos in `references/` | `analyze-reference` → `styles/<name>.style.yaml` (Phase 6); professional defaults until then. |
| 10 | Exceptional quality | Where there is a quality/speed trade-off I take quality and say so (e.g. software x264 for finals). |

## 3. Detected environment (where Phase 0 was built and tested)

Phase 0 was built and tested in a **cloud container**, not on your computer. The table below
describes that container. Your own machine is detected when you run setup: `cutroom doctor`
repeats every check and benchmark there and writes its own `config/machine.json`.

| Item | Detected |
|---|---|
| OS | Ubuntu 24.04.5 LTS, x86_64 (container) |
| CPU | Intel Xeon @ 2.10 GHz, 4 cores / 4 threads |
| RAM | 15.7 GB (the brief's "16 GB" class) |
| GPU | None; no `/dev/dri`; CPU-only |
| Hardware video encoders | NVENC/QSV/VAAPI compiled into FFmpeg, but none usable (no hardware); software libx264 |
| FFmpeg | 6.1.1 (Ubuntu build) with libx264, libx265, libass, freetype, rubberband, vidstab, zimg, all required filters |
| Python | 3.12.3 in `.venv` (system default was 3.13) |
| Node.js | 22.23.4 (needed from Phase 3) |
| Disk free | ~24 GB |

**Tested here:** Linux paths, CPU transcription, all FFmpeg operations, the whole test suite.
**Not testable here (no hardware):** Windows and macOS setup scripts, NVIDIA GPU
transcription, hardware encoders. They are written to official instructions but unverified
until you run setup on your machine. Please send me the `cutroom doctor` output.

## 4. Decisions I made (with evidence)

### 4.1 Platform and dependencies

| Decision | Why |
|---|---|
| Python **3.12** only | Wheels exist for every planned dependency (CTranslate2, onnxruntime, MediaPipe, OpenCV, torch). 3.13 support is still patchy for some vision libraries. |
| Pin everything: `requirements.lock` (all transitive deps, all OSes) + exact model revisions | Reproducibility. Proven necessary on day one: the newest PyAV (17+) silently broke faster-whisper's audio decoding; pinned `av==16.1.0` and decode audio ourselves with FFmpeg as well. |
| FFmpeg as the single decoder and assembler | One tool, every format, sample-accurate audio, mature filters (loudness, silence, denoise, ducking). |
| Typer CLI with `--json` on every command; `docs/TOOLS.md` generated from the CLI | The agent needs stable machine-readable output; docs that cannot drift (a test fails if they do). |

### 4.2 Speech recognition (the engine and model, proven on samples)

Engine: **faster-whisper 1.2.1** (CTranslate2), local, MIT licence. Chosen over WhisperX
for Phase 0 because it needs no PyTorch (smaller, simpler install on all three OSes).
WhisperX-style forced alignment may still be added in Phase 1 if timing measurements need it.

Benchmark on this machine, 5 min 50 s of real speech (public-domain White House address,
1080p talking head), CPU int8, 4 threads. WER is measured against YouTube's automatic
captions, which are themselves imperfect, so treat it as "disagreement", not true error:

| Model | Speed (processing time / audio time) | Peak RAM | WER vs YouTube | Kept the speaker's stumble "this is, this is"? |
|---|---|---|---|---|
| small.en (verbatim prompt) | 0.19 | 1.0 GB | 3.9% | **yes** |
| small.en (no prompt) | 0.19 | 0.8 GB | 4.8% | no, and invented an "and" |
| medium.en | 0.47 | 2.6 GB | 4.8% | no; also hallucinated "Good morning" |
| large-v3-turbo (verbatim prompt) | 0.24 | 2.0 GB | 4.4% | no |
| distil-large-v3 | 0.19 | 1.9 GB | 4.9% | no; dropped the first word |

Doctor benchmark (`cutroom doctor --select-asr`), on two bundled clips with known content:

| Model | Speed | Words wrong (synthetic / real) | Worst sentence-timing error | Kept stumble (synthetic / real) | Peak RAM |
|---|---|---|---|---|---|
| small.en | 0.19 | 1.5% / 0% | 400 ms | yes / yes | 0.9 GB |
| large-v3-turbo | 0.33 | 1.5% / 0% | 429 ms | yes / yes | 2.6 GB |

Conclusions:

1. **Selected for this machine: `large-v3-turbo`.** Both finalists are within the speed budget
   (≤ 0.6× real time, so a 10-minute video transcribes in under ~4 minutes here), have equal
   word accuracy and equal timing on the test clips; turbo is the stronger model on difficult
   audio (accents, noise, jargon) according to public benchmarks. Selection rule, coded in
   `doctor.py`: keep stumbles → best word timing (50 ms tolerance) → general accuracy → speed.
2. **medium.en is never the right choice on CPU**: turbo is both faster and more accurate.
3. **Verbatim prompting is on by default**: without it, Whisper "cleans up" speech, which
   hides exactly the stumbles and repetitions an editor must cut.
4. **No Whisper model reliably transcribes fillers or stumbles.** None of them output a single
   "um"/"uh" on 6 minutes of speech, and turbo kept a stumble in a short clip but dropped the
   same stumble in the full video. Phase 1 must therefore detect fillers/stumbles acoustically
   (voiced audio with no transcribed word) and may add a second, cheap verbatim pass.
5. **Word timings need refinement before they can drive cuts and captions.** Measured errors
   are up to ~400 ms, mostly on the first word after a pause (Whisper starts it early).
   The brief's caption target is ≤ 80 ms, so Phase 1 adds a boundary-refinement pass
   (snap word edges to the audio's energy envelope; forced alignment if that is not enough).
6. On an NVIDIA GPU with ≥ 8 GB VRAM the profile picks `large-v3` (float16) instead.

### 4.3 Media handling

| Decision | Why / trade-off |
|---|---|
| Ingest **copies** footage into the job (read-only) by default; `--reference` reads in place | Self-contained, safe jobs; costs disk space (offered as an option). Files are hashed (BLAKE2b) in the same read pass as the copy, de-duplicated, and never modified. |
| Proxies: 540p short side, never upscaled, constant frame rate at the exact rational source rate, 1-second GOP, libx264 CRF 23 | Fast analysis and frame grabs; CFR fixes phone-video audio drift; libx264 is identical on every OS. |
| Audio extracted twice: 16 kHz mono WAV (what the speech model hears) and 48 kHz FLAC (lossless, for loudness/noise analysis) | Word times map 1:1 to source time; analysis is lossless. |
| Previews may use a hardware encoder; **finals always use software libx264** | Best quality per bit for the master YouTube re-encodes. Trade-off: finals are slower on weak machines. |
| HDR sources are tone-mapped to SDR Rec.709 | YouTube SDR delivery is the safe default; HDR delivery could be added if a client needs it. |


### 4.4 Phase 1 decisions

| Decision | Why / evidence |
|---|---|
| Refine word timings against the audio instead of adding a forced-alignment model now | Pause-edge refinement on a 5 ms energy envelope brought sentence-edge error from 214/429 ms (mean/max) to 17/70 ms on ground truth, for both speech models, with no extra model or PyTorch. Forced alignment stays in reserve if real footage shows otherwise. |
| Cuts live only in pauses; touching words split at the quietest point; cuts move onto the source's own camera changes when one is within 0.45 s | Reading the 10-minute test edit frame by frame showed jump cuts followed by 0.1–0.3 s flashes of the old angle; snapping halved them (4 → 2). The 2 left need L-cuts/punch-ins (Phases 3–4) and are reported by every render. |
| Retakes: keep the last complete take; parallel structure, lists, spelled names, interjections never count; number corrections always go to a person | The first detector version wrongly flagged "how to document it and how to preserve it", "10,000 years ago or 5,000 years ago", "O-D-E-S-S" and "oh, oh, wow" on real speech, and would have deleted "fresh" from "human tracks, fresh human tracks". All are now regression tests. |
| Exposure judged on the face | White Sands footage (bright sand, well-lit face) was falsely flagged as overexposed by whole-frame metrics. |
| Audio assembled in Python (soundfile, BSD) with 8 ms raised-cosine fades at every join | Sample-accurate, click-free (measured), and keeps A/V sync exact; FFmpeg filters for this would buffer frames or drift. |
| MP4s without edit lists, AAC priming compensated | YouTube's upload spec asks for no edit lists; naive removal would delay audio 21 ms. Measured sync after the fix: ≤ 3 ms. |
| YuNet (via OpenCV, Apache-2.0; model MIT, 232 KB) for faces instead of MediaPipe | Much lighter install, same job for Phase 1 (presence, framing, mouth region). MediaPipe may still be evaluated for Phase 5 reframing. |
| Reorder (`move`) and sentence-level `cut` built in Phase 1 | Needed for a real cold open in the acceptance edit; small given the timeline model. |

### 4.5 Phase 2 decisions

| Decision | Why / evidence |
|---|---|
| Captions are drawn by Remotion (your choice, 2026-11-07) | You chose Remotion over libass when asked. Measured cost on 4 cores: 1,800 transparent 1080p frames in 42 s (43 fps) and 136 MB of temporary PNGs per minute. Captions render once per render pass and are composited by FFmpeg in the same encode (no extra encode). |
| Caption phrasing by an optimal split, not "fill up to N words" | Greedy filling produced "…welcome to / the test clip", "400 / grams", "White / Sands". The planner now scores every possible split of each sentence: no dangling function words or subject pronouns at line/caption ends, names and number+unit kept together, breaks preferred before function words and at commas, mid-phrase pauses do not split a phrase. On the 9-minute interview: 179 captions, 0 lines ending on a dangling word. |
| Word refinement also checks words Whisper reports as touching | A 0.26 s pause between sentences was unreported, which made the first caption 265 ms early. Refinement now looks for hidden pauses ≥ 120 ms (longer than stop consonants). Ground-truth accuracy unchanged (17 / 70 ms). |
| Voice chain planned from measurements, loudness in two passes | Denoise only when speech/noise < 35 dB (the interview: 41.9 dB → no denoise); compressor threshold 8 dB under the measured speech level; loudnorm two-pass, linear gain when the true-peak ceiling allows. Internal target −1.5 dBTP so AAC encoding stays under −1 dBTP. |
| Range renders (`render --range A-B`) | Reviewing a style or a fix should not cost a full render. |

### 4.6 Phase 3 decisions

| Decision | Why / evidence |
|---|---|
| Graphics are data: template id + parameters + placement + references in the timeline, drawn by Remotion components | The agent adds a lower third with one command and no React; the same edit restyles from design tokens (`style --client-style`). 14 templates, 35 variants. |
| One Remotion "Overlay" composition draws graphics and captions together | One frame sequence, one FFmpeg composite in the existing pass: graphics add no extra render pass and no extra encode. |
| Numbers on screen are verified, words are reported | Every number a graphic shows (parameters, numbers inside text, chart values) must be found in its references; spoken and written forms compare equal ("ten thousand" = 10,000), rounding to the shown precision is allowed, inflation is not. Content words missing from the references are listed, not refused (paraphrase is legitimate; the review sees the list). In the acceptance run an invented "12,000" was refused. |
| Placement computed from the face track and the caption band, then measured on drawn pixels | Python chooses the anchor covering the least of the head (face box grown to hair and chin) above the reserved caption band; after rendering, the share of each head covered and pixels outside the safe area are measured from the overlay frames (other graphics and the caption block masked out). |
| Graphics are attached to source moments | A graphic stores the source time it appears at; every ripple/move re-maps it, so later cuts carry graphics with their content. If that moment is trimmed (a shortened pause), it moves to the next kept moment within 1.5 s; if its content is gone, `validate` and the review warn instead of letting it drift (a warning, so it never blocks saving an edit). |
| Punch-ins with FFmpeg `zoompan` inside the existing run | One per-frame expression covers every clip of a run (no extra runs, no re-decoding), cropping from the full-resolution frame before the single scale. Frame-exactness verified by the luma-coded test with a punch-in. `--auto` alternates 100/112 % only at same-angle jump cuts (joins on a source camera change are left alone; clips < 0.8 s keep their framing). |
| Transitions are overlays centred on the cut, not crossfades | Overlays keep the frame-exact, non-overlapping video pipeline; a true crossfade needs overlapping clips (later, if a client asks). |
| Automatic backing panels, decided per theme | Panel-less text (big counter, kinetic text, minimal lower third, plain handle, text logo) gets a surface panel when the theme's text colour would fall under WCAG 4.5:1 (accent 3:1) against the footage under it. The footage is measured once when the graphic is added (darkest 25th / brightest 75th percentile luminance); the panel is decided at render, so switching to a dark-text theme re-decides it. Found in the acceptance run: a yellow counter over bright sky was unreadable. `style` also checks the theme's own tokens (text and accent on its surface) against WCAG. |
| Fully opaque overlay frames are rewritten as RGBA | Chrome writes a fully opaque frame (a dip at its peak) as RGB; in an RGBA sequence that would switch FFmpeg's input pixel format mid-stream. |
| Visual regression goldens for every template variant | 35 stills at 320x180 in `tests/golden/graphics/`, compared with a tolerance (mean difference ≤ 2/255, ≤ 1 % of pixels off by > 48), regenerated only on purpose and inspected. |

## 5. Decisions you made

| Date | Question | Your answer | Consequence |
|---|---|---|---|
| 2026-11-07 | Remotion eligibility | You work as an individual (fewer than 3 people) | Remotion's free licence applies. Phase 3 uses Remotion. If the business grows past 3 employees, a paid Company License becomes necessary; the graphics layer is isolated so it could then be swapped for the free Playwright-based alternative (`ARCHITECTURE.md` §11). Re-check when Remotion 5.0 changes its licence. |
| 2026-11-07 | Name | No change requested | "Cutroom" stays. |
| 2026-11-07 | Caption renderer: Remotion or libass | Remotion | Captions drawn in Remotion (React), composited by FFmpeg. Slower renders than libass (measured above); maximum animation freedom; one graphics engine for captions and Phase 3 graphics. |
| 2026-11-07 | How to proceed after Phase 2 | "No questions, execute every phase automatically" | Phases 3–8 run without stopping for a go-ahead. Choices that would have been questions are made on the evidence and recorded in this section; the product's own human gates (cut plan for new clients, final render before delivery, non-free installs, sensitive content) stay in the design. |

## 6. Risks and how they are handled

| Risk | Mitigation |
|---|---|
| Untested on Windows/macOS/GPU so far | Setup and doctor written to official docs; doctor checks everything end to end; your first doctor run is the real test. |
| Whisper drops fillers/stumbles | Acoustic detection in Phase 1; verbatim prompt; review-loop checks for repeated phrases. |
| Word timing error | Boundary refinement in Phase 1; caption sync measured against audio in Phase 2/4. |
| Third-party API changes (like PyAV 17) | Exact pins, lockfile, doctor checks known-bad versions. |
| Large footage on small disks | `--reference` ingest, `CUTROOM_JOBS_DIR` on another drive, disk check in doctor. |
| Agent misjudges taste | Human approval gates; every review report lists what was not verifiable automatically. |
