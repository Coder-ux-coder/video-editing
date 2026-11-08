# 07 B-roll, music, sound effects, thumbnail

## Purpose
Illustrate what is said with pictures the client may use, put music under the parts that need
it without ever fighting the voice, add the rare sound effect, and make a thumbnail that is
honest, readable at phone size and never covers the face. Zero budget: every asset is public
domain, CC0 or CC BY with its licence on record, or generated.

## Inputs
The captioned, graphics-complete timeline (HEAD), the transcript and topics, the brief (brand,
credit lines, references), `cutroom assets` (library), the style (`audio.music`).

## Procedure
1. **Find pictures.** `cutroom asset-fetch --query "<what is said>"` lists Wikimedia Commons files
   whose licence (read from Commons' metadata) is public domain, CC0 or CC BY; open the pages and
   download only pictures that show what the speaker is talking about: `--pick 1,3 --tag <words>`.
   Client-provided media go in the job's `inputs/` and are added with `asset-add` and the licence
   the client gave. Never use an image found elsewhere without a licence on record.
2. **Place b-roll.** `cutroom broll-suggest <job>` matches sentences to asset tags (evidence, not a
   decision). For each chosen moment: `cutroom broll-add <job> <asset> --at T --duration 3.5
   --move in|out|left|right --reason "…"`. Cut away after the speaker has been seen for a beat;
   2–5 s; the picture must show what is being said and never contradict it. Too-small stills are
   refused (no blurry upscales).
3. **Music.** Only where it helps: a cold open, a montage, the end. `cutroom music <job> --generate
   calm|documentary|upbeat|warm --from S --to E` (or `--asset` from the library). It sits 12 dB under
   speech and ducks 8 dB more while anyone talks; the render reports the measured speech-to-music
   ratio (must be ≥ 15 dB).
4. **Sound effects.** Sparingly: `cutroom sfx <job> --kind whoosh --transitions` puts one on each
   overlay transition; `--at T` for a single pop on a list item. Never one on every cut.
5. **Thumbnail.** `cutroom thumbnails <job> --text "<2–5 words the video supports>" -e <word>
   [--fact sentences:N]` makes candidates from the best face frames (title on the side away from the
   face, checked for size, weight, contrast and face overlap). Open them, then `--pick N`. No usable
   face (screen recording, product, landscape) or a better frame you found: `--at 1:23 --at 4:05`.
6. **Render and review** (playbook 09): b-roll lengths, licences and credits, music level and effect
   count are checked; look at the sheets around every cutaway.

## Decision rules
- Relevance beats decoration: no generic stock, no picture that only matches a keyword.
- Licences: public domain, CC0, CC BY only. CC BY credits go into the video description (delivery).
- Music never over speech louder than −15 dB; ends with fades or on phrase boundaries.
- Thumbnail text is a few words the video delivers; numbers must be said in the video.

## Outputs
A timeline with `broll`, `audio.music`, `audio.sfx` (each with licence and credit), a picked
`renders/thumbnail.jpg`, a review without blockers or majors.

## Failure handling
- Commons answers 429: it limits request rates per network; the fetcher waits as told. If it keeps
  refusing, try again later or use the assets you have.
- `broll-add` refuses a small picture: find a larger version, or skip it.
- Review `audio.music_level`: lower the bed (`music … --gain -6`) or shorten it.

## Checklist
- [ ] Every cutaway shows what is said; 2–5 s; licence on record
- [ ] Music only where it helps; ≥ 15 dB under speech (render report `mix`)
- [ ] Effects rare and purposeful
- [ ] Thumbnail picked: honest text, face clear, checks pass
- [ ] Credits collected for the description (CC BY)
