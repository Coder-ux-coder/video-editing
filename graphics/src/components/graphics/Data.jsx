import React from 'react';
import {Easing, interpolate} from 'remotion';
import {backingStyle, placeStyle, useMotion} from '../../themes/theme.js';

const fmt = (v, decimals = 0, sep = ',') => {
	const s = Number(v).toFixed(decimals);
	const [i, d] = s.split('.');
	return i.replace(/\B(?=(\d{3})+(?!\d))/g, sep) + (d ? '.' + d : '');
};

/** Animated number. Variants: big (hero number + label), inline (compact pill). */
export const Counter = ({params, variant = 'big', theme, placement, duration, backing}) => {
	const {from = 0, to = 0, prefix = '', suffix = '', decimals = 0, label = ''} = params;
	const a = useMotion(theme, duration);
	const c = theme.colors;
	const run = Math.min(duration * 0.6, Math.max(theme.motion.enter * 2, 30));
	const v = interpolate(a.frame, [theme.motion.enter * 0.5, run], [from, to],
		{extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
	if (variant === 'inline') {
		return (
			<div style={{...placeStyle(placement, theme), opacity: Math.min(a.enter, a.exit)}}>
				<div style={{background: c.surface, borderRadius: theme.base, padding: `${theme.space * 0.5}px ${theme.space * 1.2}px`,
					display: 'flex', gap: theme.space * 0.6, alignItems: 'baseline', boxShadow: theme.shadow}}>
					<span style={{fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.heading, color: c.accent}}>
						{prefix}{fmt(v, decimals)}{suffix}</span>
					{label ? <span style={{fontFamily: theme.fonts.body, fontSize: theme.size.body, color: c.text}}>{label}</span> : null}
				</div>
			</div>
		);
	}
	return (
		<div style={{...placeStyle(placement, theme), opacity: a.exit, textAlign: 'center'}}>
			<div style={{...backingStyle(theme, backing), opacity: backing ? a.enter : 1}}>
			<div style={{fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.number,
				color: c.accent, lineHeight: 1, textShadow: '0 8px 30px rgba(0,0,0,0.5)', opacity: a.enter,
				transform: `scale(${interpolate(a.enter, [0, 1], [0.8, 1])})`}}>{prefix}{fmt(v, decimals)}{suffix}</div>
			{label ? <div style={{fontFamily: theme.fonts.body, fontWeight: theme.weights.label, fontSize: theme.size.body,
				color: c.text, marginTop: theme.space * 0.4, textShadow: '0 3px 14px rgba(0,0,0,0.6)', opacity: a.enter}}>{label}</div> : null}
			</div>
		</div>
	);
};

/** Simple charts from data [{label, value}]. Variants: bar, line. */
export const Chart = ({params, variant = 'bar', theme, placement, duration}) => {
	const {title = '', data = [], unit = ''} = params;
	const a = useMotion(theme, duration);
	const c = theme.colors;
	const w = theme.base * 0.85;
	const h = theme.base * 0.42;
	const max = Math.max(1e-9, ...data.map((d) => d.value));
	const grow = interpolate(a.frame, [theme.motion.enter * 0.5, theme.motion.enter * 3], [0, 1],
		{extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
	const n = Math.max(1, data.length);
	const gap = w * 0.04;
	const bw = (w - gap * (n + 1)) / n;
	const lab = {fontFamily: theme.fonts.body, fontSize: theme.size.label, fill: c.textMuted};
	let body;
	if (variant === 'line') {
		const pts = data.map((d, i) => [gap + i * (w - 2 * gap) / Math.max(1, n - 1), h - (d.value / max) * h * 0.85]);
		const len = pts.reduce((s, p, i) => (i ? s + Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) : 0), 0);
		body = (
			<>
				<polyline points={pts.map((p) => p.join(',')).join(' ')} fill="none" stroke={c.accent} strokeWidth={theme.base * 0.006}
					strokeLinejoin="round" strokeLinecap="round" strokeDasharray={len} strokeDashoffset={len * (1 - grow)}/>
				{pts.map((p, i) => <g key={i} opacity={grow > i / n ? 1 : 0}>
					<circle cx={p[0]} cy={p[1]} r={theme.base * 0.008} fill={c.accent}/>
					<text x={p[0]} y={h + theme.size.label * 1.3} textAnchor="middle" style={lab}>{data[i].label}</text>
				</g>)}
			</>
		);
	} else {
		body = data.map((d, i) => {
			const bh = (d.value / max) * h * 0.85 * grow;
			const x = gap + i * (bw + gap);
			return (
				<g key={i}>
					<rect x={x} y={h - bh} width={bw} height={bh} rx={theme.radius / 2} fill={i === data.length - 1 ? c.accent : c.primary}/>
					<text x={x + bw / 2} y={h - bh - theme.space * 0.4} textAnchor="middle"
						style={{...lab, fill: c.text, fontWeight: theme.weights.label}} opacity={grow}>{fmt(d.value)}{unit}</text>
					<text x={x + bw / 2} y={h + theme.size.label * 1.3} textAnchor="middle" style={lab}>{d.label}</text>
				</g>
			);
		});
	}
	return (
		<div style={{...placeStyle(placement, theme), opacity: Math.min(a.enter, a.exit)}}>
			<div style={{background: c.surface, borderRadius: theme.radius, boxShadow: theme.shadow, padding: theme.space * 1.2}}>
				{title ? <div style={{fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.heading,
					color: c.text, marginBottom: theme.space * 0.6}}>{title}</div> : null}
				<svg width={w} height={h + theme.size.label * 1.8}>{body}</svg>
			</div>
		</div>
	);
};

/** Two columns. Variants: versus (A vs B), proscons (green/red ticks). */
export const Compare = ({params, variant = 'versus', theme, placement, duration}) => {
	const {left = {title: '', items: []}, right = {title: '', items: []}} = params;
	const a = useMotion(theme, duration);
	const c = theme.colors;
	const col = (side, k) => {
		const m = useMotion(theme, duration, k);
		const color = variant === 'proscons' ? (k === 1 ? c.positive : c.negative) : (k === 1 ? c.primary : c.accent);
		return (
			<div style={{flex: 1, background: c.surface, borderRadius: theme.radius, padding: theme.space * 1.1,
				borderTop: `${Math.max(4, theme.base * 0.006)}px solid ${color}`, opacity: m.enter,
				transform: `translateY(${interpolate(m.enter, [0, 1], [theme.space * 2, 0])}px)`}}>
				<div style={{fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading, fontSize: theme.size.heading,
					color: c.text, marginBottom: theme.space * 0.5}}>{side.title}</div>
				{(side.items ?? []).map((it, i) => (
					<div key={i} style={{fontFamily: theme.fonts.body, fontSize: theme.size.body, color: c.text,
						margin: `${theme.space * 0.35}px 0`, display: 'flex', gap: theme.space * 0.5}}>
						<span style={{color, fontWeight: theme.weights.heading}}>{variant === 'proscons' ? (k === 1 ? '+' : '−') : '•'}</span>{it}
					</div>
				))}
			</div>
		);
	};
	return (
		<div style={{...placeStyle(placement, theme), opacity: a.exit}}>
			<div style={{display: 'flex', gap: theme.space, width: theme.width * 0.62, alignItems: 'stretch'}}>
				{col(left, 1)}
				{variant === 'versus' ? <div style={{alignSelf: 'center', fontFamily: theme.fonts.heading, fontWeight: theme.weights.heading,
					fontSize: theme.size.heading, color: c.accent, opacity: a.enter}}>VS</div> : null}
				{col(right, 2)}
			</div>
		</div>
	);
};
