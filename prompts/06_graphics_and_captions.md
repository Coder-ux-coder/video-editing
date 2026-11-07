# 06 Captions (and, from Phase 3, motion graphics)

## Purpose
Readable, accurate, well-timed captions in the client's style, burned in where wanted and always
delivered as `.srt`/`.vtt` files. (Motion graphics are added to this playbook in Phase 3.)

## Inputs
The audio-polished timeline (HEAD), refined words, faces (`analysis/<src>/faces.json`), the client
style (`captions.*`), the brief (burned-in or files only; long-form or Short).

## Procedure
1. **Choose the style.** `clean` (calm, documentary, corporate), `bold-karaoke` (energetic, Shorts),
   `word-highlight` (tutorials, explainers), `minimal` (interviews where captions should recede).
   The client style may define its own. Shorts use `--output youtube_short` (different safe zone and line length).
2. **Plan:** `cutroom captions <job> --style <name> [--output youtube_short] [--files-only]`. It
   phrases captions by sense (never ends a line on "the", "of", "during", "they"…; keeps names and
   numbers with their words; breaks before function words), keeps ≤ 2 lines, times them to the
   refined words (fillers left out), adds at most one emphasised word, moves captions off the mouth,
   and adds a scrim when the background is too bright for contrast.
3. **Read the stats** printed (captions, median and max characters/second, how many exceed 17 cps,
   scrims, captions moved off faces). Fast speakers exceed 17 cps: captions follow speech, so this
   is reported, not "fixed" by dropping words.
4. **Proofread.** `cutroom export-captions <job>` and read `renders/vNNN.srt` top to bottom: names,
   numbers, brands (glossary), and any ⟨?word?⟩ that slipped through. Fix transcript errors at the
   source (re-transcribe with `--hotword`), then re-run captions.
5. **Render and check:** `cutroom render <job>` (or `--range A-B` to check a section quickly). The
   report must pass `captions_in_safe_zone` (measured from the drawn pixels) and
   `caption_sync_80ms` (caption appearance vs speech onsets in the rendered audio).
6. **Look.** `cutroom frames <job> --at …` on a few captions, especially the first one, long
   captions, and any with a scrim: legible at phone size, not covering the face, consistent.

## Decision rules
- Accuracy beats style: a misspelled name in a caption is a delivery blocker.
- Captions are clean verbatim: fillers out, words as spoken otherwise, sentence starts capitalised.
- One caption style per video. Emphasis only on genuinely key words (style decides colour/scale).

## Outputs
A timeline version with captions; `.srt` and `.vtt` in `renders/`; renders whose caption checks pass.

## Failure handling
- `captions_in_safe_zone` fails: the report lists frames and boxes; usually a very long word at a
  large size. Use a style with smaller `fontSizeRel` for that client, or a shorter `max_words`.
- `caption_sync_80ms` fails: check `worst` in the report; re-run `cutroom analyze <job> --force` (word
  timings) if a whole region is off.
- Remotion render fails: follow the printed fix (`npm ci` / `npx remotion browser ensure` in graphics/).

## Checklist
- [ ] Style chosen to fit the brief
- [ ] SRT proofread (names, numbers, brands)
- [ ] Render checks `captions_in_safe_zone` and `caption_sync_80ms` pass
- [ ] Captions looked at on frames; nothing covers the mouth
- [ ] `.srt` and `.vtt` exported for delivery
