import React from 'react';
import {AbsoluteFill, Sequence} from 'remotion';
import {resolveTheme} from '../themes/theme.js';
import {Captions} from './Captions.jsx';
import {COMPONENTS} from './graphics/index.js';

/**
 * Everything drawn over the video in one pass: graphics (sorted by z) and captions on top.
 * Props: {theme, graphics: [{id, component, variant, from, to, params, placement, z, backing}], captions: {...} | null}
 */
export const Overlay = ({theme: tokens = {}, graphics = [], captions = null, width, height}) => {
	const theme = resolveTheme(tokens, width, height);
	const ordered = [...graphics].sort((a, b) => (a.z ?? 0) - (b.z ?? 0));
	return (
		<AbsoluteFill>
			{ordered.map((g) => {
				const C = COMPONENTS[g.component];
				if (!C) throw new Error(`Unknown graphic component ${g.component} (graphic ${g.id})`);
				const dur = Math.max(1, g.to - g.from);
				return (
					<Sequence key={g.id} from={g.from} durationInFrames={dur} layout="none">
						<C params={g.params ?? {}} variant={g.variant} theme={theme} placement={g.placement} duration={dur} backing={g.backing}/>
					</Sequence>
				);
			})}
			{captions && captions.cues?.length ? <Captions cues={captions.cues} style={captions.style} safe={captions.safe}/> : null}
		</AbsoluteFill>
	);
};
