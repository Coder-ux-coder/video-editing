import React from 'react';
import {interpolate} from 'remotion';
import {placeStyle, useMotion} from '../../themes/theme.js';

/** Name + title. Variants: bar (accent bar + panel), pill (rounded panel), minimal (text + rule). */
export const LowerThird = ({params, variant = 'bar', theme, placement, duration}) => {
	const {name = '', title = ''} = params;
	const a = useMotion(theme, duration);
	const b = useMotion(theme, duration, 1);
	const c = theme.colors;
	const slide = interpolate(a.enter, [0, 1], [-theme.base * 0.04, 0]);
	const nameStyle = {fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.heading,
		color: variant === 'minimal' ? c.text : c.text, lineHeight: 1.1, letterSpacing: '-0.01em'};
	const titleStyle = {fontFamily: theme.fonts.body, fontWeight: theme.weights.body, fontSize: theme.size.body,
		color: c.textMuted, lineHeight: 1.2, marginTop: theme.space * 0.3, opacity: b.enter,
		transform: `translateY(${interpolate(b.enter, [0, 1], [theme.space, 0])}px)`};
	const wrap = {...placeStyle(placement, theme), opacity: a.exit, transform: `translateX(${slide}px)`};
	if (variant === 'minimal') {
		return (
			<div style={wrap}>
				<div style={{...nameStyle, textShadow: '0 2px 10px rgba(0,0,0,0.6)'}}>{name}</div>
				<div style={{height: Math.max(2, theme.base * 0.004), background: c.accent, width: `${a.enter * 100}%`,
					margin: `${theme.space * 0.4}px 0`}}/>
				{title ? <div style={{...titleStyle, color: c.text, textShadow: '0 2px 10px rgba(0,0,0,0.6)'}}>{title}</div> : null}
			</div>
		);
	}
	const panel = {background: c.surface, padding: `${theme.space * 0.8}px ${theme.space * 1.3}px`,
		borderRadius: variant === 'pill' ? theme.base : theme.radius, boxShadow: theme.shadow,
		display: 'flex', alignItems: 'stretch', gap: theme.space,
		clipPath: `inset(0 ${100 - a.enter * 100}% 0 0 round ${variant === 'pill' ? theme.base : theme.radius}px)`};
	return (
		<div style={wrap}>
			<div style={panel}>
				{variant === 'bar' ? <div style={{width: Math.max(4, theme.base * 0.007), background: c.primary,
					borderRadius: theme.radius / 2}}/> : null}
				<div>
					<div style={nameStyle}>{name}</div>
					{title ? <div style={titleStyle}>{title}</div> : null}
				</div>
			</div>
		</div>
	);
};
