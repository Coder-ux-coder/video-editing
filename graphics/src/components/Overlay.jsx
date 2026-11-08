import React from 'react';
import {AbsoluteFill, Freeze, Sequence, useCurrentFrame} from 'remotion';
import {resolveTheme} from '../themes/theme.js';
import {Captions} from './Captions.jsx';
import {COMPONENTS} from './graphics/index.js';

/**
 * Everything drawn over the video in one pass: graphics (sorted by z) and captions on top.
 * Props: {theme, graphics: [{id, component, variant, from, to, params, placement, z, backing}], captions: {...} | null}
 * frameMap (optional): output frame i shows composition frame frameMap[i], so cutroom's overlay
 * frame cache draws exactly the frames it does not have yet, in one pass.
 */
export const Overlay = ({frameMap = null, ...props}) => {
	const frame = useCurrentFrame();
	if (!frameMap) return <OverlayFrame {...props}/>;
	return <Freeze frame={frameMap[frame]}><OverlayFrame {...props}/></Freeze>;
};

const OverlayFrame = ({theme: tokens = {}, graphics = [], captions = null, width, height}) => {
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
