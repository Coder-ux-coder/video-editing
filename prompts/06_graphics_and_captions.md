# 06 Captions and motion graphics

## Purpose
Readable, accurate, well-timed captions in the client's style, burned in where wanted and always
delivered as `.srt`/`.vtt` files; then a few motion graphics that clarify, emphasise or brand,
each sourced, placed off the face, and in the client's theme. Part A is captions, part B graphics.

# Part A: captions

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

## Outputs (both parts)
A timeline version with captions and graphics; `.srt` and `.vtt` in `renders/`; renders whose caption
and graphics checks pass.

## Failure handling
- `captions_in_safe_zone` fails: the report lists frames and boxes; usually a very long word at a
  large size. Use a style with smaller `fontSizeRel` for that client, or a shorter `max_words`.
- `caption_sync_80ms` fails: check `worst` in the report; re-run `cutroom analyze <job> --force` (word
  timings) if a whole region is off.
- Remotion render fails: follow the printed fix (`npm ci` / `npx remotion browser ensure` in graphics/).

## Checklist (captions)
- [ ] Style chosen to fit the brief
- [ ] SRT proofread (names, numbers, brands)
- [ ] Render checks `captions_in_safe_zone` and `caption_sync_80ms` pass
- [ ] Captions looked at on frames; nothing covers the mouth
- [ ] `.srt` and `.vtt` exported for delivery

# Part B: motion graphics

## Inputs
The captioned timeline (HEAD), `analysis/topics.json` and `topics.task.md` (sentence numbers), the
brief (speaker names and titles, brand, CTA), documents the client provided in `inputs/`, the
client theme (`styles/clients/<client>.style.yaml`, `graphics.theme`), `cutroom templates`.

## Procedure
1. **Plan on paper first.** Read the topic map and pick only moments where a graphic helps the
   viewer: who is speaking (lower third, first time they appear), a key number (counter), a list
   the speaker enumerates (list reveal), the punchline (kinetic text, at most one or two per video),
   a topic change (transition or chapter card), one call to action near the end. Restraint: a
   10-minute talking head rarely needs more than 6-8 graphics. Note each with its sentence number.
2. **Look at the templates.** `cutroom templates` lists templates, variants and parameters;
   `cutroom template-preview [<template>] --client <client>` draws them in the client theme over
   a frame (`--background`) so you can see them before using them.
3. **Add each graphic with its source:**
   `cutroom graphic-add <job> <template> --sentence N [--duration S] [--variant V] -p key=value … -f <reference> --reason "…"`.
   References: `sentences:12` / `sentences:12-13` (what was said), `S01@83.2-90.5`, `t:1:23-1:30`
   (timeline), `brief`, `doc:inputs/<file>.md` (a document the client gave you).
   - **Numbers are verified**: every number shown (including numbers inside text and chart values)
     must appear in a reference, spoken or written ("ten thousand" = "10,000"), or the command
     refuses. Rounding to the shown precision is allowed; inflation is not. Never work around a
     refusal by changing the number to something not said.
   - **Words**: content words not found in the references are reported. Paraphrase is fine
     ("Chief Scientist, National Park Service"); new claims are not. Use the speaker's words.
   - Placement is automatic: inside the graphics safe area, above the caption band, on the side
     that covers the least of the face, away from other graphics. Kinetic text goes in the band that
     clears the head. Panel-less text gets a backing panel when the footage behind it is bright.
   - Hold time is enforced (1 s + 0.3 s per 3 words); at most 2 graphics at once (`--force` only
     with a stated reason).
   - Kinetic text, title and chapter cards hide burned-in captions while on screen: use them for
     the words being said, or over a pause (the command warns otherwise).
4. **Jump cuts:** `cutroom punch-in <job> --auto` alternates 100 %/112 % framing at every cut that
   stays on the same camera angle (joins on a camera change are left alone), centred on the face.
   `--clip <id> --scale 1.15 --push-to 1.2` for a single emphasis push; `--clear` to undo all.
5. **Transitions:** plain cuts by default. `cutroom transition <job> --at <time> --variant dip`
   only at a real change of topic, place or time.
6. **Persistent elements** if the brief wants them: `progress-bar`, `logo-bug` (client logo in
   `inputs/`, `-p src=inputs/logo.png --variant image`).
7. **Check:** `cutroom graphic-list <job>` (every graphic with its sources), `cutroom validate <job>`
   (flags graphics whose moment was cut and unsourced numbers), then range renders around each
   graphic: `cutroom render <job> --range A-B`. The report must pass `graphics_off_faces` and
   `graphics_in_safe_area` (both measured on the drawn pixels). Look at the frames.
8. **Theme:** `cutroom style <job> --client-style <client>` restyles every graphic and caption
   (colours, fonts, sizes, motion) without changing the edit.

## Decision rules
- A graphic must clarify, emphasise or brand. If it does none, do not add it.
- Never show a number or claim that is not in the transcript or a document the client provided.
- Never cover the face or mouth. If no placement clears the face, choose another moment.
- One focal element at a time; never more than two animating graphics.
- Graphics follow the edit: they are attached to the moment they appear, so later cuts move them.
  A graphic whose moment was cut is flagged by `validate`: move or remove it.

## Failure handling
- `graphic-add` refuses a number: point `--fact` at the sentence or document that states it; if no
  source states it, do not show it (ask the client for a source if it matters).
- "no anchor keeps it fully off the face": pick a moment in a wider shot, a smaller template
  (inline counter), or skip the graphic. The render check measures the real overlap.
- `graphics_off_faces` / `graphics_in_safe_area` fail in the render: `graphic-remove` and re-add at
  another moment or with `--anchor`; long text makes boxes wider than the estimate.

## Checklist (graphics)
- [ ] Each graphic has a purpose and a source (`graphic-list`)
- [ ] No refused or unsourced numbers; words are the speaker's
- [ ] Lower third on the speaker's first appearance; names spelled as in the brief
- [ ] Punch-ins on same-angle jump cuts; transitions only at real changes
- [ ] Range renders pass `graphics_off_faces` and `graphics_in_safe_area`; frames looked at
