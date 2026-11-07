import React from 'react';
import {AbsoluteFill, Easing, interpolate, random} from 'remotion';
import {useMotion} from '../../themes/theme.js';

/**
 * Overlay transitions centred on a cut (the cut happens at the middle of the graphic).
 * Variants: flash (white flash), dip (dip to black), whip (motion streak), swipe (colour panel
 * wipe), glitch (RGB bars; at most 2 flashes, under the WCAG three-flashes-per-second limit).
 */
export const Transition = ({params, variant = 'dip', theme, duration}) => {
	const {color, direction = 'left'} = params;
	const a = useMotion(theme, duration);
	const c = theme.colors;
	const f = a.frame;
	const mid = duration / 2;
	const peak = interpolate(f, [0, mid, duration], [0, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
		easing: Easing.inOut(Easing.cubic)});
	if (variant === 'flash') return <AbsoluteFill style={{background: color ?? '#FFFFFF', opacity: peak * 0.85}}/>;
	if (variant === 'dip') return <AbsoluteFill style={{background: color ?? '#000000', opacity: peak}}/>;
	const sign = direction === 'right' ? 1 : -1;
	if (variant === 'swipe') {
		const x = interpolate(f, [0, duration], [-1.05, 1.05], {easing: Easing.inOut(Easing.cubic)}) * sign;
		return <AbsoluteFill style={{background: color ?? c.primary, transform: `translateX(${x * 100}%) skewX(-12deg)`}}/>;
	}
	if (variant === 'whip') {
		const x = interpolate(f, [0, duration], [-1.2, 1.2]) * sign;
		return (
			<AbsoluteFill style={{opacity: peak}}>
				{[0, 1, 2, 3, 4, 5].map((i) => (
					<div key={i} style={{position: 'absolute', top: `${8 + i * 15}%`, height: `${6 + (i % 3) * 3}%`, width: '140%',
						left: `${x * 100 - 20 + (i % 2) * 8}%`, background: `linear-gradient(90deg, transparent, rgba(255,255,255,${0.35 + 0.1 * (i % 3)}), transparent)`,
						filter: `blur(${theme.base * 0.012}px)`}}/>
				))}
				<AbsoluteFill style={{backdropFilter: `blur(${peak * theme.base * 0.02}px)`}}/>
			</AbsoluteFill>
		);
	}
	// glitch: a few horizontal RGB-shifted bars for the middle frames only (two bursts max).
	const burst = Math.abs(f - mid) < duration * 0.25;
	if (!burst) return null;
	return (
		<AbsoluteFill>
			{Array.from({length: 6}).map((_, i) => {
				const y = random(`g${i}-${Math.floor(f / 2)}`) * 92;
				const h = 2 + random(`h${i}`) * 6;
				return <div key={i} style={{position: 'absolute', top: `${y}%`, height: `${h}%`, left: 0, right: 0,
					background: i % 2 ? 'rgba(255,0,80,0.35)' : 'rgba(0,220,255,0.35)', mixBlendMode: 'screen',
					transform: `translateX(${(random(`x${i}-${f}`) - 0.5) * theme.base * 0.06}px)`}}/>;
			})}
		</AbsoluteFill>
	);
};
