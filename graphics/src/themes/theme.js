import {Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

/**
 * Resolve design tokens (styles/*.style.yaml `graphics.theme`) into pixels for this frame size.
 * Components never hard-code colours, fonts, sizes or timing: everything comes from here.
 */
export const resolveTheme = (t = {}, width = 1920, height = 1080) => {
	const base = Math.min(width, height);
	const s = t.sizes ?? {};
	const m = t.motion ?? {};
	return {
		colors: t.colors ?? {},
		fonts: t.fonts ?? {heading: 'Montserrat', body: 'Inter'},
		weights: t.weights ?? {heading: 800, body: 500, label: 700},
		size: {
			title: Math.round((s.title ?? 0.075) * base), heading: Math.round((s.heading ?? 0.048) * base),
			body: Math.round((s.body ?? 0.032) * base), label: Math.round((s.label ?? 0.024) * base),
			number: Math.round((s.number ?? 0.11) * base),
		},
		radius: Math.round((t.radius ?? 0.012) * base),
		space: Math.round((t.spacing ?? 0.018) * base),
		shadow: t.shadow ?? '0 10px 30px rgba(0,0,0,0.35)',
		motion: {enter: m.enter_frames ?? 12, exit: m.exit_frames ?? 8, stagger: m.stagger_frames ?? 4,
			damping: m.spring_damping ?? 18, stiffness: m.spring_stiffness ?? 170},
		safe: t.safe ?? {left: 0.06, right: 0.94, top: 0.07, bottom: 0.93},
		width, height, base,
	};
};

/** Entrance (spring/ease-out) and exit (ease-in) progress, 0..1, for item `index` (staggered). */
export const useMotion = (theme, duration, index = 0) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const f = frame - index * theme.motion.stagger;
	const enter = spring({frame: f, fps, durationInFrames: theme.motion.enter,
		config: {damping: theme.motion.damping, stiffness: theme.motion.stiffness}});
	const exit = interpolate(frame, [duration - theme.motion.exit, duration], [1, 0],
		{extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.in(Easing.cubic)});
	return {enter: f < 0 ? 0 : enter, exit, frame, fps, visible: Math.min(f < 0 ? 0 : enter, exit)};
};

/**
 * Panel behind text that has none of its own, switched on by Python when the footage under the
 * graphic is too bright for white/accent text to reach WCAG contrast (like caption scrims).
 */
export const backingStyle = (theme, on, round = theme.radius) => (on ? {
	background: theme.colors.surface, borderRadius: round, boxShadow: theme.shadow,
	padding: `${theme.space * 0.7}px ${theme.space * 1.2}px`,
} : {});

/** Absolute position from a placement box computed in Python (fractions of the frame). */
export const placeStyle = (placement, theme) => {
	const {anchor = 'bottom-left', x, y} = placement ?? {};
	const W = theme.width;
	const H = theme.height;
	const st = {position: 'absolute'};
	const [v, h] = anchor.split('-');
	if (v === 'top') st.top = (y ?? theme.safe.top) * H;
	else if (v === 'center') { st.top = (y ?? 0.5) * H; st.transform = 'translateY(-50%)'; }
	else st.bottom = (1 - (y ?? theme.safe.bottom)) * H;
	if (h === 'left') st.left = (x ?? theme.safe.left) * W;
	else if (h === 'right') st.right = (1 - (x ?? theme.safe.right)) * W;
	else { st.left = 0; st.right = 0; st.display = 'flex'; st.justifyContent = 'center'; }
	return st;
};
