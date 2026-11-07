import React from 'react';
import {AbsoluteFill, interpolate} from 'remotion';
import {useMotion} from '../../themes/theme.js';

/**
 * Point at a region of the frame (rect in frame fractions). Variants: box (outline + label),
 * spotlight (dim everything else), arrow (arrow to the region + label). For screen recordings,
 * pair with a zoom (FFmpeg reframe) when the region is small.
 */
export const Callout = ({params, variant = 'box', theme, duration}) => {
	const {rect = {x: 0.4, y: 0.4, w: 0.2, h: 0.15}, label = '', step} = params;
	const a = useMotion(theme, duration);
	const b = useMotion(theme, duration, 1);
	const c = theme.colors;
	const W = theme.width;
	const H = theme.height;
	const r = {left: rect.x * W, top: rect.y * H, width: rect.w * W, height: rect.h * H};
	const lw = Math.max(3, theme.base * 0.005);
	const labelBox = label ? (
		<div style={{position: 'absolute', left: r.left, top: rect.y > 0.25 ? r.top - theme.size.body * 2.1 : r.top + r.height + theme.space * 0.6,
			background: c.surface, color: c.text, fontFamily: theme.fonts.body, fontWeight: theme.weights.label,
			fontSize: theme.size.body, padding: `${theme.space * 0.35}px ${theme.space * 0.8}px`, borderRadius: theme.radius,
			boxShadow: theme.shadow, opacity: b.enter, display: 'flex', alignItems: 'center', gap: theme.space * 0.5}}>
			{step != null ? <span style={{background: c.accent, color: c.textOnLight, borderRadius: '50%',
				width: theme.size.body * 1.3, height: theme.size.body * 1.3, display: 'inline-flex', alignItems: 'center',
				justifyContent: 'center', fontWeight: theme.weights.heading}}>{step}</span> : null}
			{label}
		</div>
	) : null;
	if (variant === 'spotlight') {
		return (
			<AbsoluteFill style={{opacity: a.exit}}>
				<div style={{position: 'absolute', ...r, borderRadius: theme.radius,
					boxShadow: `0 0 0 ${Math.max(W, H)}px rgba(0,0,0,${0.62 * a.enter})`}}/>
				{labelBox}
			</AbsoluteFill>
		);
	}
	if (variant === 'arrow') {
		const tx = r.left + r.width / 2;
		const ty = r.top + r.height / 2;
		const fx = tx + (rect.x > 0.5 ? -1 : 1) * theme.base * 0.22;
		const fy = ty + (rect.y > 0.5 ? -1 : 1) * theme.base * 0.16;
		const p = a.enter;
		const ex = fx + (tx - fx) * p * 0.86;
		const ey = fy + (ty - fy) * p * 0.86;
		const ang = Math.atan2(ty - fy, tx - fx);
		const head = theme.base * 0.03;
		return (
			<AbsoluteFill style={{opacity: a.exit}}>
				<svg width={W} height={H} style={{position: 'absolute'}}>
					<line x1={fx} y1={fy} x2={ex} y2={ey} stroke={c.accent} strokeWidth={lw * 1.6} strokeLinecap="round"/>
					<polygon fill={c.accent} opacity={p > 0.6 ? 1 : 0} points={[
						[ex + Math.cos(ang) * head, ey + Math.sin(ang) * head],
						[ex + Math.cos(ang + 2.4) * head, ey + Math.sin(ang + 2.4) * head],
						[ex + Math.cos(ang - 2.4) * head, ey + Math.sin(ang - 2.4) * head]].map((q) => q.join(',')).join(' ')}/>
				</svg>
				{label ? <div style={{position: 'absolute', left: Math.min(fx, W * 0.7), top: fy + (fy < ty ? -theme.size.body * 2 : theme.space * 0.5),
					background: c.surface, color: c.text, fontFamily: theme.fonts.body, fontWeight: theme.weights.label,
					fontSize: theme.size.body, padding: `${theme.space * 0.35}px ${theme.space * 0.8}px`,
					borderRadius: theme.radius, opacity: b.enter}}>{label}</div> : null}
			</AbsoluteFill>
		);
	}
	const grow = interpolate(a.enter, [0, 1], [1.12, 1]);
	return (
		<AbsoluteFill style={{opacity: a.exit}}>
			<div style={{position: 'absolute', ...r, border: `${lw}px solid ${c.accent}`, borderRadius: theme.radius,
				transform: `scale(${grow})`, opacity: a.enter, boxShadow: `0 0 ${theme.space}px ${c.accent}55`}}/>
			{labelBox}
		</AbsoluteFill>
	);
};
