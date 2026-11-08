# 08 Shorts

## Purpose
Turn the edited long-form video into YouTube Shorts (9:16, 1080x1920) that each carry one
self-contained idea, open on a hook, keep the speaker's face in frame, carry readable captions in
the Shorts safe zone, and come with an honest title, description and hashtags.

## Inputs
The long-form timeline after the review loop (HEAD), `analysis/topics.json` (topics marked as
Short candidates and hook lines), words, faces and camera changes, the brief (credit line,
names), the style file `shorts.*`.

## Procedure
1. **Find moments:** `cutroom shorts-find <job> --count 5`. It scores windows of whole
   sentences in natural speech order (the long edit may have moved lines) for a first line that
   stops the scroll, a clean ending, length (style `shorts.min_s`–`max_s`, default 20–58 s),
   one topic, and substance, and lists its reasons in `analysis/shorts.json`. With speakers detected,
   a window that includes another voice is marked down ("another voice speaks in it").
2. **Choose as an editor.** Read each pick's first and last line and its reasons, and the
   transcript around it (`analysis/topics.task.md`). Reject a pick that opens on a reference to
   something not shown ("That's…", "What does that say…"), ends on an open question, or stops
   before its payoff. Extend or move it with sentence boundaries; a Short may drop a leading
   "And"/"You know," by starting at the next word. Write down why for each.
3. **Create:** `cutroom short-create <job> --pick K` or `--from S --to E` (source seconds, between
   words). This makes job `<job>.short-NN`: same media (no copy), shared analysis, the parent's
   cleaned edit inside the range in speech order, framed 9:16 per clip: a still crop on a steady
   face, a slow speed-limited follow on a moving one, a jump at every camera change, a
   blurred-fill layout when there is no single clear face.
4. **Captions:** `cutroom captions <short> --style bold-karaoke --output youtube_short` (the Shorts
   safe zone keeps them clear of the buttons and the title area).
5. **Hook:** `cutroom short-hook <short> --text "<the speaker's own words, ≤ 48 chars>" --fact sentences:N`.
   The opening switches to the blurred-fill layout (frame in the middle, headline above) when
   the headline would otherwise touch the face, and switches back between words.
6. **Render and review:** `cutroom render <short>` then `cutroom review <short>` (Shorts checks: length,
   face kept inside the crop, plus all the usual ones). Open the sheets: the face must be in frame
   in every shot; the hook readable; captions not on the mouth. Fix and repeat (playbook 09).
7. **Publishing text:** `cutroom short-publish <short>` writes `publish.task.md`; write
   `publish.json` (title ≤ 100 characters, honest; 3–5 hashtags starting with `#Shorts`; description
   with the credit line from the brief); then `cutroom short-publish <short> --check`.
8. **Final:** `cutroom render <short> --mode final` for delivery (1080x1920).

## Decision rules
- One idea per Short; it must make sense to someone who has not seen the long video.
- The hook is what the speaker says, never a rewritten claim; numbers must be said in the Short.
- Never crop the face: if a shot cannot hold the face in 9:16 (two people, wide action), it uses
  the fill layout.
- Prefer 20–45 s; never over the platform limit (3 minutes).
- Shorts keep jump cuts (the accepted style); no punch-ins on vertical crops.

## Outputs
Jobs `<job>.short-NN`, each with a reviewed timeline, a preview and a final render, captions files,
and a checked `publish.json`.

## Failure handling
- `short-create` says nothing is kept in the range: the long edit cut it; choose another range.
- `short.face_cropped` in the review: the face leaves the window in a shot; re-create the Short
  (framing is recomputed) or give that clip the fill layout.
- `short-publish --check` refuses a number: say only what the Short says.

## Checklist
- [ ] Five (or the brief's number of) Shorts, each one idea with a hook and a payoff
- [ ] Hook in the speaker's words, sourced; never over the face
- [ ] Review: no blocker or major; sheets looked at
- [ ] `publish.json` checked (title, hashtags, credit)
- [ ] Final renders 1080x1920
