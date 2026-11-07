import React from 'react';
import {AbsoluteFill, interpolate} from 'remotion';
import {useMotion} from '../../themes/theme.js';

/** Title (+ subtitle). Variants: center (full-frame scrim), panel (card), chapter (number + title). */
export const TitleCard = ({params, variant = 'center', theme, duration}) => {
	const {title = '', subtitle = '', number} = params;
	const a = useMotion(theme, duration);
	const b = useMotion(theme, duration, 1);
	const c = theme.colors;
	const t = {fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.title,
		color: c.text, lineHeight: 1.05, letterSpacing: '-0.02em', textAlign: 'center', maxWidth: '80%',
		transform: `translateY(${interpolate(a.enter, [0, 1], [theme.space * 2, 0])}px)`, opacity: a.enter};
	const sub = {fontFamily: theme.fonts.body, fontWeight: theme.weights.body, fontSize: theme.size.body,
		color: c.textMuted, marginTop: theme.space, textAlign: 'center', opacity: b.enter};
	if (variant === 'panel') {
		return (
			<AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', opacity: a.exit}}>
				<div style={{background: c.surface, borderRadius: theme.radius * 1.5, boxShadow: theme.shadow,
					padding: `${theme.space * 2}px ${theme.space * 3}px`, maxWidth: '72%',
					transform: `scale(${interpolate(a.enter, [0, 1], [0.94, 1])})`, display: 'flex',
					flexDirection: 'column', alignItems: 'center'}}>
					<div style={{...t, transform: 'none', maxWidth: '100%'}}>{title}</div>
					{subtitle ? <div style={sub}>{subtitle}</div> : null}
				</div>
			</AbsoluteFill>
		);
	}
	return (
		<AbsoluteFill style={{background: c.scrim, opacity: a.exit, justifyContent: 'center', alignItems: 'center',
			flexDirection: 'column'}}>
			{variant === 'chapter' && number != null ? (
				<div style={{fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.body,
					color: c.accent, letterSpacing: '0.2em', marginBottom: theme.space, opacity: a.enter}}>
					{`CHAPTER ${number}`}
				</div>
			) : null}
			<div style={t}>{title}</div>
			{variant === 'chapter' ? <div style={{height: Math.max(3, theme.base * 0.005), background: c.accent,
				width: `${b.enter * theme.base * 0.25}px`, marginTop: theme.space}}/> : null}
			{subtitle ? <div style={sub}>{subtitle}</div> : null}
		</AbsoluteFill>
	);
};
