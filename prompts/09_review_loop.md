# 09 Review loop

## Purpose
Catch everything a careful editor would catch before a human sees the cut: render the edit, run
every automatic check, look at the frames, fix what can be fixed safely, and repeat (at most 4
rounds). Stop at the human gate with an honest report; never deliver from here.

## Inputs
The current timeline (HEAD) after captions and graphics, `analysis/` (words, shots, faces,
topics), the style file (thresholds), `docs/EDITING_STANDARDS.md` §13 (amateur tells).

## Procedure
1. **Review:** `cutroom review <job>`. It renders a preview (or reuses the render of that exact
   version), runs the checks and writes `review/vNNN/report.md`, `review.json`, a scorecard
   (0–10 per category: audio, cuts, captions, graphics, video, structure) and contact sheets
   (`sheet_NN.jpg`, a frame every 4 s; `issues.jpg`, a frame at every issue).
2. **Read the report top to bottom.** Severities: *blocker* (must not ship: wrong words on screen,
   mid-word cut, unsourced number, broken file), *major* (clearly visible or audible: loudness off,
   filler left in, flash shot, graphic on a face), *minor* (a careful editor fixes it), *polish*
   (taste; reported, never auto-fixed).
3. **Look at every sheet.** The checks cannot see taste: framing, expressions at cut points
   (eyes closed, mid-blink, awkward faces), continuity of gestures across cuts, whether graphics
   help, whether the opening grabs. Write down anything you see with its time.
4. **Fix automatically what is safe:** `cutroom review <job> --fix` repeats review → automatic
   fixes → review until no blocker or major remains that it can fix, or 4 rounds. Automatic fixes
   are ordinary cutroom commands (each a new version): remove-silence, remove-fillers, flash-shot
   trims/extensions onto camera changes, punch-in --auto, graphic-remove (unsourced or lost
   graphics), graphic-move (too short, on a face, outside the safe area), captions (missing or
   stale), audio (loudness, peaks).
5. **Fix the rest by judgement**, using the fix command printed under each issue: a flash shot
   holding a word (move the join), a weak opening (`move --sentences … --to 0`), a low-value topic
   (`cut --sentences`), a graphic without a source (re-add it with `--fact`), anything you saw on
   the sheets. Then `cutroom review <job>` again.
6. **Stop** when the report says *Ready for the human gate* (no blocker, no major) and your own
   look at the sheets found nothing, or after 4 rounds. Then report to the human: the scorecard,
   what was fixed (`cutroom versions <job>`), what remains and why, and the "Not judged
   automatically" list.

## Decision rules
- Never trade a blocker for a polish item; fix in severity order.
- An automatic fix that would change meaning (cutting words) is never automatic: the loop only
  removes fillers, pauses and word-free slivers, and moves or removes graphics.
- If a fix makes things worse (score drops, new majors), `cutroom undo <job>` and fix by hand.
- Four rounds at most: if issues remain, they need a person or a better tool; say so.

## Outputs
`review/vNNN/report.md` (+ `review.json`, sheets) for the final version, `review/loop.json` with
the rounds, a timeline version ready for the human gate.

## Failure handling
- A check seems wrong (false positive): note it with the evidence, do not "fix" it; propose a
  change to the threshold in the style file or to `EDITING_STANDARDS.md` §15.
- The render fails: follow the printed fix; `cutroom review <job> --no-render` still runs the
  checks that need no render.
- The loop stops with majors it could not fix: they are listed with a manual fix command.

## Checklist
- [ ] `cutroom review` (or `--fix`) run on the final version
- [ ] Every sheet looked at; observations written down
- [ ] No blocker, no major; minors fixed or explained
- [ ] Scorecard, changes and remaining limits reported to the human
