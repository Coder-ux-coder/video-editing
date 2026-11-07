// Fonts are bundled from @fontsource packages (SIL Open Font License 1.1): nothing is fetched
// from the internet at render time, so renders are offline and reproducible.
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/inter/800.css';
import '@fontsource/inter/900.css';
import '@fontsource/montserrat/500.css';
import '@fontsource/montserrat/600.css';
import '@fontsource/montserrat/700.css';
import '@fontsource/montserrat/800.css';
import '@fontsource/montserrat/900.css';
import {continueRender, delayRender} from 'remotion';

export const FONT_FACES = [
	'400 32px Inter', '500 32px Inter', '600 32px Inter', '700 32px Inter', '800 32px Inter', '900 32px Inter',
	'500 32px Montserrat', '600 32px Montserrat', '700 32px Montserrat', '800 32px Montserrat', '900 32px Montserrat',
];

let loaded = null;

/** Block rendering until every bundled font face is loaded (no fallback-font frames). */
export const ensureFonts = () => {
	if (loaded) return loaded;
	const handle = delayRender('Loading bundled fonts');
	loaded = Promise.all(FONT_FACES.map((f) => document.fonts.load(f)))
		.then(() => document.fonts.ready)
		.then(() => continueRender(handle));
	return loaded;
};
