import React from 'react';
import {Composition} from 'remotion';
import {Captions} from './components/Captions.jsx';
import {Overlay} from './components/Overlay.jsx';
import {ensureFonts} from './fonts.js';

ensureFonts();

// Every composition takes all of its size, timing and styling from input props, so the
// Python side (cutroom) fully controls what is drawn. Defaults only make the Studio usable.
const fromProps = ({props}) => ({
	width: props.width ?? 1920,
	height: props.height ?? 1080,
	fps: props.fps ?? 30,
	durationInFrames: Math.max(1, props.durationInFrames ?? 90),
});

export const Root = () => (
	<>
		<Composition
			id="Captions"
			component={Captions}
			defaultProps={{cues: [], style: {}, safe: {}}}
			calculateMetadata={({props}) => fromProps({props})}
			width={1920}
			height={1080}
			fps={30}
			durationInFrames={90}
		/>
		<Composition
			id="Overlay"
			component={Overlay}
			defaultProps={{theme: {}, graphics: [], captions: null}}
			calculateMetadata={({props}) => ({...fromProps({props}), props: {...props, width: props.width ?? 1920, height: props.height ?? 1080}})}
			width={1920}
			height={1080}
			fps={30}
			durationInFrames={90}
		/>
	</>
);
