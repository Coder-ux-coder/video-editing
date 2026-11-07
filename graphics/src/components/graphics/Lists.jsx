import React from 'react';
import {interpolate} from 'remotion';
import {placeStyle, useMotion} from '../../themes/theme.js';

/** Items revealed one by one. Variants: numbered (steps), checklist (ticks), bullets. */
export const ListReveal = ({params, variant = 'numbered', theme, placement, duration}) => {
	const {title = '', items = []} = params;
	const a = useMotion(theme, duration);
	const c = theme.colors;
	// Reveal items across the first half of the graphic so each can be read as it appears.
	const per = Math.max(theme.motion.stagger, Math.floor((duration * 0.5) / Math.max(1, items.length)));
	return (
		<div style={{...placeStyle(placement, theme), opacity: a.exit}}>
			<div style={{background: c.surface, borderRadius: theme.radius, boxShadow: theme.shadow,
				padding: `${theme.space * 1.1}px ${theme.space * 1.5}px`, minWidth: theme.base * 0.45,
				transform: `translateY(${interpolate(a.enter, [0, 1], [theme.space * 1.5, 0])}px)`, opacity: a.enter}}>
				{title ? <div style={{fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.heading,
					color: c.text, marginBottom: theme.space * 0.6}}>{title}</div> : null}
				{items.map((it, i) => <Item key={i} i={i} text={it} per={per} variant={variant} theme={theme} duration={duration}/>)}
			</div>
		</div>
	);
};

const Item = ({i, text, per, variant, theme, duration}) => {
	const m = useMotion({...theme, motion: {...theme.motion, stagger: per}}, duration, i + 1);
	const c = theme.colors;
	const mark = theme.size.body * 1.35;
	let badge;
	if (variant === 'checklist') {
		badge = (
			<svg width={mark} height={mark} viewBox="0 0 24 24">
				<circle cx="12" cy="12" r="11" fill={c.positive}/>
				<path d="M6.5 12.5l3.5 3.5 7.5-8" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round"
					strokeDasharray="20" strokeDashoffset={20 * (1 - m.enter)}/>
			</svg>
		);
	} else if (variant === 'bullets') {
		badge = <div style={{width: mark * 0.35, height: mark * 0.35, borderRadius: '50%', background: c.accent, margin: mark * 0.32}}/>;
	} else {
		badge = <div style={{width: mark, height: mark, borderRadius: '50%', background: c.primary, color: c.text,
			fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.label,
			display: 'flex', alignItems: 'center', justifyContent: 'center'}}>{i + 1}</div>;
	}
	return (
		<div style={{display: 'flex', alignItems: 'center', gap: theme.space * 0.7, margin: `${theme.space * 0.45}px 0`,
			opacity: m.enter, transform: `translateX(${interpolate(m.enter, [0, 1], [theme.space * 2, 0])}px)`}}>
			{badge}
			<div style={{fontFamily: theme.fonts.body, fontWeight: theme.weights.body, fontSize: theme.size.body, color: c.text}}>{text}</div>
		</div>
	);
};
