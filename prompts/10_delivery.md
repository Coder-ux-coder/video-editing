# 10 Delivery and revisions

## Purpose
Hand the client a complete, checked package for one version, wait for a person's approval, and
turn their notes into traceable edits. Cutroom prepares; a person approves. Never the agent.

## Inputs
The reviewed timeline (HEAD, ready for the human gate), its Shorts, the topic map, the brief
(credit lines, names), the picked thumbnail.

## Procedure
1. **Where are we?** `cutroom next <job>` lists every stage as done or not and the next command.
2. **Publishing text.** `cutroom publish <job>` writes `publish.task.md` (chapters proposed from the
   topic map, credits collected). Write `publish.json`: an honest title (≤ 100 characters), a
   description in the speaker's terms, chapters (first at 0:00, at least 3, each ≥ 10 s; rename
   duplicates and chapters whose content was cut), up to 3 hashtags, tags. `cutroom publish <job>
   --check` validates it, writes the chapters into the timeline and `delivery/description.txt`.
   Do this after the last edit (chapters are times in the edit).
3. **Final render.** `cutroom render <job> --mode final` (1080p, or 4K from 4K sources; x264, high
   quality). Every self-check must pass. Then `cutroom review <job>` (it reuses the final render):
   no blocker, no major; accepted issues carry their reason.
4. **Shorts.** Each Short needs its final render, a clean review and a checked `publish.json`
   (playbook 08).
5. **Interchange.** `cutroom export <job>` writes OTIO (Resolve), FCPXML 1.10 (Resolve, Final Cut)
   and a CMX3600 EDL (Premiere, Avid), each verified by reading it back; captions as SRT.
6. **Open questions.** Write `questions.md` in the job folder: one numbered question per item that
   needs the person (a fact the speaker corrected, a title nobody confirmed, a count the brief left
   blank), each with your recommended answer. Revision notes you answered with a question
   ("Question for you: …") are collected automatically.
7. **Package.** `cutroom deliver <job>` refuses until everything above holds, then assembles
   `delivery/<job>_vNNN/`: the video, captions (SRT/VTT), thumbnail, title and description, Shorts
   with their texts, NLE files, `REPORT.md` (checks, scorecard, accepted issues, licences),
   `MANIFEST.json`, `checksums.sha256`, and `APPROVAL.md`. Status: AWAITING APPROVAL. The open
   questions head `REPORT.md` and are pointed to from `APPROVAL.md`.
8. **Stop and tell the person.** Summarise in plain language: what the video is, its length,
   what was cut and why (versions), what you could not judge, the accepted issues, the open
   questions, and where the package is. Ask them to watch it. Do **not** run `cutroom approve`.
9. **Revisions.** When notes come back: `cutroom revise <job> --notes notes.txt --seen vNNN`. Each
   note's times are mapped from the version they watched to the current edit, with what is said and
   on screen there (`review/revisions/rNN/task.md`). For every note write commands and/or a response
   in `plan.json`: do what is asked when it is clear and safe; ask when it needs facts you do not
   have (a title, a number, a spelling); decline with the reason when it would break a rule
   (e.g. an unsourced claim). `cutroom revise <job> --apply rNN` runs the plan; then re-plan
   captions if words moved, re-run publish, render, review and deliver again. Send the person
   `report.md` (note → what was done → version).

## Decision rules
- The package is never sent anywhere by Cutroom; the person delivers it.
- Every client note gets an answer: done, question, or declined with the reason.
- Titles, names and numbers come from the brief, the transcript or a document; never assumed.

## Outputs
`delivery/<job>_vNNN/` awaiting approval; revision reports under `review/revisions/`.

## Failure handling
- `deliver` lists what is missing: fix each item and run it again.
- A note's moment was cut since: the task says so; answer it from the context.

## Checklist
- [ ] `cutroom next` shows only approval left
- [ ] Publishing text checked after the last edit
- [ ] Final render and review clean (accepted issues explained)
- [ ] Open questions in `questions.md`, numbered, each with a recommendation
- [ ] Package prepared; person told what to watch; no self-approval
- [ ] Revision notes each answered, with versions
