// Render a Remotion composition to a transparent PNG sequence for FFmpeg to composite.
//
//   node render.mjs --composition Captions --props props.json --out DIR
//                   [--concurrency N] [--browser /path/to/chrome-headless-shell] [--from F --to F]
//                   [--frames 12,40,95]   (stills only: one browser, <frame>.png per listed frame)
//
// Prints JSON lines on stdout: {"progress": 0.42} ... then {"done": true, ...}. Frames are named
// 000000.png, 000001.png, ... by composition frame number (FFmpeg reads them with %06d.png).
import {bundle} from '@remotion/bundler';
import {openBrowser, renderFrames, renderStill, selectComposition} from '@remotion/renderer';
import {createHash} from 'node:crypto';
import {existsSync, mkdirSync, readFileSync, readdirSync, renameSync, statSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
	if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1]?.startsWith('--') ? 'true' : all[i + 1]]);
	return acc;
}, []));

const fail = (msg) => {
	console.log(JSON.stringify({error: msg}));
	process.exit(2);
};

const hashDir = (dir, h = createHash('sha1')) => {
	for (const name of readdirSync(dir).sort()) {
		const p = path.join(dir, name);
		if (statSync(p).isDirectory()) hashDir(p, h);
		else h.update(name).update(readFileSync(p));
	}
	return h;
};

const getBundle = async () => {
	// Rebuild only when the source changes: bundling takes several seconds.
	const key = hashDir(path.join(here, 'src')).update(readFileSync(path.join(here, 'package.json'))).digest('hex').slice(0, 12);
	const cacheDir = path.join(here, '.bundle-cache', key);
	if (existsSync(path.join(cacheDir, 'index.html'))) return cacheDir;
	mkdirSync(path.dirname(cacheDir), {recursive: true});
	const tmp = await bundle({entryPoint: path.join(here, 'src', 'index.js'), outDir: cacheDir + '.tmp'});
	renameSync(tmp, cacheDir);
	return cacheDir;
};

const main = async () => {
	if (!args.props || !args.out) fail('usage: render.mjs --props props.json --out DIR');
	const inputProps = JSON.parse(readFileSync(args.props, 'utf-8'));
	const serveUrl = await getBundle();
	const browserExecutable = args.browser || process.env.CUTROOM_BROWSER || null;
	const id = args.composition || 'Captions';
	const composition = await selectComposition({serveUrl, id, inputProps, browserExecutable,
		logLevel: 'error'});
	mkdirSync(args.out, {recursive: true});
	if (args.frames) {
		const frames = args.frames.split(',').map(Number);
		const t0 = Date.now();
		const browser = await openBrowser('chrome', {browserExecutable, logLevel: 'error'});
		try {
			for (const [i, frame] of frames.entries()) {
				await renderStill({composition, serveUrl, inputProps, frame, output: path.join(args.out, `${frame}.png`),
					imageFormat: 'png', puppeteerInstance: browser, logLevel: 'error'});
				console.log(JSON.stringify({progress: Number(((i + 1) / frames.length).toFixed(3))}));
			}
		} finally {
			await browser.close({silent: true});
		}
		console.log(JSON.stringify({done: true, frames: frames.length, seconds: (Date.now() - t0) / 1000,
			width: composition.width, height: composition.height, fps: composition.fps}));
		return;
	}
	// --ranges a-b,c-d (or --ranges-file holding that text): only these inclusive frame ranges, in one browser session (frames that
	// are already cached are not drawn again). Otherwise --from/--to, or the whole composition.
	const rangeSpec = args['ranges-file'] ? readFileSync(args['ranges-file'], 'utf-8').trim() : args.ranges;
	const ranges = rangeSpec
		? rangeSpec.split(',').map((r) => r.split('-').map(Number))
		: [[args.from ? Number(args.from) : 0, args.to ? Number(args.to) : composition.durationInFrames - 1]];
	const total = ranges.reduce((s, [a, b]) => s + b - a + 1, 0);
	let last = 0;
	let before = 0;
	const t0 = Date.now();
	const browser = ranges.length > 1 ? await openBrowser('chrome', {browserExecutable, logLevel: 'error'}) : null;
	try {
		for (const [from, to] of ranges) {
			await renderFrames({
				composition, serveUrl, inputProps, browserExecutable,
				...(browser ? {puppeteerInstance: browser} : {}),
				outputDir: args.out,
				imageFormat: 'png',
				frameRange: [from, to],
				concurrency: args.concurrency ? Number(args.concurrency) : null,
				imageSequencePattern: '[frame].[ext]',
				logLevel: 'error',
				onStart: () => {},
				onFrameUpdate: (done) => {
					const p = (before + done) / total;
					if (p - last >= 0.01 || before + done === total) {
						last = p;
						console.log(JSON.stringify({progress: Number(p.toFixed(3))}));
					}
				},
			});
			before += to - from + 1;
		}
	} finally {
		if (browser) await browser.close({silent: true});
	}
	console.log(JSON.stringify({done: true, frames: total, ranges: ranges.length, seconds: (Date.now() - t0) / 1000,
		width: composition.width, height: composition.height, fps: composition.fps}));
};

main().catch((e) => fail(String(e?.stack || e)));
