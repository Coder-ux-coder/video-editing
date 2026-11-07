"""Motion-graphics templates: JSON specs in ``templates/`` that the agent fills with parameters.

A spec names the Remotion component that draws it, its variants, where it may sit on screen
(zone + anchors + size hint), which parameters are facts (numbers that must be verified against
the transcript or a provided document) and a typed parameter schema. Specs are validated here
so a bad graphic is refused before anything renders.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from cutroom.errors import CutroomError, RefusedError
from cutroom.paths import project_root

ZONES = {"lower", "side", "full", "region", "band", "corner", "edge"}
PARAM_TYPES = {"string", "integer", "number", "rect", "list", "data", "column", "asset", "color", "enum"}
_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\([\d\s.,%]+\)|[a-z]+)$")


@dataclass
class TemplateSpec:
    id: str
    component: str
    variants: list[str]
    default_duration_s: float
    description: str
    zone: str
    anchors: list[str] = field(default_factory=list)
    size_hint: dict | None = None
    fact_params: list[str] = field(default_factory=list)
    hides_captions: bool = False
    persistent: bool = False
    at_cut: bool = False
    params: dict = field(default_factory=dict)
    backing_variants: list[str] = field(default_factory=list)   # no panel of their own: auto backing on bright footage

    @property
    def text_params(self) -> list[str]:
        return [k for k, p in self.params.items() if p["type"] in ("string", "list", "column", "data")]


def templates_dir() -> Path:
    return project_root() / "templates"


def _check_spec(d: dict, path: Path) -> TemplateSpec:
    need = {"id", "component", "variants", "default_duration_s", "description", "zone", "params"}
    missing = need - d.keys()
    if missing:
        raise CutroomError(f"Template {path.name} is missing {sorted(missing)}.", fix="See templates/README.md.")
    if d["zone"] not in ZONES:
        raise CutroomError(f"Template {path.name}: unknown zone {d['zone']}.", fix=f"Use one of {sorted(ZONES)}.")
    for k, p in d["params"].items():
        if p.get("type") not in PARAM_TYPES:
            raise CutroomError(f"Template {path.name}: parameter {k} has unknown type {p.get('type')}.",
                               fix=f"Use one of {sorted(PARAM_TYPES)}.")
    for k in d.get("fact_params", []):
        if k not in d["params"]:
            raise CutroomError(f"Template {path.name}: fact parameter {k} is not a parameter.", fix="Fix the spec.")
    if path.stem != d["id"]:
        raise CutroomError(f"Template {path.name} has id {d['id']}.", fix="The file name must equal the id.")
    return TemplateSpec(**{k: v for k, v in d.items() if k in TemplateSpec.__dataclass_fields__})


def load_templates(folder: Path | None = None) -> dict[str, TemplateSpec]:
    folder = folder or templates_dir()
    out = {}
    for p in sorted(folder.glob("*.json")):
        out[p.stem] = _check_spec(json.loads(p.read_text(encoding="utf-8")), p)
    return out


def get_template(tid: str) -> TemplateSpec:
    specs = load_templates()
    if tid not in specs:
        raise RefusedError(f"Unknown template '{tid}'.", fix=f"Known: {', '.join(specs)} (`cutroom templates`).")
    return specs[tid]


def _num(v, k: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            raise RefusedError(f"Parameter {k} must be a number, got {v!r}.", fix="Pass a plain number.") from None
    return float(v)


def validate_params(spec: TemplateSpec, params: dict, variant: str | None) -> tuple[dict, str]:
    """Type-check and coerce parameters; returns (params, variant). Refuses unknown or missing ones."""
    variant = variant or spec.variants[0]
    if variant not in spec.variants:
        raise RefusedError(f"Template {spec.id} has no variant '{variant}'.", fix=f"Use one of {spec.variants}.")
    unknown = set(params) - set(spec.params)
    if unknown:
        raise RefusedError(f"Template {spec.id} has no parameter(s) {sorted(unknown)}.",
                           fix=f"Parameters: {', '.join(spec.params)}.")
    out = {}
    for k, p in spec.params.items():
        if k not in params or params[k] in (None, ""):
            if p.get("required"):
                raise RefusedError(f"Template {spec.id} needs parameter '{k}'.", fix=f"Pass --param {k}=...")
            continue
        v, t = params[k], p["type"]
        if t == "string":
            v = str(v).strip()
            if len(v) > p.get("max_len", 200):
                raise RefusedError(f"'{k}' is {len(v)} characters; at most {p['max_len']} fit this template.",
                                   fix="Shorten the text (graphics are read in a glance).")
        elif t == "integer":
            v = int(_num(v, k))
        elif t == "number":
            v = _num(v, k)
            v = int(v) if v.is_integer() else v
        elif t == "enum":
            if v not in p.get("values", []):
                raise RefusedError(f"'{k}' must be one of {p.get('values')}.", fix="Pick a listed value.")
        elif t == "color":
            if not _COLOR.match(str(v)):
                raise RefusedError(f"'{k}' is not a colour: {v!r}.", fix="Use #RRGGBB or a CSS colour name.")
        elif t == "rect":
            if isinstance(v, str):
                v = dict(zip("xywh", (_num(x, k) for x in v.split(","))))
            if set(v) != set("xywh") or not all(0 <= v[a] <= 1 for a in "xywh") or v["x"] + v["w"] > 1.001 \
                    or v["y"] + v["h"] > 1.001:
                raise RefusedError(f"'{k}' must be x,y,w,h as fractions of the frame.", fix="e.g. 0.6,0.2,0.3,0.25")
        elif t == "list":
            if isinstance(v, str):
                v = [s.strip() for s in v.split("|") if s.strip()]
            if not v or len(v) > p.get("max_items", 6):
                raise RefusedError(f"'{k}' needs 1-{p.get('max_items', 6)} items.", fix="Separate items with |.")
            v = [str(s) for s in v]
            long = [s for s in v if len(s) > p.get("max_len", 48)]
            if long:
                raise RefusedError(f"List item too long for {spec.id}: {long[0]!r}.", fix="Keep items short.")
        elif t == "data":
            if isinstance(v, str):   # "2019:12, 2020:30"
                v = [{"label": a.strip(), "value": _num(b, k)} for a, b in (x.rsplit(":", 1) for x in v.split(","))]
            if not v or len(v) > p.get("max_items", 8) or not all({"label", "value"} <= set(d) for d in v):
                raise RefusedError(f"'{k}' must be label:value pairs (1-{p.get('max_items', 8)}).",
                                   fix="e.g. --param data='2019:12,2020:30'")
            v = [{"label": str(d["label"]), "value": _num(d["value"], k)} for d in v]
        elif t == "column":
            if isinstance(v, str):   # "Title|item|item"
                parts = [s.strip() for s in v.split("|")]
                v = {"title": parts[0], "items": [s for s in parts[1:] if s]}
            if "title" not in v:
                raise RefusedError(f"'{k}' needs a title and items.", fix="e.g. 'Before|slow|manual'")
        elif t == "asset":
            v = str(v)
        out[k] = v
    return out, variant


def visible_words(spec: TemplateSpec, params: dict) -> int:
    """Words a viewer has to read (for the minimum hold time)."""
    n = 0
    for k in spec.text_params:
        v = params.get(k)
        if isinstance(v, str):
            n += len(v.split())
        elif isinstance(v, list):
            n += sum(len(str(x.get("label", x) if isinstance(x, dict) else x).split()) for x in v)
        elif isinstance(v, dict):
            n += len(str(v.get("title", "")).split()) + sum(len(str(x).split()) for x in v.get("items", []))
    return n


def min_hold(spec: TemplateSpec, params: dict, rules: dict) -> float:
    """EDITING_STANDARDS §7.4: at least 1 s plus 0.3 s per 3 words on screen."""
    if spec.at_cut or spec.persistent:
        return 0.0
    return rules.get("min_hold_s", 1.0) + rules.get("hold_per_3_words_s", 0.3) * visible_words(spec, params) / 3
