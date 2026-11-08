import React from 'react';
import {AbsoluteFill, Img} from 'remotion';
import {resolveTheme} from '../themes/theme.js';

/**
 * YouTube thumbnail (1280x720): a frame of the video, a gradient on the side away from the face,
 * and a short title in big type with one emphasised word. Python chooses the frame, the side and
 * the text; this only draws. Props: {image, text, emphasis: [words], side: 'left'|'right', theme}.
 */
export const Thumbnail = ({image, text = '', emphasis = [], side = 'left', theme: tokens = {}, width, height}) => {
	const theme = resolveTheme(tokens, width, height);
	const c = theme.colors;
	const words = text.split(/\s+/).filter(Boolean);
	const emph = new Set(emphasis.map((w) => w.toLowerCase()));
	const size = Math.round(height * 0.135);
	const grad = side === 'left'
		? 'linear-gradient(90deg, rgba(0,0,0,0.82) 0%, rgba(0,0,0,0.55) 45%, rgba(0,0,0,0) 70%)'
		: 'linear-gradient(270deg, rgba(0,0,0,0.82) 0%, rgba(0,0,0,0.55) 45%, rgba(0,0,0,0) 70%)';
	return (
		<AbsoluteFill style={{backgroundColor: '#000'}}>
			{image ? <Img src={image} style={{width: '100%', height: '100%', objectFit: 'cover'}}/> : null}
			<AbsoluteFill style={{background: grad}}/>
			<div style={{position: 'absolute', top: height * 0.1, bottom: height * 0.1, width: width * 0.5,
				[side]: width * 0.05, display: 'flex', alignItems: 'center'}}>
				<div style={{fontFamily: theme.fonts.heading, fontWeight: 900, fontSize: size, lineHeight: 1.02,
					letterSpacing: '-0.02em', color: c.text, textTransform: 'uppercase', textAlign: side,
					width: '100%', textShadow: '0 6px 28px rgba(0,0,0,0.6)'}}>
					{words.map((w, i) => (
						<span key={i} style={{color: emph.has(w.toLowerCase().replace(/[^\w']/g, '')) ? c.accent : c.text}}>
							{w}{i < words.length - 1 ? ' ' : ''}
						</span>
					))}
				</div>
			</div>
		</AbsoluteFill>
	);
};
