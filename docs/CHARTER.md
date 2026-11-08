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

### 4.7 Phase 4 decisions

| Decision | Why / evidence |
|---|---|
| Checks ask the editing tools' own planners what they would still do, on a copy of the timeline | First version counted planned removals directly: on the interview it reported 123 "long pauses" that `remove-silence` correctly ignored (they lay in material already cut, out of order after the cold-open move). A check and its fix must never disagree, or the loop runs fixes that do nothing. |
| Flash shots fixed without touching words | Trim a word-free sliver so the cut lands on the source's camera change, else lengthen the shot into the neighbouring removed pause (`extend_clip_edge`, refuses to bring back words). A sliver holding a word with no pause to borrow is left for a person: moving the join by a word changes what is said. |
| Fix order inside a round, and no fights between fixes | Pauses and fillers first, then flash shots recomputed on that edit, then punch-ins, graphics, captions, audio. Found in the acceptance run: flash fixes before `remove-silence` were undone by it. Pauses next to a source camera change are not counted as "too long" (that is where flash fixes lengthen them), so the loop converges instead of cycling. |
| Level jumps compared only across distant takes, on per-word levels | Comparing one second of audio on each side of every join flagged natural sentence-end declination (−36 dB trailing off vs −24 dB starting the next sentence) dozens of times. Within one continuous take the level changes are the speaker's own. |
| Caption staleness checked on timing as well as words | Restoring a pause or a filler leaves the caption words identical but shifts them; the first version missed this seeded defect (only the sync check noticed, and that is not auto-fixable). |
| Ready = no blocker and no major | Minors and polish are reported with fixes; the loop never claims taste. The report ends with what automation cannot judge, and the agent must look at the contact sheets. |
| A review reuses the render of the same version | Versions are immutable, so a render of vNNN is still valid; this halves the loop's render time. |

### 4.8 Phase 5 decisions

| Decision | Why / evidence |
|---|---|
| A Short is its own job (`<job>.short-NN`) referencing the parent's media and sharing its analysis by hard links | Every tool (captions, graphics, review, render) works on a Short unchanged; no media copies; the Short has its own version history and review. Name fixes in the parent propagate to its Shorts. |
| Moments come from the cleaned edit in natural speech order | The long-form edit's cold open moved the key finding away from its context; the first finder (edit order) produced "…the finding… My name is Dan Odess." In speech order the same finder opens Short 1 on the finding and ends on its payoff. |
| The finder proposes, the agent chooses | Scores (first line, ending, length, topic, substance) with reasons; in the acceptance 2 of 5 picks were used as proposed and 3 were moved by the agent to self-contained openings and payoffs. Heuristics cannot judge a story; they make the agent's reading fast and the reasons visible. |
| Vertical framing per clip, per source shot | Still crop on a steady face (most of the interview), speed-limited follow on a moving one, a hard jump at every source camera change (a smoothed pan across a camera change looked like a mistake), blurred-fill layout for no face or a two-shot. Crop via FFmpeg `crop` with per-frame expressions in the existing runs; frame-exact (tested). |
| Hook headline in the speaker's words; fill layout when it would touch the face | A headline over the face-tracked crop covered 21 % of the head in the first test (measured on pixels). Opening on the blurred-fill layout (frame in the middle, headline above) for the hook's seconds is the common Shorts pattern and keeps the face clear; the layout switch lands in a real pause between words. |
| No punch-ins on vertical Shorts; jump cuts accepted | The crop is the framing; punch-in would replace it. Jump cuts are the accepted Shorts style, so the review does not flag them there. |
| Caption sync on very short media | With 7 measurable captions one outlier decided the verdict; Shorts (< 20 measurable) tolerate one miss up to 200 ms. |
| An issue can be accepted with a reason | Short 3 keeps a 0.26 s flash shot whose sliver holds "But did": trimming changes the words, lengthening brings back a filler. The editor's acceptance and reason are written into every later report; a J-cut would fix it properly (not supported yet). |
| `fix-words` for misheard names | "white sand" (low confidence) for "White Sands" reached the captions; corrections keep timing, are re-applied when words are rebuilt, and propagate to Shorts. |

### 4.9 Phase 6 decisions

| Decision | Why / evidence |
|---|---|
| Stock stills from Wikimedia Commons, licence read from Commons' metadata | Commons and nps.gov were reachable; Freesound and Pixabay were blocked (403). Commons gives a machine-readable licence per file, so the allowlist (public domain, CC0, CC BY) is enforced in code. Found public-domain NPS/USGS photos of the very trackways the interview is about. |
| Polite fetching | Wikimedia only serves standard thumbnail widths and throttles originals (HTTP 429); the fetcher asks for standard widths (1920, else 1280), paces downloads and honours `Retry-After`. During the run the shared network was throttled for minutes at a time; the fetch completed for 4 of 5 chosen files. |
| Music and sound effects generated (numpy), library music optional | No reachable free music source with reliable machine-readable licences; generated beds have no rights questions. Loops are seamless by construction (oscillators phase-locked to the loop length, circular envelopes and reverb), verified by measuring the sample jump at the loop point. |
| Mixing after the voice chain, before mastering; levels relative to measured speech | Compression never pumps on music; the final −14 LUFS holds whatever the bed. Music 12 dB under speech, a further 8 dB ducked while anyone speaks (look-ahead): measured 20.1 dB under speech in the acceptance. |
| B-roll as its own timeline list, attached to content like graphics | Cutaways move with their sentence when the edit changes. Rendered by pre-rendering each cutaway to exact frames (Ken Burns via zoompan on a 2× upscale, so the slow move has no pixel jitter) and overlaying it inside the runs, under captions and graphics; frame-exact (tested). Pictures needing more than 1.8× enlargement are refused. |
| Thumbnails: face frames scored, recomposed, checked on glyph pixels | Candidates from large, sharp, well-exposed faces spread over the video; reframed to put the face on a thirds line opposite the title; contrast measured against the background behind the glyphs (title rendered alone on black as the glyph mask; the first difference-based mask passed invisible text). |
| `analyze-reference` measures, it does not guess | Pauses kept by the reference's editor and its shot lengths become style parameters, with the measurements in the file header; look, captions, music and colour are left to the agent's eye. |

### 4.10 Phase 7 decisions

| Decision | Why / evidence |
|---|---|
| OTIO through the official library; FCPXML 1.10 and EDL written directly | OpenTimelineIO 0.17's core ships only its native formats; the FCP XML and EDL adapters are separate plugins. FCPXML and CMX3600 are small, documented formats; writing them directly avoids two more dependencies. Every file is read back and compared with the timeline frame by frame (clips, source ranges, total) before it counts as written. |
| Cutroom never approves its own work | `deliver` prepares a complete package marked AWAITING APPROVAL; `approve` records a named person. The CLI cannot know who types the command, so the rule is stated in the playbooks (00, 10) and the package explains what to watch. During this autonomous run nothing was approved. |
| Revision notes are mapped through the source | A client's "8:06" refers to the version they watched; mapping through the source time finds the same words in the current edit even after later cuts. Every note must end with commands or an answer (question or declined with the reason); approval and delivery are refused inside a revision plan. |
| Chapters are written after the last edit | Chapter times are positions in the edit; the publishing check validates them against the current length and YouTube's rules (first at 0:00, ≥ 3, each ≥ 10 s). |

### 4.11 Phase 8 decisions

| Decision | Why / evidence |
|---|---|
| Renders resume from finished parts, keyed to the exact plan | A 1080p final of the 8.5-minute interview takes ~18 minutes; losing it to a crash at minute 17 is the costliest failure a person would see. Finished audio, overlay frames and encoded runs are kept; the key is a hash of everything that changes the picture or sound (edit, settings, Cutroom version, Remotion sources), so a stale part can never be reused. Tested by crashing between runs and in the middle of the overlay: the resumed output matches a clean render. |
| Interrupted commands stop their whole process tree | Found while testing resume: Ctrl-C during an encode left FFmpeg running (it died only on its next progress write), racing the resumed render for the same part file. FFmpeg and Remotion (Node + Chrome) now run in their own process group and are killed as a group. |
| Batch lines run as separate processes, keyed by command | One job's crash cannot corrupt another's state; a finished line is not repeated when lines are added above it. `approve` is refused in a batch (the human gate). |
| Every job command is timed by the plugin registry | Tools did not all log themselves; wrapping them at registration covers every current and future tool. Logging failures never change a command's outcome. |
| The cost report states $0.00 and shows the evidence | There are no paid services to meter; the report instead lists what reached the network (pinned models, licensed assets with their sources). An audit of the code found network calls only in `assets/library.py` (Commons) and `perception/models.py` (model downloads); there is no upload path. |
| Golden test on the bundled synthetic clip with a frozen transcript | Runs everywhere without a speech model, exercises the real CLI from ingest to delivery, and pins what a person would notice (cuts, clips, captions, graphic, checks, review verdict, package files) in frames, with one frame of tolerance for decoder differences between FFmpeg builds. |
| CI only on manual dispatch | Zero budget: no runner minutes are spent unless a person starts the workflow. |
| Diarization not built | Planned for Phase 8. The good free models are gated (pyannote: an account and accepted terms, which is the person's decision, gate c); the ungated alternatives (speaker embeddings + clustering) could not be validated: the test recordings here have one on-camera speaker (the interviewer is heard only in a couple of short off-mic questions), far too little to measure accuracy. Shipping speaker labels without measured accuracy would mislead cutting and Shorts. Today: off-mic voices are flagged by level and the agent identifies interviewer questions from content. Next step when multi-speaker footage is available: measure an ungated embedding model on it before wiring it in. |
| Thumbnails accept chosen moments | The face-based picker failed on footage without faces (found by the golden test); `--at` lets the agent choose frames, and a face near the moment is still kept clear of the title. |

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
