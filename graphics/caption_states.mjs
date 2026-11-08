// The caption state of every overlay frame, for cutroom's overlay frame cache.
//
//   node caption_states.mjs --props props.json --out states.json
//
// Writes a JSON list, one entry per composition frame: null (no caption on screen) or
// [cue index, state] with state from src/components/captionState.js, the same function
// Captions.jsx draws from. Two frames of one cue with equal states are identical pixels.
import {readFileSync, writeFileSync} from 'node:fs';
import {captionState, findCue} from './src/components/captionState.js';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
	if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1]]);
	return acc;
}, []));
const props = JSON.parse(readFileSync(args.props, 'utf-8'));
const cues = props.captions?.cues ?? [];
const style = props.captions?.style ?? {};
const index = new Map(cues.map((c, i) => [c, i]));
const out = [];
for (let f = 0; f < props.durationInFrames; f++) {
	const cue = cues.length && style.fontFamily ? findCue(cues, f) : null;
	out.push(cue ? [index.get(cue), captionState(cue, f, style, props.fps)] : null);
}
writeFileSync(args.out, JSON.stringify(out));
