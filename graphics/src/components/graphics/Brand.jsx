import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {backingStyle, placeStyle, useMotion} from '../../themes/theme.js';

/** Call to action button with a click. Generic: no platform logos or trademarks. Variants: subscribe, follow, link. */
export const CTA = ({params, variant = 'subscribe', theme, placement, duration}) => {
	const {text = {link: 'Link in the description', follow: 'Follow'}[variant] ?? 'Subscribe'} = params;
	const a = useMotion(theme, duration);
	const c = theme.colors;
	const press = interpolate(a.frame, [theme.motion.enter + 6, theme.motion.enter + 10, theme.motion.enter + 16],
		[1, 0.92, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
	const done = a.frame > theme.motion.enter + 10 && variant !== 'link';
	return (
		<div style={{...placeStyle(placement, theme), opacity: a.exit}}>
			<div style={{transform: `scale(${a.enter * press})`, background: done ? c.surface : c.negative, color: c.text,
				fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.body,
				padding: `${theme.space * 0.6}px ${theme.space * 1.4}px`, borderRadius: theme.base, boxShadow: theme.shadow,
				letterSpacing: '0.02em', display: 'flex', alignItems: 'center', gap: theme.space * 0.6}}>
				{variant === 'subscribe' ? <svg width={theme.size.body} height={theme.size.body} viewBox="0 0 24 24">
					<path d="M12 22a2.5 2.5 0 002.4-2h-4.8A2.5 2.5 0 0012 22zm7-6V11a7 7 0 00-5.5-6.8V3a1.5 1.5 0 00-3 0v1.2A7 7 0 005 11v5l-2 2v1h18v-1z"
						fill={c.text}/></svg> : null}
				{done && variant === 'subscribe' ? 'Subscribed' : text}
			</div>
		</div>
	);
};

/** Thin progress bar across the whole video, with optional chapter ticks. Variants: top, bottom. */
export const ProgressBar = ({params, variant = 'bottom', theme}) => {
	const frame = useCurrentFrame();
	const {durationInFrames} = useVideoConfig();
	const {total = durationInFrames, offset = 0, chapters = []} = params;
	const c = theme.colors;
	const hgt = Math.max(4, theme.base * 0.007);
	const p = Math.min(1, (frame + offset) / Math.max(1, total));
	return (
		<AbsoluteFill>
			<div style={{position: 'absolute', left: 0, right: 0, height: hgt, background: 'rgba(255,255,255,0.18)',
				[variant === 'top' ? 'top' : 'bottom']: 0}}>
				<div style={{width: `${p * 100}%`, height: '100%', background: c.primary}}/>
				{chapters.map((t, i) => <div key={i} style={{position: 'absolute', left: `${(t / total) * 100}%`, top: 0,
					width: Math.max(2, hgt / 2), height: '100%', background: c.textOnLight ?? '#000'}}/>)}
			</div>
		</AbsoluteFill>
	);
};

/** Small persistent brand mark in a corner. Variants: text, image (data URL). */
export const LogoBug = ({params, variant = 'text', theme, placement, duration, backing}) => {
	const {text = '', src, opacity = 0.85} = params;
	const a = useMotion(theme, duration);
	const c = theme.colors;
	const box = {...placeStyle(placement, theme), opacity: Math.min(a.enter, a.exit) * opacity};
	if (variant === 'image' && src) {
		return <div style={box}><img src={src} style={{height: theme.base * 0.07, objectFit: 'contain'}}/></div>;
	}
	return (
		<div style={{...box, ...backingStyle(theme, backing)}}>
			<div style={{fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.label,
				color: c.text, letterSpacing: '0.12em', textTransform: 'uppercase', textShadow: '0 2px 8px rgba(0,0,0,0.6)'}}>{text}</div>
		</div>
	);
};

/** Social handle / website lower third. Variants: pill, plain. */
export const Handle = ({params, variant = 'pill', theme, placement, duration, backing}) => {
	const {handle = '', platform = ''} = params;
	const a = useMotion(theme, duration);
	const c = theme.colors;
	return (
		<div style={{...placeStyle(placement, theme), opacity: a.exit,
			transform: `translateY(${interpolate(a.enter, [0, 1], [theme.space * 1.5, 0])}px)`}}>
			<div style={{background: variant === 'pill' || backing ? c.surface : 'transparent', borderRadius: theme.base,
				padding: `${theme.space * 0.45}px ${theme.space * 1.1}px`, display: 'flex', gap: theme.space * 0.5, alignItems: 'center',
				fontFamily: theme.fonts.body, fontSize: theme.size.body, color: c.text, opacity: a.enter,
				textShadow: variant === 'plain' ? '0 2px 10px rgba(0,0,0,0.6)' : 'none'}}>
				{platform ? <span style={{color: c.accent, fontWeight: theme.weights.label}}>{platform}</span> : null}
				<span style={{fontWeight: theme.weights.label}}>{handle}</span>
			</div>
		</div>
	);
};
