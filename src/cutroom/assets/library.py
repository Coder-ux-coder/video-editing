"""Licence-tracked asset library: b-roll stills and clips, music, sound effects.

Every file has a sidecar ``<id>.json`` with its licence, author, source URL, attribution text and
hash. Only licences that allow commercial use without share-alike or no-derivatives terms are
accepted (public domain, CC0, CC BY): anything else is refused, never "probably fine". Assets
Cutroom generates itself are recorded as such. Client-provided media stay in the job (inputs/).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cutroom.errors import CutroomError, RefusedError
from cutroom.job import atomic_write_text, now_iso
from cutroom.paths import assets_dir

KINDS = ("image", "video", "music", "sfx")
EXT = {"image": {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}, "video": {".mp4", ".mov", ".mkv", ".webm"},
       "music": {".wav", ".flac", ".mp3", ".ogg", ".opus", ".m4a"}, "sfx": {".wav", ".flac", ".mp3", ".ogg", ".opus"}}
USER_AGENT = "Cutroom/0.1 (local video editor fetching free-licensed stills; https://github.com/coder-ux-coder/video-editing)"
# Wikimedia serves only standard thumbnail widths (mediawiki.org/wiki/Common_thumbnail_sizes):
# 20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920, 3840. Other widths are rejected (HTTP 429).
COMMONS_WIDTH = 1920


def normalize_license(name: str) -> tuple[str, bool]:
    """(canonical short name, attribution required). Refuses anything not on the allowlist."""
    n = re.sub(r"\s+", " ", (name or "").strip()).lower()
    if n in ("public domain", "public-domain", "pd", "pdm", "public domain mark", "pd-usgov", "pd-us-gov", "cc pdm 1.0",
             "pd-usgov-interior-nps", "pd-usgov-nps", "us government work", "generated"):
        return ("public-domain" if n != "generated" else "generated"), False
    if n.startswith("pd-") or n.startswith("public domain"):
        return "public-domain", False
    if n in ("cc0", "cc0 1.0", "cc-zero", "cc0-1.0"):
        return "CC0-1.0", False
    m = re.fullmatch(r"cc[ -]by(?:[ -]?(\d\.\d))?", n)
    if m:
        return f"CC-BY-{m.group(1) or '4.0'}", True
    raise RefusedError(f"Licence '{name}' is not on the allowlist (public domain, CC0, CC BY).",
                       fix="Share-alike, non-commercial and no-derivatives licences are refused for client work. "
                           "Find another asset or generate one.")


@dataclass
class Asset:
    id: str
    kind: str
    file: str                  # relative to the library folder
    license: str
    attribution_required: bool
    author: str = ""
    title: str = ""
    source_url: str = ""
    license_url: str = ""
    attribution: str = ""
    tags: list[str] = field(default_factory=list)
    sha256: str = ""
    added_at: str = field(default_factory=now_iso)
    width: int | None = None
    height: int | None = None
    duration: float | None = None

    @property
    def credit(self) -> str:
        if self.license == "generated":
            return ""
        who = self.author or "unknown author"
        base = self.attribution or f"“{self.title or self.id}” by {who}"
        return f"{base} ({self.license}{', ' + self.source_url if self.source_url else ''})"


def library_dir() -> Path:
    return assets_dir() / "library"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or "asset"


def _probe(p: Path, kind: str) -> dict:
    if kind == "image":
        import cv2

        img = cv2.imread(str(p))
        return {"width": int(img.shape[1]), "height": int(img.shape[0])} if img is not None else {}
    from cutroom.ingest.probe import probe

    info = probe(p)
    v = info.primary_video
    return {"duration": round(info.duration, 3), "width": v.display_width if v else None,
            "height": v.display_height if v else None}


def add_file(path: Path, *, kind: str, license: str, author: str = "", title: str = "", source_url: str = "",
             license_url: str = "", attribution: str = "", tags: list[str] | None = None, move: bool = False) -> Asset:
    if kind not in KINDS:
        raise RefusedError(f"Unknown asset kind '{kind}'.", fix=f"Use one of {KINDS}.")
    if path.suffix.lower() not in EXT[kind]:
        raise RefusedError(f"{path.name} is not a {kind} file.", fix=f"Expected {sorted(EXT[kind])}.")
    lic, attr = normalize_license(license)
    if attr and not (author or attribution):
        raise RefusedError(f"{lic} requires attribution.", fix="Pass --author (and --attribution if the source asks for a wording).")
    digest = _sha256(path)
    for a in list_assets():
        if a.sha256 == digest:
            return a                       # already in the library
    aid = f"{kind}-{_slug(title or path.stem)}-{digest[:6]}"
    dst = library_dir() / kind / f"{aid}{path.suffix.lower()}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    (shutil.move if move else shutil.copy2)(str(path), str(dst))
    a = Asset(id=aid, kind=kind, file=str(dst.relative_to(library_dir()).as_posix()), license=lic,
              attribution_required=attr, author=author, title=title, source_url=source_url, license_url=license_url,
              attribution=attribution, tags=sorted({t.lower() for t in (tags or [])}), sha256=digest,
              **_probe(dst, kind))
    atomic_write_text(dst.with_suffix(".json"), json.dumps(asdict(a), indent=1) + "\n")
    return a


def list_assets(kind: str | None = None) -> list[Asset]:
    out = []
    root = library_dir()
    if not root.is_dir():
        return out
    for js in sorted(root.glob("*/*.json")):
        d = json.loads(js.read_text(encoding="utf-8"))
        if kind is None or d["kind"] == kind:
            out.append(Asset(**d))
    return out


def get_asset(aid: str) -> Asset:
    for a in list_assets():
        if a.id == aid:
            return a
    raise RefusedError(f"No asset '{aid}' in the library.", fix="List them with `cutroom assets`.")


def asset_path(a: Asset) -> Path:
    return library_dir() / a.file


# --------------------------------------------------------------------------- Wikimedia Commons


def _wait(e: urllib.error.HTTPError, attempt: int) -> None:
    """Honour the server's Retry-After (seconds), else back off; capped so a run never hangs."""
    try:
        sec = float(e.headers.get("Retry-After", ""))
    except ValueError:
        sec = 5.0 * (attempt + 1)
    time.sleep(min(60.0, sec + 1.0))


def _open(url: str, tries: int = 6):
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            return urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == tries - 1:
                raise CutroomError(f"Wikimedia answered HTTP {e.code} for {url.split('?')[0]}.",
                                   fix="Wait a few minutes and retry (Wikimedia limits request rates).") from e
            _wait(e, attempt)
        except OSError as e:
            raise CutroomError("Wikimedia Commons is not reachable.", fix="Check the network.", details=str(e)) from e
    raise AssertionError("unreachable")


def _get_json(url: str) -> dict:
    with _open(url) as r:
        return json.loads(r.read().decode("utf-8"))


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def commons_search(query: str, limit: int = 10, width: int = COMMONS_WIDTH) -> list[dict]:
    """Files on Wikimedia Commons matching ``query``, with their licence metadata (not downloaded)."""
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {query}",
              "gsrnamespace": "6", "gsrlimit": str(min(50, limit * 3)), "prop": "imageinfo",
              "iiprop": "url|size|extmetadata|mime", "iiurlwidth": str(width)}
    data = _get_json("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params))
    out = []
    for page in sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        ii = (page.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        lic = meta.get("LicenseShortName", {}).get("value", "")
        try:
            norm, attr = normalize_license(lic)
        except RefusedError:
            continue
        url = ii.get("thumburl") or ii.get("url")
        if "/thumb/" not in (url or "") and width > 1280:
            # The original came back (it is exactly the requested width): originals are throttled
            # for automated clients, so take the next standard thumbnail size down.
            again = _get_json("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(
                {"action": "query", "format": "json", "titles": page["title"], "prop": "imageinfo",
                 "iiprop": "url|size", "iiurlwidth": "1280"}))
            ii2 = (next(iter(again.get("query", {}).get("pages", {}).values()), {}).get("imageinfo") or [{}])[0]
            ii = {**ii, "thumburl": ii2.get("thumburl"), "thumbwidth": ii2.get("thumbwidth"),
                  "thumbheight": ii2.get("thumbheight")}
            time.sleep(0.5)
        out.append({"title": page["title"].removeprefix("File:"), "license": norm, "attribution_required": attr,
                    "license_raw": lic, "license_url": meta.get("LicenseUrl", {}).get("value", ""),
                    "author": _strip_html(meta.get("Artist", {}).get("value", "")),
                    "credit": _strip_html(meta.get("Credit", {}).get("value", "")),
                    "description": _strip_html(meta.get("ImageDescription", {}).get("value", ""))[:300],
                    "url": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl", ""),
                    "width": ii.get("thumbwidth") or ii.get("width"), "height": ii.get("thumbheight") or ii.get("height")})
        if len(out) >= limit:
            break
    return out


def commons_fetch(item: dict, tags: list[str], scratch: Path) -> Asset:
    """Download one Commons search result into the library with its licence."""
    scratch.mkdir(parents=True, exist_ok=True)
    ext = Path(urllib.parse.urlparse(item["url"]).path).suffix.lower() or ".jpg"
    tmp = scratch / f"commons{ext}"
    url = item["url"].split("?")[0]          # the API's thumbnail URL, without tracking parameters
    time.sleep(1.0)                          # polite pacing between downloads
    with _open(url) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f)
    title = Path(item["title"]).stem
    attribution = (f"“{title}” by {item['author']}" if item["attribution_required"] else "")
    return add_file(tmp, kind="image", license=item["license"], author=item["author"], title=title,
                    source_url=item["page"], license_url=item["license_url"], attribution=attribution,
                    tags=tags + re.findall(r"[a-z]{4,}", (item["description"] + " " + title).lower())[:20], move=True)
