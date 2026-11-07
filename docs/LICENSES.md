# Licences

Every dependency, model, tool and asset Cutroom uses, with its licence and what that means
for paid client work. Rule: nothing is used without a recorded, free licence that allows
commercial use. Last checked: 2026-11-07.

Summary: **everything used so far is free for commercial client work**, including Remotion
for you as an individual (see the bottom of this page).

## External programs

| Tool | Licence | Notes for client work |
|---|---|---|
| FFmpeg (system or downloaded build) | LGPL-2.1+, builds with x264/x265 are GPL-2.0+ | Free to use for any purpose, including commercial video production. Cutroom runs it as a separate program and does not redistribute it; GPL obligations apply only if you redistribute the binary. Videos you make are not affected. |
| x264 (inside FFmpeg) | GPL-2.0+ | Same as above. On patents: as we understand the Via LA (formerly MPEG LA) AVC terms, H.264 video distributed free to viewers over the internet carries no royalty, and YouTube re-encodes uploads anyway. Not legal advice. |
| Node.js (Phase 3+) | MIT | Free. |
| Python 3.12 | PSF License | Free. |
| uv (optional, used by setup to install Python when missing, and by `scripts/lock.sh`) | MIT or Apache-2.0 | Free. |
| winget / Homebrew (package managers used by setup) | MIT / BSD-2-Clause | Free. |

## Python packages (requirements.lock)

All permissive (MIT, BSD, Apache-2.0, ISC, MPL-2.0, PSF). Read from the installed packages' metadata:

| Package | Version | Licence |
|---|---|---|
| faster-whisper | 1.2.1 | MIT |
| ctranslate2 | 4.8.5 | MIT |
| av (PyAV) | 16.1.0 | BSD-3-Clause |
| onnxruntime (Silero VAD runtime) | 1.31.1 | MIT |
| tokenizers | 0.23.3 | Apache-2.0 |
| huggingface-hub | 2.4.0 | Apache-2.0 |
| hf-xet | 1.8.0 | Apache-2.0 |
| numpy | 2.5.4 | BSD-3-Clause (+ 0BSD, MIT, Zlib, CC0 parts) |
| pydantic / pydantic-core | 2.14.3 / 2.50.4 | MIT |
| typer | 0.28.0 | MIT |
| click | 8.5.1 | BSD-3-Clause |
| rich | 15.0.0 | MIT |
| pyyaml | 6.0.3 | MIT |
| psutil | 7.2.2 | BSD-3-Clause |
| protobuf | 7.36.2 | BSD-3-Clause |
| flatbuffers | 25.12.19 | Apache-2.0 |
| httpx2 / httpcore2 | 2.16.0 | BSD-3-Clause |
| fsspec | 2026.9.0 | BSD-3-Clause |
| filelock, anyio, h11, truststore, typing-inspection, annotated-types, annotated-doc, markdown-it-py, mdurl, iniconfig, pluggy | various | MIT |
| idna | 3.20 | BSD-3-Clause |
| packaging | 26.3 | Apache-2.0 or BSD-2-Clause |
| pygments | 2.21.0 | BSD-2-Clause |
| shellingham | 1.5.4 | ISC |
| tqdm | 4.70.1 | MPL-2.0 and MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| colorama (Windows only) | 0.4.6 | BSD-3-Clause |
| soundfile | 0.14.0 | BSD-3-Clause (bundles libsndfile 1.2.2: LGPL-2.1, dynamically linked; no obligations for using it) |
| cffi / pycparser | 2.1.1 / 3.12 | MIT / BSD-3-Clause |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 (OpenCV and the wheel's bundled libraries) |
| pytest (development) | 9.2.0 | MIT |

### NVIDIA GPU libraries (`requirements-gpu.txt`, only installed on NVIDIA machines)

| Package | Version | Licence |
|---|---|---|
| nvidia-cublas-cu12 | 12.9.2.10 | NVIDIA proprietary EULA (free to use and redistribute as wheels) |
| nvidia-cudnn-cu12 | 9.28.1.4 | NVIDIA proprietary EULA (free to use) |

## Node packages (graphics/, pinned by `graphics/package-lock.json`)

| Package | Version | Licence |
|---|---|---|
| remotion, @remotion/bundler, @remotion/cli, @remotion/renderer, @remotion/layout-utils (+ their @remotion/* parts) | 4.0.541 | Remotion License: **free for you** (individual; see the Remotion section below) |
| react, react-dom | 19.3.1 | MIT |
| @fontsource/inter, @fontsource/montserrat (font files bundled, nothing fetched at render time) | 5.3.0 | SIL Open Font License 1.1 (fonts may be used in commercial videos) |
| mediabunny and its encoders (inside @remotion/media) | 1.64.0 | MPL-2.0 (file-level copyleft: obligations only when modifying and redistributing its files; Cutroom does neither) |
| All other transitive packages (≈ 230) | various | MIT, ISC, BSD-2/3-Clause, Apache-2.0, 0BSD, Unlicense, Python-2.0, CC-BY-4.0 (browser-support data), counted with a script over `node_modules` on 2026-11-07 |

Chrome Headless Shell (downloaded by `npx remotion browser ensure`, or any Chrome given via
`CUTROOM_BROWSER`): BSD-3-Clause (Chromium) plus bundled open-source components; free to use.

## Models (downloaded to `models/`, pinned revisions in `src/cutroom/perception/models.py`)

| Model | Source | Licence |
|---|---|---|
| Whisper weights (all sizes) | OpenAI | MIT |
| CTranslate2 conversions tiny.en … large-v3, distil-large-v3 | Systran on HuggingFace | MIT |
| large-v3-turbo CTranslate2 conversion | deepdml on HuggingFace | MIT |
| Silero VAD (bundled inside faster-whisper) | Silero Team | MIT |
| YuNet face detector `face_detection_yunet_2023mar.onnx` (pinned revision + SHA-256) | OpenCV Zoo, via huggingface.co/opencv/face_detection_yunet | MIT |

Transcripts produced by these models belong to you; the MIT licence puts no restriction on output.

## Bundled assets

| File | Licence | Source |
|---|---|---|
| `assets/samples/doctor_sample.mp4/.json` | CC0 (generated by Cutroom) | `scripts/make_doctor_sample.py`: FFmpeg flite voice "slt" (flite: BSD-style licence) + FFmpeg test pattern |
| `assets/samples/real_speech_stumble.opus/.json` | Public domain (US federal government work, 17 U.S.C. 105) | "Your Weekly Address", 4 January 2015, The White House, via archive.org; 13.5 s excerpt |

Fonts, music, SFX, LUTs and stock footage: none yet. Each will get a row here and a
per-asset metadata file (source URL, licence, date) when added in Phases 2–6.

## Remotion (Phase 3): eligible for the free licence

Remotion's LICENSE.md (checked 2026-11-07) allows free use for **individuals, for-profit
organisations with up to 3 employees, non-profits, and evaluation**; other companies need a
paid Company License. **You confirmed (2026-11-07) that you work as an individual**, so the
free licence applies, including for paid client work. Watch points:

- If the business grows beyond 3 employees, a Company License becomes required. The graphics
  layer is isolated behind its own interface so it can be swapped for the free
  Playwright-based alternative (`ARCHITECTURE.md` §11) instead of paying.
- Remotion 5.0 will "slightly change" the licence; re-check before upgrading across a major version.
- Remotion downloads a headless Chrome build at first render (free, BSD-style Chromium licence).
