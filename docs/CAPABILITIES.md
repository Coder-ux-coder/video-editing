# Capabilities

An honest map of what Cutroom can do today, what it can do partially, and what it cannot
do yet, with the plan for each gap. Updated at the end of every phase.

Last updated: Phase 8 (hardening: resumable renders, batch queue, job report, golden end-to-end test, CI).

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
| Voice chain + loudness | `audio`, `loudness` | Stages decided from measurements (denoise only when needed); −14 LUFS by linear gain + oversampled limiter; verified on the output file |
| Captions, 4 styles (Remotion) | `captions --style clean|bold-karaoke|word-highlight|minimal` | Sense-based phrasing, ≤ 2 lines, word-level karaoke/highlight, emphasis, off-face placement, automatic contrast scrim, long-form and Shorts safe zones |
| Caption files | `export-captions` | `.srt` and `.vtt` in edit time |
| Caption QA | `render` | Safe zone measured on drawn pixels; timing vs speech onsets; reading speed reported |
| Range renders | `render --range A-B` | Review any section without a full render |
| Plugins | `src/cutroom/tools/*.py` | Manifest + auto-registration + required tests |
| Motion-graphics library (Remotion) | `templates`, `template-preview`, `graphic-add`, `graphic-remove`, `graphic-list` | 14 templates, 35 variants: lower thirds, title and chapter cards, callouts (box/spotlight/arrow), list reveals, kinetic text, counters, bar/line charts, comparisons, CTA, social handle, progress bar, logo bug, overlay transitions; all styled from design tokens |
| Facts on screen verified | `graphic-add --fact` | Every number shown must be in the referenced transcript span or a client document (spoken = written forms); unsupported words reported; references stored in the timeline |
| Graphics placement | `graphic-add` | Off the face (face track), above the caption band, inside the safe area, away from other graphics; ≤ 2 at once; reading-time minimum; automatic backing panel when the theme's text would lack WCAG contrast against the footage; verified on drawn pixels at render |
| Graphics follow the edit | all cutting commands | Attached to source moments; cuts and moves carry them; a cut-away moment is flagged by `validate` |
| Jump-cut disguise | `punch-in --auto` | Alternating 100/112 % framing at same-angle jump cuts, centred on the face, frame-exact; single-clip punch or slow push |
| Overlay transitions | `transition` | dip, flash, whip, swipe, glitch, centred on a cut and moving with it |
| Theme switch | `style --client-style` | Client themes (`styles/clients/*.style.yaml`) restyle every graphic and caption without touching the edit |
| Graphics visual regression | tests | Golden still for every variant, compared with a tolerance |
| Automatic review | `review` | Audio (loudness, peaks, clicks, pauses/fillers left, level jumps between takes), cuts (mid-word, flash shots, undisguised jump cuts, clipped words), captions (missing, stale words or timing, safe zone, sync, reading speed), graphics (on faces, safe area, unsourced, no source, lost moment, too short, crowded), video (frames, black/frozen frames, quality flags), structure (ending, opening, static stretches, low-value topics); report with severity, evidence and fix command; scorecard; contact sheets for the agent to look at |
| Shorts: moment finding | `shorts-find` | Whole-sentence windows in speech order scored for hook, ending, length, topic, substance; reasons listed; agent chooses |
| Shorts: derived 9:16 jobs | `short-create` | Own job per Short sharing media and analysis; face-tracked crop (still / slow follow / cut at camera changes) or blurred-fill layout; frame-exact render |
| Shorts: hook | `short-hook` | Headline in the speaker's words, sourced; blurred-fill opening when the headline would touch the face |
| Shorts: captions and checks | `captions --output youtube_short`, `review` | Shorts safe zone; length within platform limit; face kept inside the crop |
| Shorts: publishing text | `short-publish` | Agent task + check: title ≤ 100 chars, 3–5 hashtags starting #Shorts, numbers said in the Short, credit line, no hype |
| Batch Shorts | `shorts` | find → create → captions → hook (→ preview) in one command |
| Transcript corrections | `fix-words` | Misheard names/terms fixed with timing kept; re-applied on rebuild; propagated to Shorts |
| Licence-tracked asset library | `assets`, `asset-add`, `asset-fetch` | Public domain / CC0 / CC BY only, licence read from the source's metadata (Wikimedia Commons), sidecar per file, de-duplicated by hash |
| B-roll cutaways | `broll-suggest`, `broll-add` | Keyword suggestions; stills with Ken Burns or video clips; attached to content; frame-exact; small pictures refused |
| Music beds | `music` | Generated (calm, documentary, upbeat, warm; seamless loops) or from the library; 12 dB under speech, ducked 8 dB more under speech; measured in every render |
| Sound effects | `sfx` | Generated whoosh, riser, impact, pop, ding; on transitions or at moments |
| Thumbnails | `thumbnails` | Candidates from the best face frames, title away from the face, 1280x720 < 2 MB, contrast and face checks, sourced numbers; `--at` for moments you choose (footage without faces); `--pick` |
| Style from a reference video | `analyze-reference` | Pauses and pacing measured into `styles/<name>.style.yaml` |
| NLE export | `export` | OTIO (Resolve), FCPXML 1.10 (Resolve, Final Cut), CMX3600 EDL (Premiere/Avid), SRT; verified by reading back |
| Publishing text and chapters | `publish` | Agent task + check (title, description, YouTube chapter rules, tags, hashtags, sourced numbers, credits); chapters into the timeline |
| Client revisions | `revise` | Notes mapped from the watched version to the current edit with context; per-note plan; applied as versions; note-by-note report |
| Delivery package | `deliver` | Video, captions, thumbnail, texts, Shorts, NLE files, report, manifest, checksums; refuses until ready; open questions first in the report; AWAITING APPROVAL |
| Human approval gate | `approve` | Records a named person's approval; never run by the agent |
| Orchestration guide | `next` | Every playbook stage done or not, and the next command |
| Client styles | `client-style`, `style --client-style` | Template for brand colours, fonts, captions, music levels |
| Accepting an issue | `review --accept CHECK@T --reason …` | Editor's judgement recorded in every later report |
| Resumable renders | `render` (same command again) | An interrupted render keeps its finished audio, overlay frames and encoded runs, keyed to the exact plan (edit, settings, graphics code); a changed plan starts clean; frame-exact after resume (tested by crashing mid-overlay and between runs) |
| Stage timings | `render` | Every render reports seconds per stage (audio, overlay, checks, b-roll, video, mux) |
| Job report: time, cost, network | `report` | Every command's runs, failures and time; render stages; review verdict; cost ($0.00, no paid services); models and assets that came from the network; client media uploaded: never |
| Batch queue | `batch` | A text file of commands, each in its own process; re-running skips finished lines (keyed by command, not line number); stops at the first failure or `--keep-going`; `approve` refused |
| Golden end-to-end regression | `tests/test_golden.py` | The bundled clip from raw footage to a package awaiting approval through the real CLI; the edit (cuts, clips, captions, graphic, checks, review, package) pinned in `tests/golden/` |
| CI | `.github/workflows/tests.yml` | Runs only when a person starts it (no automatic runner minutes) |
| Autonomous fix loop | `review --fix` | Up to 4 rounds of review → safe fixes (pauses, fillers, flash shots, punch-ins, graphics, captions, audio) → review; stops at the human gate. Seeded-defect test: 9/9 found and fixed in one fix round |

## Partially

| Capability | Gap | Plan |
|---|---|---|
| Flash shots where the source's own camera change falls mid-speech | `review --fix` trims word-free slivers onto the camera change or lengthens the shot into a neighbouring pause; a sliver that holds a word with no pause to borrow is left for a person | Move the join by a word (needs judgement) |
| Speaker identification | Off-mic voices hinted by level (⟪quiet⟫); interviewer questions identified by the agent from content | Diarization was planned for Phase 8 and **not built**: the free models good enough to trust are gated (an account and accepted terms) or untested here; see CHARTER 4.11 |
| Filler/stumble transcription | Whisper drops some; acoustic detection catches voiced sounds in pauses, not fillers fused into words | Measure on more real footage; optional second verbatim pass |
| Footage-type detection | Heuristic; screen-recording detection untested on real screen captures | Test on real samples; add OCR text density |
| Windows / macOS / NVIDIA GPU | Written to official docs, not yet run on real hardware | Your first `setup` + `doctor` run |
| Reading speed | Captions follow speech, so fast talkers exceed 17 chars/s (reported, not hidden) | Optional condensed captions (agent-edited text) for very fast speech |
| Caption and graphics render time | Remotion draws every frame. Profile of the 8.5-min interview preview (540p, 4 cores, 406 s): overlay 238 s (59 %), video 85 s, audio 40 s, mux 24 s; the 1080p final takes ~18 min. An interrupted render resumes in a fraction of that (104 s here) | Skip frames with nothing on screen; reuse unchanged overlay frames between versions |
| Graphics size estimate | Placement uses a size hint per template; long text can be wider than estimated (the render check then fails and says so) | Measure text with the theme fonts before placing |
| J/L cuts | Audio and picture are cut together; a flash shot that holds a word cannot be fixed without one | Separate audio/picture edges per join |
| Shorts with several speakers | Two-shots use the fill layout; no active-speaker switching | Needs diarization (not built) |
| B-roll matching | Keyword match between sentences and asset tags; relevance is judged by the agent looking at the picture | Visual-semantic matching (local CLIP-style model) |
| Music | Generated beds are simple pads; no free music source with machine-readable licences was reachable | Library music added by hand (`asset-add`) with its licence |
| Crossfades | Transitions are overlays over a cut; true video crossfades (overlapping clips) are not rendered | Add when a client style asks for them |
| Review of taste | Framing, expressions at cut points, whether graphics help, whether the hook lands: contact sheets are produced, the agent judges them | Expression/blink detection at cut points |
| Black/frozen frame and level checks | Measured on the render (FFmpeg blackdetect/freezedetect) and on per-word levels; lip sync is not measured | Lip-sync check for separately recorded audio |
| Audio stages | EQ is a gentle fixed curve, not matched to the voice; no hum removal, no music/speech separation | Measured EQ and hum notch (when a client needs it); speech/music segmentation with music (Phase 6) |

## Cannot do yet

| Capability | Phase |
|---|---|
| Speaker diarization (who speaks when) | Deferred: see CHARTER 4.11 |
| Speed ramps, stabilisation, colour matching, multicam sync, green screen, PiP, split screen, cursor-following zooms, sponsor segments, A/B thumbnails, J/L cuts | Not built; candidates for later work (each needs its own render support and tests) |
