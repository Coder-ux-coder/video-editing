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

## Phase 3: Motion graphics (2026-11-07)

**Built**

- Component library in Remotion (`graphics/src/components/graphics/`), all styled from design
  tokens in the style file (`graphics.theme`: colours, fonts, weights, sizes, radius, spacing,
  shadow, motion timing, safe area): lower thirds (bar, pill, minimal), title and chapter cards,
  callouts (box, spotlight, arrow), list reveals (numbered, checklist, bullets), kinetic text (pop,
  slide, marker), counters (big, inline), bar and line charts, comparisons (versus, pros/cons),
  CTA, social handle, progress bar, logo bug, overlay transitions (dip, flash, whip, swipe,
  glitch): 14 templates, 35 variants, each with a JSON spec in `templates/`.
- One "Overlay" composition draws graphics and captions in a single pass, composited by FFmpeg in
  the existing encode.
- `templates`, `template-preview` (stills in any theme, over a frame), `graphic-add`,
  `graphic-remove`, `graphic-list`, `punch-in` (`--auto` jump-cut disguise, single-clip punch or
  push), `transition`, `style` (switch theme).
- Facts on screen: every number a graphic shows is checked against its references (transcript
  sentences/spans, the brief, documents in the job's new `inputs/` folder); spoken and written
  numbers compare equal; unsourced numbers are refused; unsupported words are reported.
- Placement off the face (face track) and above the caption band, max 2 at once, reading-time
  minimum, kinetic text in the band that clears the head; backing panels decided by WCAG contrast
  between the theme's colours and the footage; graphics attached to source moments so edits move
  them (and `validate` flags a graphic whose moment was cut).
- Render: per-clip punch-ins/pushes via `zoompan` in the existing runs (frame-exact); new checks
  `graphics_off_faces` and `graphics_in_safe_area` measured on drawn pixels; opaque overlay frames
  normalised to RGBA.
- Playbook 06 part B (graphics), `templates/README.md`, editing standards §7.8–7.10.

**Bugs found and fixed on the way**

- Kinetic text words ran together (spacing tied to a layout token, not the font size).
- Chrome writes a fully opaque frame as RGB inside an RGBA sequence (would switch FFmpeg's input
  format mid-stream).
- A yellow counter over bright sky was unreadable → automatic backing panels; first version
  assumed light text, so a dark-text theme would have broken it → contrast decided per theme at render.
- Centred kinetic text sat under the speaker's mouth → band placement.
- The whole-video progress bar made the caption safe-zone check skip every frame (and pass
  vacuously) → it is masked, and pixel checks that sample nothing now fail.
- Marker highlight made the emphasised word unreadable in a dark-text theme.

**Acceptance run** (the 10-minute NPS interview, from the captioned v069; `scripts/acceptance/phase3_graphics.sh`)

| Step | Result |
|---|---|
| Lower third | "Dan Odess / Chief Scientist, National Park Service" when he first says his name; sources: sentences 1 and 4; placed bottom-left (face on the right), above the captions |
| Number counter | "8,000+ years / further into the past" from "…pushing them back 10,000 years, 8,000 years further into the past…"; an invented 12,000 was **refused** |
| List reveal | "Tracks found here: Human, Sloth, Mammoth, Wolf, Bear" as he names them (sentences 19–20) |
| Kinetic text | "The evidence is tracks" (marker), low band, captions hidden for those words only (1 cue) |
| Punch-ins | `punch-in --auto`: 63 of 134 clips at 112 % on same-angle jump cuts |
| Transition, CTA, progress bar | Dip at the topic change at 4:05; subscribe CTA near the end; progress bar |
| Full preview render (8:42, 540p, captions + 7 graphics + punch-ins) | 6 min 21 s on 4 cores (Phase 2 without graphics: 6 min 50 s); 15,658/15,658 frames; all 10 checks pass |
| Graphics checks (drawn pixels) | 5 placed graphics measured: 0 % of the head covered, 0 frames outside the safe area |
| Captions | 0 of 1,135 sampled frames outside the safe zone; sync 96.5 % within 80 ms (median 19 ms) |
| Audio | −14.2 LUFS, −1.4 dBTP, 0 clicks at 133 joins |
| Theme switch | `style --client-style demo-bold`: every graphic restyled (light panels, dark text, red/teal, Inter 900, faster motion); backing panels re-decided for dark text; range renders pass all checks |
| Visual regression | 35 golden stills, all variants match |

**Tested**: 166 automated tests (156 fast + 10 slow, all passing), including frame-exact rendering with a punch-in and a
lower third, golden images for every variant, the theme switch measured on pixels, fact checking
(spoken/written numbers, rounding vs inflation, documents), placement, graphics following edits.

**Known limits**

- Graphic sizes are estimated for placement (a size hint per template); the render measures the
  real pixels and fails the check if text ran wider than estimated.
- Transitions are overlays; true crossfades (overlapping clips) are not rendered.
- Facts are checked for numbers; claims in words are reported, not proven (paraphrase is allowed).
- 4 flash shots remain in this edit (made by Phase 2's sentence cuts next to the source's camera
  changes); the Phase 4 review loop detects and fixes these.
- Remotion still draws every frame; graphics add no measurable time, captions dominate.

**Next: Phase 4** (review loop): `cutroom review` with automatic checks, frame sheets, report and
scorecard, and the autonomous fix loop. Acceptance: seeded defects are detected and fixed.

## Phase 4: Review loop (2026-11-07)

**Built**

- `cutroom review <job>`: preview render (reused for an already-rendered version), automatic
  checks in six categories, contact sheets (a frame every 4 s, a frame at every issue),
  `review/vNNN/report.md` + `review.json` with issues by severity, evidence and a fix command,
  scorecard 0–10 per category, "ready for the human gate" = no blocker and no major.
- Checks: audio (no voice chain, loudness, true peak, clicks, pauses and fillers the tools would
  still remove, level jumps between distant takes), cuts (mid-word cuts, invalid timeline, flash
  shots, undisguised mid-sentence jump cuts, words clipped by a cut), captions (missing, stale
  words or timing, safe zone, sync, reading speed), graphics (unsourced numbers, no source, lost
  moment, too short, on a face, outside the safe area, crowded), video (frame count, duration,
  edit lists, black and frozen frames, analysis quality flags on used shots), structure (ends
  mid-sentence, weak opening, long static stretches, low-value topics kept).
- `review --fix`: up to 4 rounds of review → safe fixes as ordinary commands (remove-silence,
  remove-fillers, flash-shot trim/extension, punch-in --auto, graphic-remove, graphic-move,
  captions, audio) → review. New `extend_clip_edge` (lengthen a clip into a removed pause, never
  over words) and `graphic-move`.
- Playbook `09_review_loop.md`; editing standards §13 now says which tells are checked
  automatically and which the agent judges on the sheets.

**Bugs found and fixed on the way**

- "123 long pauses" on an edit already cleaned: the check counted planner proposals in material
  already cut (out of order after the cold open). Checks now ask what the tool would really remove.
- Level jumps flagged natural sentence-end declination dozens of times: now per-word levels,
  distant takes only.
- Stale caption timing (same words, shifted) was missed: now detected and re-planned.
- Flash-shot fixes were undone by `remove-silence` later in the same round: fix order changed and
  pauses at camera changes protected, so the loop converges in one fix round.
- Caption sync counted a caption deliberately delayed by kinetic text as 2.8 s late.
- Graphics without any source reference were not flagged.
- A counter suffix " years" lost its space (shown "10,000years").

**Acceptance run 1: seeded defects** (`scripts/acceptance/phase4_review.sh`; a 2:40 excerpt of the
NPS interview edited cleanly to v013, then 9 defects planted by `phase4_seed.py`)

| Seeded defect | Found by | Fixed by the loop |
|---|---|---|
| 2 shortened pauses restored | audio.long_pauses | remove-silence |
| a removed "um" restored | audio.fillers | remove-fillers |
| voice chain removed | audio.no_chain | audio |
| captions left stale by the restores | captions.stale (timing) | captions --style clean |
| list graphic forced over the face | graphics.face (26 % of the head, measured) | graphic-move --replace |
| counter showing 30,000 (never said) | graphics.unsourced (blocker) | graphic-remove |
| lower third shown 0.6 s | graphics.too_short | graphic-move --duration 1.4 |
| punch-ins removed | cuts.jump_cuts | punch-in --auto |
| (also found: 3 real flash shots in the clean edit) | cuts.flash_shot | lengthened into the pause |

All 9 seeds detected; round 1 (16 issues) → 1 fix round (9 commands) → round 2: 0 blockers, 0 majors,
ready for the human gate. The 2 remaining minors are the seeded graphics that cite no source
(left for a person by design). Render checks on the result all pass (−14.2 LUFS, −1.4 dBTP,
0 flash shots, graphics off faces). 3 min 31 s on 4 cores including both renders.

**Acceptance run 2: the full 10-minute interview** (v093, the Phase 3 result)

| Round | Version | Blocker / major / minor / polish | Fixes applied |
|---|---|---|---|
| 1 | v093 | 0 / 4 / 1 / 2 | 3 flash shots lengthened into the neighbouring pause (0.15, 0.07, 0.28 s); captions re-planned |
| 2 | v096 | 0 / 1 / 1 / 2 | none safe: stops |

The remaining major is a 0.26 s flash shot whose sliver holds the start of a word and has no
pause to borrow: moving that join changes what is said, so it is left for a person with the
command to do it. All 10 render checks pass on v096 (−14.2 LUFS, −1.5 dBTP, captions 0 of 1,147
frames outside the safe zone and 95.5 % within 80 ms, graphics off faces). 7 min per round on 4
cores (one full render each; round 1 reused the existing render).

**Tested**: 175 automated tests (165 fast + 10 slow, all passing); `tests/test_review.py` seeds
each defect type in a synthetic timeline and checks it is found, fixable and scored.

**Known limits**

- Taste is not checked: expressions at cut points, whether graphics help, whether the hook lands.
  The report lists sheets to look at and what automation cannot judge.
- A flash shot that holds a word, a weak opening, a graphic without a source and low-value
  topics are reported with a fix command but never fixed automatically.
- No lip-sync measurement (matters only for separately recorded audio).
- Each round renders the whole video (7 min for 9 min of output here); reviewing only changed
  ranges is a Phase 8 optimisation.

**Next: Phase 5** (Shorts): moment finding and scoring, 9:16 reframing with a smoothed face
track or blurred fill, hook text, captions in the Shorts safe zone, titles and descriptions.
Acceptance: 5 Shorts from the interview.

## Phase 5: Shorts (2026-11-08)

**Built**

- `shorts-find`: whole-sentence windows of the cleaned edit in speech order, scored for the first
  line (no reference to unseen context, strong words, a question or a number), a complete ending
  (not an open question), length (20–58 s by default), one topic (preferring topics marked as
  Short candidates) and substance; reasons listed; non-overlapping picks.
- `short-create`: a Short is its own job `<job>.short-NN` (media referenced, analysis hard-linked),
  1080x1920, framed per clip and per source shot: still crop on a steady face, speed-limited
  follow on a moving one, a cut at each camera change, blurred-fill layout when there is no single
  clear face. Renderer: per-frame `crop` expressions and a blur/overlay fill graph in the existing
  runs, frame-exact.
- `short-hook`: headline in the speaker's own words (sourced, new `headline` template); the opening
  switches to the blurred-fill layout when the headline would touch the face, at a real pause.
- Captions in the Shorts safe zone; graphics placement and face checks mapped through the crop;
  Shorts safe area for graphics; review checks for Short length and faces cut by the crop.
- `short-publish`: agent task + check (title ≤ 100, 3–5 hashtags starting `#Shorts`, numbers said in
  the Short, brief's credit line, no hype). `shorts`: batch of the above.
- Also: `fix-words` (misheard names, timing kept, re-applied on rebuild, propagated to Shorts),
  `review --accept` (an editor's judgement with its reason, shown in every report), a fair
  caption-sync verdict for very short media. Playbook `08_shorts.md`.

**Bugs found and fixed on the way**

- Moments in edit order split the key finding from its context (cold open) → speech order.
- "That's the first one…" scored as a good opening (contraction not recognised as a
  back-reference); questions at the end scored as good endings.
- A smoothed crop panned across a source camera change → framing per shot with a hard jump.
- The hook headline covered 21 % of the head and crossed the Shorts safe area (measured on
  pixels) → blurred-fill opening, kinetic text kept inside its box, smaller headline type.
- The hook's layout switch landed between two touching words (mid-word cut).
- Panel drop shadows counted as graphic pixels in the safe-area check.
- `punch-in` would have replaced a Short's crop → refused on vertical timelines.
- "white sand" (for White Sands) in the captions of the long-form and Short 3 → `fix-words`.

**Acceptance run: five Shorts from the interview** (`scripts/acceptance/phase5_shorts.sh`)

| Short | Source range | Length | Opening line | Hook (layout) | Review |
|---|---|---|---|---|---|
| 1 The White Sands finding | 0:50–1:43 | 47.5 s | "The most significant finding is…" | "People were here longer than we thought" (fill) | ready |
| 2 Tracks everywhere | 2:18–3:02 | 37.6 s | "What surprises me is just how common these things are out here." | "Archaeologists typically don't look for tracks" (crop) | ready |
| 3 Did humans cause the extinction? | 4:54–5:41 | 39.3 s | "I think in the past people have said…" | "Did humans cause the extinction?" (fill) | ready, 1 flash shot accepted with reason |
| 4 Sloths, mammoths and people | 6:02–6:27 | 20.1 s | "We've published papers where we see ground sloths…" | "Ground sloths reacting to human tracks" (crop) | ready |
| 5 Kids at the lake | 3:04–3:32 | 26.7 s | "The other thing that's surprising…" | "Tracks from kids, including toddlers" (crop) | ready |

The finder's picks 1 and 2 were used (1 extended to its payoff); 3–5 were chosen by the agent for
self-contained openings and endings after reading the reasons. Every crop keeps the face in frame
(checked on the sheets and by `short.face_cropped`). Final renders 1080x1920, all checks pass:
−14.1/−14.2 LUFS, −1.9 dBTP, captions 0 frames outside the Shorts safe zone, sync passes, 0 clicks;
8 min for all five (2:51 of Shorts) on 4 cores. Publishing texts written and checked for all five.

**Tested**: 190 automated tests (179 fast + 11 slow, all passing), including a frame-exact vertical
render mixing crop and fill layouts, framing rules, scoring rules, derived jobs, publishing checks
and word corrections.

**Known limits**

- No J/L cuts: a flash shot whose sliver holds a word can only be accepted.
- One speaker per Short in practice: two-shots use the fill layout; no active-speaker switching
  (needs diarization, now Phase 8).
- Moment scoring is a heuristic; the agent's reading decides (3 of 5 picks were changed here).
- Final 1080x1920 renders spend most time drawing caption frames in Chrome.

**Next: Phase 6** (b-roll, music, SFX, thumbnails).

## Phase 6: B-roll, music, sound effects, thumbnails (2026-11-08)

**Built**

- Licence-tracked asset library (`assets`, `asset-add`, `asset-fetch`): public domain, CC0 and CC BY
  only (enforced in code), licence read from Wikimedia Commons' own metadata, sidecar per file,
  de-duplicated by hash; polite fetching (standard thumbnail widths, pacing, `Retry-After`).
- Generated music beds (calm, documentary, upbeat, warm; seamless loops) and sound effects
  (whoosh, riser, impact, pop, ding), numpy only.
- `music` and `sfx`: beds mixed after the voice chain and before mastering, 12 dB under the
  measured speech and ducked 8 dB more under speech with look-ahead; every render reports the
  measured speech-to-music ratio.
- `broll-suggest` and `broll-add`: cutaways (stills with a slow Ken Burns move, or video) attached to
  their sentence, pre-rendered to exact frames and composited under captions and graphics;
  pictures that would need > 1.8× enlargement are refused.
- `thumbnails`: candidates from the best face frames, recomposed with the face on a thirds line
  opposite the title, drawn by Remotion; checked for 1280x720, < 2 MB, title contrast against
  what is behind the glyphs, no glyph on the face, sourced numbers; `--pick`.
- `analyze-reference`: a reference video's kept pauses and shot lengths into a style file.
- `extend` (lengthen a clip into a removed pause, never over words); review checks for licences,
  credits, b-roll length, music level, effect overuse. Playbook `07_broll_and_music.md`.

**Bugs found and fixed on the way**

- Generated loops clicked at the loop point (oscillator phases, envelopes and reverb not
  circular) → phase-locked oscillators, circular envelopes and reverb; seam jump now equal to an
  ordinary sample step.
- Wikimedia refused originals and non-standard widths (HTTP 429): standard widths, an original is
  re-requested at 1280, `Retry-After` honoured. The licence normaliser rejected its own canonical
  names.
- Thumbnail checks passed text that would be invisible (difference-based glyph mask) → the title is
  also rendered alone as the mask. A first thumbnail put the title over the face → recomposition.
- Review false positive: a zero-length word (recogniser artefact) at the end counted as a clipped word.

**Acceptance run** (the interview, after Phases 3–5; `scripts/acceptance/phase6_media.sh`)

| Step | Result |
|---|---|
| Assets | 4 public-domain photos of the White Sands trackways (NPS, USGS) from Commons; a 5th refused by the rate limit, the ground-sloth photo refused by `broll-add` (489 px wide) |
| B-roll | 3 cutaways (4.0, 4.5, 3.6 s) at the finding, the list of tracks, "the evidence is tracks"; graphics and captions drawn over them |
| Music | Generated documentary bed under the cold open (0–22 s) and the last 20 s; measured 19.8 dB under speech |
| Effects | One whoosh on the dip transition |
| Full preview render (8:43) | All 10 checks pass: frames exact, −14.2 LUFS, −1.5 dBTP, captions in safe zone and in sync, graphics off faces; 6 min 33 s on 4 cores |
| Review | Ready for the human gate: 0 blocker, 0 major, 0 minor (one flash shot accepted with its reason) |
| Thumbnail | 3 candidates, all checks pass (contrast 4.5–4.6:1, ~126 KB); candidate 2 picked (eyes open, facing camera) |
| Reference style | `weekly-address`: 160.7 words/min, pauses kept 0.21 s mid-sentence / 0.77 s between sentences → style with those pauses; one long shot → pacing left at defaults |

**Tested**: 198 automated tests (186 fast + 12 slow, all passing), including a frame-exact b-roll
render, ducking measured on a synthetic mix, seamless loops, the licence allowlist and the
thumbnail checks.

**Known limits**

- B-roll suggestions match keywords; whether a picture fits is the agent's judgement (by eye).
- Generated music is simple; real music needs a file with a licence on record (`asset-add`).
- Commons rate-limits shared networks; fetching can take minutes.
- No J/L cuts; no colour matching between b-roll and the interview.

**Next: Phase 7** (orchestrator, revisions, NLE export, delivery).

## Phase 7: Orchestrator, revisions, NLE export, delivery (2026-11-08)

**Built**

- `export`: the edit for an NLE, each file read back and compared with the timeline before it is
  reported: OTIO via OpenTimelineIO (Resolve), FCPXML 1.10 (Resolve, Final Cut), CMX3600 EDL
  (Premiere, Avid), captions as SRT. Cuts on the renderer's frame grid; b-roll on V2, music and
  effects on A2/A3 (OTIO, FCPXML); chapters and graphics as markers.
- `publish`: a task with chapters proposed from the topic map and the brief's credit lines; `--check`
  validates `publish.json` (title ≤ 100 characters, no all-caps hype, every number said in the
  video, YouTube's chapter rules: first at 0:00, at least 3, each ≥ 10 s) and writes the chapters
  into the timeline and `delivery/description.txt`.
- `revise`: client notes (one per bullet or line) with their times mapped from the version the
  client watched to the current edit, through the source, so cuts made since are accounted for;
  what is said and on screen there; a per-note plan (commands and/or a response); `--apply` runs it
  as ordinary versioned edits and writes a note → action → version report. `approve` and
  `deliver` can never be part of a plan.
- `deliver`: refuses until the final render, a clean review of it, the thumbnail and checked
  publishing text exist for the long-form and every Short; then a package with the video, SRT/VTT,
  thumbnail, title, description, Shorts with their texts, NLE files, `REPORT.md`, `MANIFEST.json`,
  `checksums.sha256` and `APPROVAL.md`, status AWAITING APPROVAL. Open questions (`questions.md`,
  and revision notes answered with a question) head the report. `approve` records a person's name;
  the agent never runs it.
- `next`: every playbook stage as done or not, with the next command. Playbook `10_delivery.md`;
  the orchestrator (`00`) uses `next`, the approval gate and revision rounds.

**Acceptance run** (the interview; `scripts/acceptance/phase7_delivery.sh`)

| Step | Result |
|---|---|
| Revision r01 (notes on v103) | 5 notes: 3 done as versions v104–v108 (opening music −4 dB; footprints picture 3.6 → 5 s; two off-topic sentences cut), 1 kept as asked, 1 answered with a question ("Dr." is not in the brief or the footage, so it is not put on screen until confirmed). Note times mapped correctly through the cuts made since |
| Captions, review | Re-planned after the cut (v109); review ready |
| Publishing text | Title, description in the speaker's terms with the credit line, 11 chapters (v110) |
| Final render v110 | 1920x1080, 8:31, all 10 self-checks pass (−14.2 LUFS, −1.8 dBTP, music 21 dB under speech, 7 graphics off faces, captions in the safe zone and in sync); 17 min 53 s on 4 cores |
| Review of the final | Ready for the human gate: 0 blocker, 0 major, 0 minor, 2 polish (a 29 s stretch without a visual change; fast captions follow fast speech); the flash shot accepted with its reason. Contact sheets looked at: graphics and b-roll clear of the face |
| NLE export | OTIO, FCPXML and EDL each 131 clips / 15,314 frames, verified on read-back |
| Package | `delivery/accept-nps-interview_v110/`: video, captions, thumbnail, title, description, 5 Shorts with texts, NLE files, report, manifest, checksums; **AWAITING APPROVAL**, with 3 open questions (the speaker's 10,000 → 8,000 years correction, how many Shorts, "Dr.") |
| `next` | Every stage done except "approval (a person)" |

**Bugs found and fixed on the way**

- A header line in the client's notes ("Notes on the cut you sent") was parsed as a note → when
  any line is a bullet, only bullets are notes.
- Open questions lived only in the job log and the revision report → `questions.md` and question
  responses are collected into the package report and `APPROVAL.md`.

**Tested**: 7 delivery tests (NLE round trips, publishing checks and chapters, revision mapping
through later cuts, the delivery gate and approval, stage guide, client style scaffold, open
questions), plus the acceptance run above.

**Known limits**

- FCPXML and EDL carry the cut and the b-roll; graphics and captions travel as markers and SRT,
  not as editable titles (Remotion draws them).
- A revision note without a timecode is mapped to nothing; the agent reads it against the whole edit.
- Package files are hard links where the file system allows (copies otherwise); the person sends
  the package, Cutroom uploads nothing.

**Next: Phase 8** (hardening): resumable renders, stage timings, batch queue, a job report with
cost and network use, a golden end-to-end test, CI, documentation.

## Phase 8: Hardening (2026-11-08)

**Built**

- **Resumable renders.** A render keeps its finished work (edit audio and mix, pre-rendered b-roll,
  overlay frames, each encoded video run) in its scratch folder with a `state.json` keyed to a hash
  of the exact plan: the edit, the settings, the Cutroom version and the Remotion sources. If it
  fails or is interrupted, the same command continues from there; any other plan starts clean.
  The scratch folder is removed only after a successful mux.
- **Stage timings** (`timings_s`) in every render report: audio, overlay, checks, b-roll, video, mux.
- **Clean interrupts.** FFmpeg and Remotion (Node and its Chrome) run in their own process group
  and are killed as a group when a command is interrupted or fails; SIGTERM (`kill`, a shutdown,
  a task runner) is handled like Ctrl-C (exit code 130, message on stderr, warning in `log.md`).
- **`batch`**: a text file of commands, one process per line; the outcome of each line is kept next
  to the file, so running it again skips finished lines (keyed by the command, not its line
  number); stops at the first failure unless `--keep-going`; `approve` is refused.
- **`report`**: every logged command (the plugin registry now times every job command) with runs,
  failures and time; render stages; the review verdict; cost ($0.00, no paid services); what came
  from the network (pinned models, licensed assets with sources); client media uploaded: no.
- **Golden end-to-end test** (`tests/test_golden.py`): the bundled synthetic clip from raw footage
  to a package awaiting approval through the real CLI (one process per command, frozen transcript,
  no speech model needed), including refusals (an unsourced number in a graphic, a number in the
  title the video never says, delivery before the final render). The edit is pinned in
  `tests/golden/doctor_pipeline.json` (steps, clips and cuts in frames, captions, graphic, every
  render check, review verdict, package files), compared with one frame of tolerance.
- **CI** (`.github/workflows/tests.yml`): GitHub's free runners, started by hand only.
- **`thumbnails --at`** for footage without faces (the golden test found the face-only picker
  could not make a thumbnail of a screen-like clip, and its error pointed to an option that did
  not exist).
- **Docs**: `docs/GUIDE.md` (how a person runs, approves and revises a job), README for the
  finished system, CAPABILITIES, CHARTER 4.11, ARCHITECTURE (resume, command log, process groups,
  diarization status).

**Acceptance run** (the interview at v110; `scripts/acceptance/phase8_hardening.sh`)

| Step | Result |
|---|---|
| Profile of a full 540p preview (8:31 edit, 4 cores) | 406 s: overlay drawing 238 s (59 %), video encode 85 s, audio 40 s, mux 24 s, b-roll 10 s, checks 6 s |
| Interrupt | `kill` 15 s into the second (last) video run, after audio, b-roll, 15,314 overlay frames and the first run were done: exit 130, finished parts kept, warning in the log |
| Resume (same command) | **104 s instead of ~400 s**: audio, b-roll and overlay reused (0.3 s), only the cut-off run re-encoded; all 10 self-checks pass (frames exact, loudness, true peak, clicks, captions safe and in sync, graphics off faces and in the safe area) |
| Batch | validate, export, report: 3 ok; run again: all 3 skipped |
| Job report | 1 h 49 min of logged commands for this job, almost all renders (46 runs; 6 failed: interrupted ones and failures while earlier phases were built); cost $0.00; 5 models and 6 assets listed with sources; client media uploaded: no |
| Golden end-to-end | Raw clip → package awaiting approval through 23 CLI calls (3 of them refusals it must make) in ~85 s; matches `tests/golden/doctor_pipeline.json` |

**Bugs found and fixed on the way**

- Ctrl-C during an encode left FFmpeg running until its next progress write, racing a resumed
  render for the same file → process groups, killed as a group.
- `kill` (SIGTERM) ended Python without any cleanup → handled like Ctrl-C.
- The first acceptance attempt could not interrupt the render: a script's background job ignores
  SIGINT (POSIX), so it ran to completion; the script now sends SIGTERM.
- The face-only thumbnail picker failed on footage without faces, and its error pointed to an
  option that did not exist → `thumbnails --at`.
- Most tools did not record their time → the plugin registry times every job command.
- The report's `[x]` checkboxes were eaten as terminal markup → printed verbatim.

**Tested**: 217 automated tests (203 fast + 14 slow), all passing, including resume after a crash
between runs and in the middle of the overlay (output compared with a clean render), a changed
plan never reusing parts, corrupt state discarded, FFmpeg stopped on interrupt, SIGTERM through
the real CLI, batch resume and refusals, the job report, and the golden end-to-end run.

**Known limits**

- A hard kill of the Python process alone (`kill -9`) cannot stop its children; FFmpeg then stops
  on its next progress write (its pipe is gone). A power cut stops everything, and the resume
  redoes any part not recorded as finished.
- Resume is per render; a batch resumes by skipping finished lines, not mid-line work of other
  commands (analysis stages are already cached).
- Overlay drawing dominates render time; frames with nothing on screen are still drawn.
- Speaker diarization was not built (CHARTER 4.11).
- Windows, macOS and NVIDIA GPUs remain untested on real hardware; CI covers Linux when started.

**Next**: none scheduled. The interview's delivery package awaits a person's approval with three
open questions; candidates for later work are in CAPABILITIES ("Partially", "Cannot do yet").
