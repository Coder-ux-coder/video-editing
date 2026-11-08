import {interpolate, spring, Easing} from 'remotion';

/** The cue on screen at ``frame`` (cues are sorted and do not overlap). */
export const findCue = (cues, frame) => {
	let lo = 0;
	let hi = cues.length - 1;
	while (lo <= hi) {
		const mid = (lo + hi) >> 1;
		const c = cues[mid];
		if (frame < c.from) hi = mid - 1;
		else if (frame >= c.to) lo = mid + 1;
		else return c;
	}
	return null;
};

/**
 * Everything about a caption frame that changes over time, as plain numbers. Captions.jsx draws
 * a cue only from (cue, style, safe, size) and this state, so two frames of the same cue with an
 * equal state are identical pixels. cutroom's overlay frame cache keys caption frames on it
 * (graphics/caption_states.mjs), so a word that has finished popping is not drawn again for
 * every frame until the next word lights up.
 *
 *  opacity   cue opacity (entrance and exit)
 *  liftT     frames into the 'rise' entrance, clamped to its length (the lift is a function of it)
 *  cueScale  'pop' entrance scale of the whole cue (1 otherwise)
 *  active    index of the karaoke word lit (-1: none, or karaoke off)
 *  wordScale pop scale of the lit word (1 when settled)
 */
export const captionState = (cue, frame, style, fps) => {
	const inF = style.inFrames ?? 4;
	const outF = style.outFrames ?? 3;
	const t = frame - cue.from;
	const tOut = cue.to - frame;
	let opacity = 1;
	let liftT = 0;
	let cueScale = 1;
	if (style.entrance === 'fade' || style.entrance === 'rise') {
		opacity = Math.min(interpolate(t, [0, inF], [0, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)}),
			interpolate(tOut, [0, outF], [0, 1], {extrapolateRight: 'clamp', easing: Easing.in(Easing.cubic)}));
	}
	if (style.entrance === 'rise') liftT = Math.min(t, inF);
	if (style.entrance === 'pop') {
		const p = spring({frame: t, fps, durationInFrames: inF + 2, config: {damping: 13, stiffness: 160}});
		opacity = Math.min(1, t / Math.max(1, inF / 2));
		cueScale = interpolate(p, [0, 1], [0.86, 1]);
	}
	let active = -1;
	let wordScale = 1;
	if (style.karaoke) {
		const words = cue.lines.flat();
		active = words.findIndex((w, i) => frame >= w.from && (i === words.length - 1 ? frame < cue.to : frame < words[i + 1].from));
		if (active >= 0 && style.wordPop) {
			const p = spring({frame: frame - words[active].from, fps, durationInFrames: style.wordPopFrames ?? 6,
				config: {damping: 14, stiffness: 180}});
			// Grow into place (from smaller) rather than shrink from larger: a word scaled above 1 covers
			// the spaces beside it ("MYSURPRISE,"), and the pop lights up a new word every few frames.
			wordScale = Math.min(1, interpolate(p, [0, 1], [1 - style.wordPop, 1]));
		}
	}
	return {opacity, liftT, cueScale, active, wordScale};
};
