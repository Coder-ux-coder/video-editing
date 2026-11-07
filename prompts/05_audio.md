# 05 Audio polish

## Purpose
Make the voice clear, even and at the right loudness for YouTube, without audible processing.
Audio is more than half of perceived quality (EDITING_STANDARDS §5).

## Inputs
The rough cut (HEAD), `analysis/<src>/audio_report.json` (loudness, true peak, noise floor,
speech/noise ratio, clipping), the client style (`audio.target_lufs`, `audio.true_peak_dbtp`).

## Procedure
1. **Read the audio report** in `analysis/summary.md`. Note clipping (cannot be undone: tell the
   human if it is on important words), very low speech/noise (< 20 dB), hum, music under speech.
2. **Plan the chain:** `cutroom audio <job>`. It decides each stage from the measurements and prints
   a reason for each: high-pass; denoise only when speech/noise < 35 dB, stronger as it gets worse;
   gentle EQ; compression set from the measured speech level; light de-essing; loudness target
   (default −14 LUFS) and true-peak ceiling (−1 dBTP). Options: `--target-lufs`, `--denoise/--no-denoise`.
3. **Render and verify:** `cutroom render <job>` (preview). In the report, `loudness_on_target`
   (±1 LU) and `true_peak_ok` must pass. `audio_chain` shows before/after loudness and whether
   loudness was applied linearly (best) or dynamically.
4. **Listen by proxy.** You cannot hear, so check what can be measured: compare `cutroom loudness`
   on the preview with the target; look for flags in the report; if denoise ran at 9–12 dB, mention
   in the summary that a human should listen for warbly artefacts in quiet passages.

## Decision rules
- Prefer no processing over audible processing. A little room tone is natural; artefacts are not.
- Loudness is a delivery spec, not a taste: always on target for the final.
- Never raise the target above −14 LUFS for YouTube (it would only be turned down and lose dynamics).
- Clipped source audio: keep it, do not hide it; report it.

## Outputs
A timeline version with `audio.voice_chain`; renders whose loudness checks pass.

## Failure handling
- `loudness_on_target` fails: re-run `cutroom audio` (the source may have changed) and render again; if it still
  fails, report the measured numbers.
- Audio sounds over-compressed (very low LRA < 3 LU in the report): lower the ratio in a client style.

## Checklist
- [ ] Audio report read; clipping/noise issues noted
- [ ] `cutroom audio` reasons read and sensible
- [ ] Render checks `loudness_on_target` and `true_peak_ok` pass
- [ ] Anything a human should listen for is listed
