# Editing standards

What "professional" means for Cutroom, written so an agent can apply it and a person can
check it. Consulted on every job. A living document: after each job, the review loop
proposes changes (section 15).

**How to read the evidence tags**

- **[Official]**: published by the platform or standards body (linked).
- **[Consensus]**: widely documented industry practice; sources agree, but no single official spec.
- **[Default]**: Cutroom's professional default where practice varies; tuned per client via the style file.

Numbers in `code` are keys in `styles/default.style.yaml`; tools read them from there, so
changing the style changes the behaviour. Last reviewed: 2026-11-07.

---

## 1. First principles

1. **Emotion and story beat technique.** A cut that serves what the viewer feels and
   understands is right even if it breaks a rule; a technically perfect cut that loses the
   thread is wrong. Walter Murch's ranking (emotion, then story, then rhythm, then eye
   trace, then screen geometry) is the tiebreaker when rules conflict.
   ([Murch, *In the Blink of an Eye*, 2001] [Consensus])
2. **Every second must earn its place.** If removing something loses nothing, remove it.
3. **Invisible craft.** The viewer should notice the content, not the editing. Visible
   techniques (punch-ins, graphics, SFX) are used when they clarify or emphasise, never by habit.
4. **Never change meaning.** Cuts may tighten what someone said; they must never make
   them say something they did not mean. Numbers, claims and corrections are never cut on
   inference alone: a person confirms (Cutroom gives such retakes confidence ≤ 0.5).
5. **Consistency per client.** One look, one type system, one motion language, one
   loudness target per client, set in the style file.

## 2. Story and hook

| Rule | Default |
|---|---|
| The first 3–8 seconds earn the watch: open on the most compelling line, question or visual, then give context. | `pacing.hook_window_s: [3, 8]` [Consensus] |
| Cut greetings and preamble ("hey guys, welcome back, before we start…") unless the client's format requires them. | [Consensus] |
| Tease the payoff early ("by the end you'll know…") only if the video really delivers it. | [Default] |
| Re-hook at likely drop points (topic changes, after long explanations): a question, a visual change, a reveal. | [Consensus] |
| End with a clear payoff and **one** call to action; no long outro. | [Consensus] |
| Structure: hook → context (why this matters) → body in topic order of value → payoff → CTA. Low-value topics (usefulness 1–2 in `topics.json`) are cut first when trimming for time. | [Default] |

Cutroom: the agent proposes hook candidates in the cut plan (`prompts/03_story_and_cut_plan.md`)
from `topics.json` (`hook_candidate: true`) and the pacing map.

## 3. Pacing by format

Footage type is detected by `cutroom analyze` (a guess with evidence) and confirmed by the agent.

| Format | Visual change rhythm | Specifics |
|---|---|---|
| Long-form talking head | A visual change (cut, punch-in, graphic, b-roll) every ~3–6 s on average, varied, never metronomic; no stretch over `pacing.max_static_s` (12 s) without one. | Tighten pauses; disguise jump cuts (section 4). [Consensus/Default] |
| Podcast / interview | Stay on whoever is speaking; cut to reactions when they add meaning; minimal graphics. | Keep natural turn-taking; remove interviewer questions when the answer stands alone; keep laughter that lands. [Consensus] |
| Tutorial / screen recording | Cut dead time (loading, typing that doesn't teach); zoom to the action; a callout for every UI action the viewer must copy. | Never cut a step the viewer needs to follow. [Consensus] |
| Vlog / B-roll heavy | Edit to music and story beats; shots 1–4 s. | Sound design carries continuity. [Consensus] |
| Shorts | A new visual beat every 1–3 s; one self-contained idea of 20–45 s (style allows up to 58 s; platform limit 3 min); hook in the first second, shown as a headline in the speaker's words; never opens on a line that needs earlier context; ends on a complete line, not an open question. | [Consensus]; Cutroom's finder scores these (`shorts-find`) |

Speech rate: most natural presenters sit at roughly 130–170 words per minute; the pacing map
(`analysis/<src>/pacing.json`) shows slow or flat stretches for the agent to tighten or cover.

## 4. Cutting craft

| Rule | Default / how Cutroom applies it |
|---|---|
| Cut on word boundaries, never inside a word. Cuts land in pauses; where words touch, at the quietest point between them. | Refined word timings (`words.json`), valley snapping (±40 ms); `cutroom validate` fails on mid-word cuts. |
| Keep pads so consonants and decays are not clipped. | `pad_before_word: 0.06`, `pad_after_word: 0.10` s |
| Shorten long pauses, don't eliminate them: rhythm needs air. Longer after a sentence end than mid-sentence. | Pauses over `max_pause` (0.5 s) shortened to `keep_pause_mid_sentence` (0.18 s) or `keep_pause_sentence_end` (0.32 s) |
| Protect dramatic pauses: a long pause before a short, punchy sentence is often deliberate. | Kept at `dramatic_pause_keep` (0.55 s), confidence 0.7 so the agent reviews it |
| Breaths: keep natural breaths in calm styles; remove in fast styles; never cut halfway through a breath. | Cuts never split a breath or any other sound |
| Remove hesitation fillers (um, uh, er, hmm) always; discourse markers (like, you know, I mean) only when isolated by commas and only if the style allows. Meaningful uses ("I like it", "you know him") are protected. | `fillers_always`, `fillers_contextual`, `remove_contextual_fillers: false` |
| Stumbles and false starts: keep the clean attempt. Retakes: keep the **last complete** take unless it is clearly worse (shorter, mumbled). | `detect-retakes`; parallel structure ("how to A and how to B"), lists, spelled names and number corrections are protected |
| No orphaned fragments: nothing under 0.4 s left between cuts unless it contains a whole word. | `min_clip: 0.40` |
| A jump cut inside a continuous sentence must be disguised (punch-in of ~110–120 %, b-roll, graphic) unless jump cuts are the client's style. Between sentences, an undisguised jump cut is acceptable in YouTube style. | `punch-in --auto`: alternate 100 % / 112 % (`graphics.rules.punch_in_scale`) at every same-angle jump cut, crop centred on the face with the head kept in frame; joins on a camera change and clips < 0.8 s are left alone |
| On multi-camera material, prefer cutting at a camera change; it hides the edit. | `analysis/<src>/visual_report.json` lists shots |
| J-cuts and L-cuts (audio leads or trails the picture) for natural dialogue flow and transitions. | Supported by the timeline model; tools in a later phase |
| Click-free joins: every audio join gets a short fade on both sides. | `join_fade: 0.008` s raised-cosine; render checks every join |

## 5. Audio

Audio is more than half of perceived quality: viewers forgive soft video, not bad sound. [Consensus]

**Processing order** [Consensus]: clean-up (noise reduction, hum removal) → EQ (high-pass around
70–100 Hz for voice, gentle presence) → compression (2–4:1, a few dB of gain reduction) →
de-ess → loudness normalisation → true-peak limiter. Each step is subtle; artefacts
(warbly denoise, pumping) are worse than the noise they remove.

Cutroom's chain (`cutroom audio`) decides each stage from measurements: denoise only below
35 dB speech/noise; compressor threshold 8 dB under the measured speech level; loudness by a
single linear gain to target followed by a limiter that runs at 4× the sample rate (so peaks
between samples count) with 1 dB of extra headroom for the AAC encoder. No dynamic loudness
riding: dynamics stay natural. Every render measures the final file.

| Target | Value | Evidence |
|---|---|---|
| Long-form integrated loudness | **−14 LUFS** ±1 LU (`audio.target_lufs`) | YouTube normalises playback to about −14 LUFS and turns louder uploads down ([Production Advice](https://productionadvice.co.uk/youtube-music/), [Meterplugs](https://meterplugs.com/blog/2019/09/18/youtube-changes-loudness-reference-to-14-lufs.html)). [Consensus; YouTube does not publish this officially] |
| Speech-only reference elsewhere | −18 LUFS (podcast feeds), −17 LUFS mixed content | AES TD1008 as summarised by [Radio World](https://www.radioworld.com/?p=115138) and [Telos](https://docs.telosalliance.com/docs/understanding-loudness-for-streaming-audio) [Consensus] |
| True peak | **≤ −1 dBTP** (`audio.true_peak_dbtp`) | ITU-R BS.2434 lists −1 dBTP for distribution ([ITU](https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-BS.2434-2018-PDF-E.pdf)); some engineers prefer −2 to −3 dBTP before lossy encoding ([Production Advice](https://productionadvice.co.uk/td1008/)). [Official/Consensus] |
| Music under speech | −18 to −24 dB below the voice, ducked under speech, never abruptly started or stopped | [Consensus] |
| Speaker consistency | Speakers and shots within ±1.5 LU of each other | [Default] |
| SFX | Subtle, tied to on-screen motion, consistent palette; never on every cut | [Consensus] |
| Delivery audio | AAC-LC (or Opus) stereo, 48 kHz | [YouTube upload settings](https://support.google.com/youtube/answer/1722171) [Official] |

## 6. Captions

| Rule | Value | Evidence |
|---|---|---|
| Lines per caption | 1–2 lines; short-form 3–5 words per phrase | 2 lines max in professional subtitle guides ([Netflix Timed Text Style Guide](https://backlothelp.netflix.com/hc/en-us/articles/215758617)) [Official for Netflix; Consensus for YouTube] |
| Line length | ≤ 42 characters (long-form); much shorter in Shorts | Netflix English guide as reported by multiple sources [Consensus] |
| Reading speed | Target ≤ **17 characters/second**; never above 20 | 20 cps (adult) / 17 cps (children) commonly attributed to Netflix's English guide; sources disagree, so the stricter figure is the default [Consensus] |
| Minimum on screen | 1 s plus ~0.3 s per 3 words (text graphics); captions follow speech | [Default] |
| Sync | Word start within **80 ms** of the audio | Brief requirement; refined word timings measured at 17 ms mean / 70 ms worst on ground truth |
| Break lines and captions at phrase boundaries: before function words ("that", "and", "in"), after commas; never after an article, preposition, conjunction, auxiliary or subject pronoun ("…welcome **to** / the clip", "older **than they** / have"); never inside a name ("White / Sands") or between a number and its unit ("400 / grams") | Cutroom solves this per sentence as an optimisation over all possible splits | [Consensus] |
| A pause inside a phrase ("at White Sands during [pause] the last ice age") does not split the phrase unless it is long (≥ 1 s); a pause between phrases always does | `break_on_pause: 0.35` | [Default] |
| Clean verbatim: fillers left out of captions, sentence starts capitalised even when a filler was removed | | [Consensus] |
| Emphasis | At most one emphasised word per phrase, only genuinely key words | [Default] |
| Contrast | ≥ 4.5:1 text/background (add a subtle shadow or scrim over busy video) | [WCAG 2.x SC 1.4.3](https://a11ybot.pages.dev/learn/1-4-3-contrast/) [Official for web; adopted here] |
| Placement | Never over the face or mouth; above platform UI zones (section 10) | [Consensus] |
| Accuracy | Names, brands and numbers checked against the client glossary; uncertain words resolved before delivery | |
| Files | Always deliver `.srt` and `.vtt` | Accessibility (section 11) |

## 7. Motion graphics

1. **Purpose over decoration.** Every graphic clarifies, emphasises or brands. If it does none, cut it.
2. **Hierarchy.** One focal element at a time; never more than two things animating at once on a talking head.
3. **Consistency.** Same easing, timing, type system and colour tokens for the whole video (from the style file).
4. **Timing.** Entrances 8–14 frames with ease-out or spring; exits 6–10 frames with ease-in; stagger
   multiple items by 3–6 frames; hold text at least 1 s plus ~0.3 s per 3 words. [Default]
5. **Never cover the face or mouth.** Graphics are placed using the face track (`analysis/<src>/faces.json`).
6. **Facts on screen come from sources.** Every number or claim in a graphic carries a reference in
   the timeline (`graphics[].facts`): a transcript span or a document the client provided. Nothing is invented.
7. **Restraint.** Fewer, better graphics beat many.
8. **Contrast.** Text without its own panel gets one when the theme's text colour would fall under
   WCAG 4.5:1 (accent/large text 3:1) against the footage behind it, at its darkest or brightest
   look (`graphics/contrast.py`), the same idea as caption scrims. Theme tokens are checked too.
9. **Captions stay readable.** Graphics sit above the caption band; only graphics that show the
   words being said (kinetic text) or full-frame cards hide burned-in captions, and the caption
   files always stay complete.
10. **Transitions are rare.** Plain cuts by default; a dip, flash or whip marks a real change of
    topic, place or time. Never a different transition at every cut.

## 8. Colour and look

- Consistent exposure and white balance across shots; skin tones natural and safe from clipping.
- Judge exposure on the **subject**, not the frame: a bright beach or white sand behind a well-lit
  face is a look, not a fault (Cutroom measures face luma and face clipping).
- One look (LUT/grade) per project; match shots to a hero shot rather than grading each in isolation.
- Deliver SDR Rec.709 unless HDR is explicitly required.

## 9. B-roll and music

| Rule | Default |
|---|---|
| B-roll appears within ~1 s of the idea it illustrates and lasts 2–5 s; cut away after the speaker has been seen for a beat. | [Consensus]; review flags < 1.5 s or > 8 s |
| Stills move slowly (Ken Burns, ≤ 10 % zoom or a small pan over the cutaway); never enlarge a small picture more than ~1.8×. | [Consensus]; `broll-add` refuses small pictures |
| B-roll never contradicts what is said; avoid cliché stock (handshakes, pointing at screens) unless ironic. | [Consensus] |
| Every asset has a recorded free licence: public domain, CC0 or CC BY (attribution in the description); share-alike, non-commercial and no-derivatives licences are refused for client work; otherwise generate or skip. | Brief requirement; `assets/library` sidecars |
| Music matches energy, ducks under speech, starts and ends on phrase boundaries or with fades. Cutroom: 12 dB under speech in pauses, a further 8 dB while anyone speaks (look-ahead 150 ms, release 500 ms); the render measures the speech-to-music ratio during speech and the review requires ≥ 15 dB. | [Consensus: commonly −15 to −25 dB under dialogue]; style `audio.music` |
| Sound effects are rare and motivated (a transition, a list beat); never one on every cut. | [Consensus]; review flags overuse |
| Thumbnail: 1280×720, under 2 MB, a few words the video delivers, readable at phone size (≥ 3:1 against what is behind the glyphs), never over the face. | YouTube limits (§10); `thumbnails` checks |

## 10. Platform rules (YouTube only)

| Item | Requirement | Evidence |
|---|---|---|
| Container | MP4, moov atom at the front (fast start), **no edit lists** | [YouTube upload settings](https://support.google.com/youtube/answer/1722171) [Official] |
| Video | H.264, progressive, 4:2:0, variable bitrate, source frame rate kept | [Official] |
| Audio | AAC-LC or Opus, stereo, 48 kHz | [Official] |
| Long-form frame | 16:9, 1920×1080 default; 3840×2160 when the source is 4K | Brief |
| Shorts frame | 9:16, 1080×1920 | Brief |
| Shorts length | Square or vertical videos up to 3 minutes uploaded on or after 15 Oct 2024 are Shorts; longer ones are long-form | [YouTube Help: Understand three-minute YouTube Shorts](https://support.google.com/youtube/answer/15424877) [Official] |
| Shorts framing | A 9:16 window on the speaker's face (still when steady, slow follow when moving, a cut at every camera change); a blurred-fill layout when there is no single clear face; never crop a face | [Consensus]; `short-create` |
| Shorts safe zones | Keep text and faces out of the right-hand action column (~19 % of width) and the bottom title/subscribe area (≥ ~20 % of height; some guides say up to ~35 %). Captions sit in the middle band. Check on a real phone. | Third-party templates disagree ([adkit](https://adkit.so/tools/safe-zones/youtube), [vidpros](https://vidpros.com/vertical-video-safe-zone/)) [Consensus, conservative] |
| Thumbnails | 1280×720 (16:9), minimum width 640; under 2 MB (JPEG) | [YouTube thumbnails help](https://support.google.com/youtube/answer/72431) [Official for size; 2 MB per secondary sources] |
| Chapters | First chapter at 0:00, at least 3 chapters, each ≥ 10 s, in ascending order | Commonly documented ([Dickinson Media Center](https://blogs.dickinson.edu/mediacenter/2026/01/12/youtube-adding-video-chapters/)) [Consensus; verify against YouTube Help] |

## 11. Accessibility

- Captions always available as `.srt` and `.vtt`, accurate to the words spoken.
- Text contrast ≥ 4.5:1 ([WCAG 1.4.3](https://a11ybot.pages.dev/learn/1-4-3-contrast/)).
- **No flashing more than three times per second** (glitch transitions, strobing graphics)
  ([WCAG 2.3.1](https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html)) [Official].
- Do not rely on colour alone to convey meaning in graphics.

## 12. Delivery quality

- Finals encoded once from full-resolution sources with software x264 (best quality per bit).
- Loudness and true peak measured on the final file, not assumed.
- Duration and frame count checked against the timeline; every audio join checked for clicks.
- The QA report lists what could not be verified automatically (taste, factual accuracy, lip-sync under very fast edits).

## 13. Common amateur tells (the review loop checks for these)

1. A greeting or preamble before the first interesting line.
2. Constant zoom punches on every cut.
3. A whoosh or swoosh on every transition.
4. Mismatched fonts, or more than two type families.
5. Captions covering the face or mouth.
6. Uneven loudness between clips or speakers; music fighting the voice.
7. Music that starts or stops abruptly mid-phrase.
8. Text on screen too briefly to read (or far too long).
9. Long intros, logo stings over 3 s, or an outro longer than the CTA needs.
10. Inconsistent graphic timing and easing.
11. Random transition types (spins, page curls, glitch) with no purpose.
12. Clicks or pops at cuts.
13. Breaths or words cut in half.
14. Jump cuts inside sentences with nothing to disguise them.
15. Dead air: long pauses left in, or no air at all (breathless, robotic rhythm).
16. Over-processed audio: warbly noise reduction, pumping compression, harsh de-essing.
17. Clipped highlights on faces; inconsistent colour between cameras.
18. Graphics that decorate instead of inform; stock footage clichés.
19. Typos and misspelled names in captions or titles.
20. Fake or unsupported numbers on screen.
21. Caption styles that change mid-video.
22. Hook promises the video never delivers (clickbait).
23. Vertical video with letterboxing or unblurred black bars.
24. Low-resolution graphics or stretched images.
25. Flashing or strobing effects (also an accessibility risk).
26. Several CTAs fighting each other instead of one clear ask.

How `cutroom review` covers these today (Phase 4): **automatic check** for 1 (weak opening), 5
(captions placed off the mouth; safe zone measured), 8 (graphics too short), 12 (clicks), 13 (cuts
inside words, words within 15 ms of a cut), 14 (undisguised mid-sentence jump cuts), 15 (pauses
the style would still shorten), 19 partly (captions out of step with the words), 20 (unsourced
numbers), 21 (one caption style per timeline by design), 6 partly (level jumps between distant
takes; loudness on target). By design rather than by check: 4 and 10 (design tokens), 2
(punch-ins only at same-angle jump cuts). **Looked at by the agent** on the contact sheets: 3, 7,
9, 11, 16, 17, 18, 22–26 (music, b-roll and Shorts arrive in Phases 5–6, with their own checks).

## 14. What automation cannot judge (and how Cutroom compensates)

| Cannot judge reliably | Compensation |
|---|---|
| Taste, humour, whether a moment "lands" | Agent reads the transcript in full and looks at contact sheets; human approves the cut plan for new clients and the final render. |
| Factual accuracy of claims | Never adds facts; graphics carry source references; sensitive claims flagged in transcript review. |
| Brand nuance and client politics | Client style file and brief; revision workflow. |
| Intent behind repetition (emphasis vs stumble) | Confidence scores; low-confidence retakes are suggestions for a person, never auto-applied. |
| Who is speaking on a single microphone | Agent identifies interviewer questions from content; diarization planned (Phase 5). |

## 15. Changes to these standards

Proposed after each job by the review loop (`prompts/09_review_loop.md`), with the evidence
that motivated them. Accepted changes update this file and the default style.

| Date | Change | Why |
|---|---|---|
| 2026-11-07 | Exposure judged on the face, not the whole frame | White Sands interview: bright sand behind a well-lit face was falsely flagged as overexposed |
| 2026-11-07 | Repeated phrases after a conjunction, lists, spelled names and number corrections are not retakes | 10-min interview: parallel structure ("how to document it and how to preserve it") and a number list were wrongly detected as false starts |
| 2026-11-07 | Self-corrections keep the added words ("human tracks, fresh human tracks" → drop only "human tracks,") | Same interview |
| 2026-11-07 | Caption phrasing rules (function-word breaks, names, numbers, pronouns, mid-phrase pauses) | Interview captions read badly with "fill up to N words": "…during / the last…", "White / Sands", "older than they / have" |
| 2026-11-07 | Loudness by linear gain + oversampled limiter, 1 dB encoder headroom | Dynamic loudness normalisation plus AAC encoding gave −0.5 dBTP (over the −1 dBTP limit) on the interview |
| 2026-11-07 | Backing panel for panel-less graphics over bright footage (§7.8) | Interview: a yellow counter over bright sky and sand was unreadable |
| 2026-11-07 | Kinetic text goes in the band that clears the head, low by default (§7.9) | Interview: centred kinetic text sat just under the speaker's mouth |
