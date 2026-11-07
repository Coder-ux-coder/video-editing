# 04 Rough cut

## Purpose
Turn the cut plan into a clean, tight edit: every cut on a word boundary, no clicks, every
decision logged, the result readable as an outline.

## Inputs
The cut plan, the analyzed job.

## Procedure
1. **Mechanical pass.** `cutroom rough-cut <job>`. It runs assemble → remove-silence → remove-fillers
   → detect-retakes (applies confidence ≥ 0.75) → preview render, saving one version per step.
   Style overrides come from the client style file (`editing.*`).
2. **Read the result.** `cutroom show <job>` (the outline). Check:
   - ✂ lines: does each cut's reason make sense? Restore wrong ones: `cutroom restore <job> x0012`.
   - ⟪quiet: …⟫ stretches (off-mic voices) and ⟨?word?⟩ marks.
   - Retakes kept the right take.
3. **Editorial cuts from the plan.** For each: `cutroom cut <job> --source S01 --from <s> --to <s> --reason "<why>"`.
   Use source times from the transcript; add `--snap` if an edge falls inside a word. Use `--dry-run` first when unsure.
   Low-confidence retakes you decided to cut: `cutroom detect-retakes <job> --apply --only S01-r005`.
4. **Validate.** `cutroom validate <job>` must pass (schema, overlaps, no mid-word cuts).
5. **Render and look.** `cutroom render <job>` (preview). All checks must be OK (duration, frames, no
   clicks, no edit lists). Then look at the edit around risky cuts:
   `cutroom frames <job> --at <t-0.2> --at <t> --at <t+0.2>` (timeline times) for mid-sentence jump cuts
   and the first/last seconds. Note jump cuts that need disguise later.
6. **Summarise.** `cutroom diff <job> v001` for the whole change; add a short summary to `log.md`:
   source length → edit length, cuts by kind, editorial decisions, open questions.

## Decision rules
- Prefer restoring a doubtful cut over keeping it: missing words are worse than a slightly long pause.
- If a filler removal makes speech sound rushed, restore it or lengthen the pause in the style.
- Two cuts closer than ~0.4 s apart can create a stutter: check them by eye (frames) and ear (preview).

## Outputs
A rough-cut version at HEAD that validates, a preview render whose checks all pass, log entries.

## Failure handling
- `validate` reports a mid-word cut: `cutroom restore` it or redo with `--snap`.
- Render check `no_clicks` fails: the click list has times; inspect, restore or adjust the cut (joins already fade).
- Duration check fails: report it (should not happen); do not deliver.

## Checklist
- [ ] Every ✂ reason read; wrong cuts restored
- [ ] Editorial cuts from the plan applied
- [ ] `cutroom validate` passes
- [ ] Preview render: all checks OK
- [ ] Risky cuts looked at; jump cuts needing disguise listed
- [ ] Summary in `log.md`
