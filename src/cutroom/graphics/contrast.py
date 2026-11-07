"""WCAG 2.x contrast between theme colours and footage, for backing panels and theme checks."""

from __future__ import annotations

import re

LARGE_TEXT = 3.0    # WCAG 1.4.3: large text (≥ 24 px, or 18.7 px bold) needs 3:1
BODY_TEXT = 4.5     # everything else 4.5:1


def parse_color(c: str) -> tuple[float, float, float, float] | None:
    """'#RGB', '#RRGGBB', '#RRGGBBAA', 'rgb(...)', 'rgba(...)' → (r, g, b, a) in 0..1."""
    c = c.strip().lower()
    if c.startswith("#"):
        h = c[1:]
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        if len(h) not in (6, 8):
            return None
        vals = [int(h[i:i + 2], 16) / 255 for i in range(0, len(h), 2)]
        return (*vals[:3], vals[3] if len(vals) == 4 else 1.0)
    m = re.fullmatch(r"rgba?\(([^)]*)\)", c)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        try:
            rgb = [float(p[:-1]) / 100 if p.endswith("%") else float(p) / 255 for p in parts[:3]]
            a = float(parts[3]) if len(parts) > 3 else 1.0
        except ValueError:
            return None
        return (*rgb, a)
    named = {"white": (1.0, 1.0, 1.0, 1.0), "black": (0.0, 0.0, 0.0, 1.0)}
    return named.get(c)


def luminance(rgb: tuple[float, float, float]) -> float:
    lin = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _srgb_grey(lum: float) -> float:
    """The sRGB value of a grey with relative luminance ``lum``."""
    return lum * 12.92 if lum <= 0.0031308 else 1.055 * lum ** (1 / 2.4) - 0.055


def over(fg: tuple, bg_lum: float) -> float:
    """Luminance of a (possibly translucent) colour composited over a grey background of
    luminance ``bg_lum``, blended in gamma-encoded sRGB as browsers do."""
    g = _srgb_grey(bg_lum)
    return luminance(tuple(fg[3] * c + (1 - fg[3]) * g for c in fg[:3]))


def needs_backing(theme: dict, bg: list[float] | None, *, body: float = BODY_TEXT, large: float = LARGE_TEXT) -> bool:
    """Would the theme's text (body contrast) or accent (large-text contrast) fail against the
    footage under the graphic, at its darkest or brightest look?"""
    if not bg:
        return False
    colors = theme.get("colors", {})
    text = parse_color(colors.get("text", "#FFFFFF"))
    accent = parse_color(colors.get("accent", "#FFD60A"))
    for lum in bg:
        if text and ratio(luminance(text[:3]), lum) < body:
            return True
        if accent and ratio(luminance(accent[:3]), lum) < large:
            return True
    return False


def theme_problems(theme: dict) -> list[str]:
    """Token combinations that fail WCAG on the theme's own panels (checked when switching theme)."""
    colors = theme.get("colors", {})
    out = []
    surface = parse_color(colors.get("surface", "rgba(12,12,18,0.84)"))
    if surface is None:
        return ["surface colour is not parseable"]
    for bg_name, bg_lum in (("dark footage", 0.02), ("bright footage", 0.8)):
        s_lum = over(surface, bg_lum)
        for name, need in (("text", BODY_TEXT), ("textMuted", BODY_TEXT), ("accent", LARGE_TEXT)):
            col = parse_color(colors.get(name, ""))
            if col is None:
                continue
            r = ratio(over(col, s_lum), s_lum)
            if r < need:
                out.append(f"{name} on surface over {bg_name}: {r:.1f}:1 (needs {need}:1)")
    return out
