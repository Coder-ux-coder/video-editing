import React, {useMemo} from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig, Easing} from 'remotion';
import {measureText} from '@remotion/layout-utils';

/**
 * Caption layer. Props (from cutroom's caption planner):
 *  cues:  [{from, to, placement: 'bottom'|'top', lines: [[{text, from, to, emph}]]}]  (frames)
 *  style: resolved style tokens (see styles/default.style.yaml `captions.styles`)
 *  safe:  {left, right, top, bottom} as fractions of the frame: text never leaves this box.
 * Phrase splitting, line breaks and emphasis are decided in Python; this component only
 * draws, animates, and shrinks text that would not fit the safe width.
 */

const findCue = (cues, frame) => {
	let lo = 0;
	let hi = cues.length - 1;
	while (lo <= hi) {
		const mid = (lo + hi) >> 1;
		const c = cues[mid];
		if (frame < c.from) hi = mid - 1;
		else if (frame >= c.to) lo = mid + 1;
		else return c;
	}
	return null;
};

const lineText = (line, upper) => {
	const t = line.map((w) => w.text).join(' ');
	return upper ? t.toUpperCase() : t;
};

const fitSize = (lines, style, maxWidth) => {
	let size = style.fontSize;
	for (const line of lines) {
		const {width} = measureText({
			text: lineText(line, style.uppercase),
			fontFamily: style.fontFamily,
			fontWeight: String(style.fontWeight),
			fontSize: size,
			letterSpacing: `${style.letterSpacing ?? 0}em`,
		});
		const total = width + 2 * (style.strokeWidth ?? 0) + 2 * (style.box ? style.box.padX : Math.round(size * 0.32));
		if (total > maxWidth) size = Math.floor((size * maxWidth) / total);
	}
	return Math.max(size, Math.round(style.fontSize * 0.6));
};

const Word = ({w, frame, style, active, fps, fontSize}) => {
	const upper = style.uppercase;
	const text = upper ? w.text.toUpperCase() : w.text;
	let color = style.color;
	if (w.emph && style.emphasisColor) color = style.emphasisColor;
	if (active && style.activeColor) color = style.activeColor;
	let scale = 1;
	if (active && style.wordPop) {
		const p = spring({frame: frame - w.from, fps, durationInFrames: style.wordPopFrames ?? 6,
			config: {damping: 14, stiffness: 180}});
		// Grow into place (from smaller) rather than shrink from larger: a word scaled above 1 covers
		// the spaces beside it ("MYSURPRISE,"), and the pop lights up a new word every few frames.
		scale = Math.min(1, interpolate(p, [0, 1], [1 - style.wordPop, 1]));
	}
	if (w.emph && style.emphasisScale) scale *= style.emphasisScale;
	const showBox = active && style.activeBox;
	// A scale does not move the neighbours: an emphasised word would cover the spaces beside it
	// ("A CLEAN30"). Reserve its overhang on each side (width estimated from its length). Only the
	// lasting emphasis scale counts: the brief pop would make the line jiggle word by word.
	const lasting = w.emph && style.emphasisScale > 1 ? style.emphasisScale : 1;
	const overhang = Math.round((lasting - 1) * text.length * 0.62 * fontSize / 2);
	const css = {
		display: 'inline-block',
		color,
		transform: `scale(${scale})`,
		transformOrigin: 'center bottom',
		padding: showBox ? `0 ${Math.round(fontSize * 0.18)}px` : 0,
		margin: `0 ${(showBox ? -Math.round(fontSize * 0.18) : 0) + overhang}px`,
		borderRadius: showBox ? Math.round(fontSize * 0.18) : 0,
		background: showBox ? style.activeBox : 'transparent',
		WebkitTextStroke: style.strokeWidth ? `${style.strokeWidth}px ${style.strokeColor}` : undefined,
		paintOrder: 'stroke fill',
		textShadow: style.shadow ?? 'none',
	};
	return <span style={css}>{text}</span>;
};

export const Captions = ({cues = [], style = {}, safe = {}}) => {
	const frame = useCurrentFrame();
	const {fps, width, height} = useVideoConfig();
	const cue = useMemo(() => findCue(cues, frame), [cues, frame]);
	if (!cue || !style.fontFamily) return null;

	const left = (safe.left ?? 0.1) * width;
	const right = (1 - (safe.right ?? 0.9)) * width;
	const maxWidth = width - left - right;
	const fontSize = fitSize(cue.lines, style, maxWidth);

	const inF = style.inFrames ?? 4;
	const outF = style.outFrames ?? 3;
	const t = frame - cue.from;
	const tOut = cue.to - frame;
	let opacity = 1;
	let lift = 0;
	if (style.entrance === 'fade' || style.entrance === 'rise') {
		opacity = Math.min(interpolate(t, [0, inF], [0, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)}),
			interpolate(tOut, [0, outF], [0, 1], {extrapolateRight: 'clamp', easing: Easing.in(Easing.cubic)}));
	}
	if (style.entrance === 'rise') {
		lift = interpolate(t, [0, inF], [fontSize * 0.25, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
	}
	if (style.entrance === 'pop') {
		const p = spring({frame: t, fps, durationInFrames: inF + 2, config: {damping: 13, stiffness: 160}});
		opacity = Math.min(1, t / Math.max(1, inF / 2));
		lift = 0;
		style = {...style, _cueScale: interpolate(p, [0, 1], [0.86, 1])};
	}

	const words = cue.lines.flat();
	const activeIdx = words.findIndex((w, i) => frame >= w.from && (i === words.length - 1 ? frame < cue.to : frame < words[i + 1].from));

	const block = {
		position: 'absolute',
		left,
		right,
		textAlign: 'center',
		fontFamily: style.fontFamily,
		fontWeight: style.fontWeight,
		fontSize,
		lineHeight: style.lineHeight ?? 1.18,
		letterSpacing: `${style.letterSpacing ?? 0}em`,
		opacity,
		transform: `translateY(${lift}px) scale(${style._cueScale ?? 1})`,
		transformOrigin: 'center bottom',
	};
	if (cue.placement === 'top') block.top = (safe.top ?? 0.1) * height;
	else block.bottom = (1 - (safe.bottom ?? 0.9)) * height;

	let k = 0;
	return (
		<AbsoluteFill>
			<div style={block} data-cue={cue.id}>
				{cue.lines.map((line, li) => {
					// A box is part of the style, or added per caption as a scrim when the background
					// behind the text is too bright for readable contrast (decided in Python).
					const box = style.box ?? (cue.scrim ? {color: style.scrimColor ?? 'rgba(0,0,0,0.42)',
						radius: Math.round(fontSize * 0.2), padX: Math.round(fontSize * 0.32),
						padY: Math.round(fontSize * 0.08), gap: 4} : null);
					const lineStyle = box ? {
						display: 'inline-block', background: box.color, borderRadius: box.radius,
						padding: `${box.padY}px ${box.padX}px`, margin: `${box.gap ?? 4}px 0`,
					} : {display: 'inline-block'};
					return (
						<div key={li}>
							<div style={lineStyle}>
								{line.map((w, wi) => {
									const idx = k++;
									return (
										<React.Fragment key={wi}>
											{wi > 0 ? ' ' : null}
											<Word w={w} frame={frame} style={style} fps={fps} fontSize={fontSize}
												active={style.karaoke && idx === activeIdx}/>
										</React.Fragment>
									);
								})}
							</div>
						</div>
					);
				})}
			</div>
		</AbsoluteFill>
	);
};
