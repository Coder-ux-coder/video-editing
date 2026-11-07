# Graphics templates

One JSON spec per motion-graphics template. A spec tells `cutroom` what the agent may fill in and
where the graphic may go; the Remotion component named in `component`
(`graphics/src/components/graphics/`) draws it, taking every colour, font, size and timing from
the style's `graphics.theme` tokens. Adding a graphic needs no React: `cutroom graphic-add`.

| Field | Meaning |
|---|---|
| `id` | Template id; must equal the file name. |
| `component` | Remotion component in `graphics/src/components/graphics/index.js`. |
| `variants` | Looks the component supports; the first is the default. |
| `default_duration_s` | Seconds on screen unless `--duration` (never below the reading time). |
| `zone` | `lower`, `side`, `corner` (placed by anchor), `band` (kinetic text: a horizontal band), `full`, `region` (callout rectangle), `edge` (progress bar). |
| `anchors`, `size_hint` | Candidate anchors and the estimated box (frame fractions) for placement off faces and captions. |
| `fact_params` | Parameters that are facts: numbers in them must appear in the graphic's `--fact` references. Numbers inside any text parameter are checked too. |
| `hides_captions` | Burned-in captions are hidden while it is on screen (kinetic text, title and chapter cards). |
| `persistent` | Spans the whole video and follows its length (progress bar, logo bug). |
| `at_cut` | Centred on a cut (transitions). |
| `backing_variants` | Variants without a panel of their own: they get one automatically over bright footage. |
| `params` | Typed parameters: `string` (`max_len`), `integer`, `number`, `list` (`max_items`; `a|b|c` on the CLI), `data` (`label:value,…`), `column` (`Title|item|item`), `rect` (`x,y,w,h`), `color`, `enum` (`values`), `asset` (a file in the job, e.g. `inputs/logo.png`). |

To add a template: write the component (props `params, variant, theme, placement, duration,
backing`; use `useMotion` and `placeStyle` from `themes/theme.js`, never hard-coded colours), register
it in `index.js`, add the spec here, run `cutroom template-preview <id>` and look at it, then
regenerate the visual goldens (`CUTROOM_UPDATE_GOLDENS=1 pytest tests/test_graphics.py -m slow -k golden`)
and inspect the new images in `tests/golden/graphics/`.
