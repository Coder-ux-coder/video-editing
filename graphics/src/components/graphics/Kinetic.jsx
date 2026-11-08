import React from 'react';
import {AbsoluteFill, interpolate, spring} from 'remotion';
import {backingStyle, useMotion} from '../../themes/theme.js';

/** A key phrase, word by word. Variants: pop (scale in), slide (rise per word), marker (highlighter sweep). */
export const KineticText = ({params, variant = 'pop', theme, duration, placement, backing}) => {
	const {text = '', emphasis = []} = params;
	const a = useMotion(theme, duration);
	const c = theme.colors;
	const words = text.split(/\s+/).filter(Boolean);
	const y = placement?.y ?? 0.5;
	// Stay inside the placement box horizontally (Shorts: clear of the right-hand buttons).
	const box = placement?.box;
	const span = box ? {left: box[0] * theme.width, width: (box[2] - box[0]) * theme.width,
		display: 'flex', justifyContent: 'center'} : {left: 0, right: 0, display: 'flex', justifyContent: 'center'};
	return (
		<AbsoluteFill style={{opacity: a.exit}}>
			<div style={{position: 'absolute', top: `${y * 100}%`, transform: placement?.valign === 'top' ? 'none' : 'translateY(-50%)', ...span}}>
			<div style={{...backingStyle(theme, backing), maxWidth: box ? '100%' : '84%', textAlign: 'center',
				fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size[params.size ?? 'title'] ?? theme.size.title,
				lineHeight: 1.1, letterSpacing: '-0.02em'}}>
				{words.map((w, i) => {
					const f = a.frame - i * theme.motion.stagger;
					const p = spring({frame: f, fps: a.fps, durationInFrames: theme.motion.enter,
						config: {damping: theme.motion.damping, stiffness: theme.motion.stiffness}});
					const emph = emphasis.includes(i) || emphasis.includes(w.replace(/[^\w]/g, ''));
					const style = {display: 'inline-block', margin: `0 ${(theme.size[params.size ?? 'title'] ?? theme.size.title) * 0.13}px`, color: emph && variant !== 'marker' ? c.accent : c.text,
						textShadow: '0 6px 24px rgba(0,0,0,0.55)', opacity: f < 0 ? 0 : Math.min(1, p * 1.4)};
					if (variant === 'pop') style.transform = `scale(${interpolate(p, [0, 1], [0.4, 1])})`;
					if (variant === 'slide') style.transform = `translateY(${interpolate(p, [0, 1], [theme.space * 2.5, 0])}px)`;
					if (variant === 'marker') {
						style.backgroundImage = `linear-gradient(${c.primary}, ${c.primary})`;
						style.backgroundRepeat = 'no-repeat';
						style.backgroundSize = `${emph ? p * 100 : 0}% 38%`;
						style.backgroundPosition = '0 88%';
					}
					return <span key={i} style={style}>{w}</span>;
				})}
			</div>
			</div>
		</AbsoluteFill>
	);
};
