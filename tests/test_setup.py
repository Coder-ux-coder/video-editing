"""Pure-function tests for the stdlib-only setup bootstrap."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "setup" / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)

ASSETS = [{"name": n, "browser_download_url": f"https://example/{n}"} for n in [
    "ffmpeg-master-latest-win64-gpl.zip",
    "ffmpeg-n7.1-latest-win64-gpl-7.1.zip",
    "ffmpeg-n8.0-latest-win64-gpl-8.0.zip",
    "ffmpeg-n8.0-latest-win64-lgpl-8.0.zip",
    "ffmpeg-n8.0-latest-win64-gpl-shared-8.0.zip",
    "ffmpeg-n7.1-latest-linux64-gpl-7.1.tar.xz",
    "ffmpeg-n8.0-latest-linuxarm64-gpl-8.0.tar.xz",
]]


def test_picks_newest_release_gpl_static_build():
    assert bootstrap.pick_btbn_asset(ASSETS, "win64", "Windows").endswith("ffmpeg-n8.0-latest-win64-gpl-8.0.zip")
    assert bootstrap.pick_btbn_asset(ASSETS, "linux64", "Linux").endswith("ffmpeg-n7.1-latest-linux64-gpl-7.1.tar.xz")
    assert bootstrap.pick_btbn_asset(ASSETS, "winarm64", "Windows") is None


def test_platform_mapping():
    assert bootstrap.btbn_platform("Windows", "x86_64") == "win64"
    assert bootstrap.btbn_platform("Linux", "arm64") == "linuxarm64"
    assert bootstrap.btbn_platform("Darwin", "arm64") is None   # macOS uses Homebrew


def test_detects_full_ffmpeg_here():
    import shutil

    ff = shutil.which("ffmpeg")
    if ff:
        ok, why = bootstrap.ffmpeg_is_full(Path(ff))
        assert ok, why
