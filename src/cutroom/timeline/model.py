"""The project model: one canonical, versioned JSON timeline per edit.

Times are seconds (floats, millisecond precision). ``src_in``/``src_out`` are source
time; ``start`` is timeline time. The renderer snaps video edges to frames and audio
edges to samples. Every clip and cut carries a ``reason`` so the edit explains itself.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator

from cutroom.joblog import now_iso

SCHEMA = "cutroom.timeline/1"
EPS = 0.0005

CutKind = Literal["silence", "filler", "acoustic_filler", "retake", "stumble", "head", "tail",
                  "orphan", "manual", "non_speech"]


class ChangeEntry(BaseModel):
    version: int
    command: str = Field(description="The cutroom command that produced this version.")
    summary: str = Field(description="One human-readable line: what changed and why.")
    at: str = Field(default_factory=now_iso)


class Meta(BaseModel):
    schema_: str = Field(SCHEMA, alias="schema")
    job_id: str
    version: int = 1
    parent: int | None = Field(None, description="Version this one was derived from.")
    created_at: str = Field(default_factory=now_iso)
    title: str | None = None
    client: str | None = None
    brief: str = "brief.md"
    style: str = "default"
    client_style: str | None = Field(None, description="Client theme (styles/clients/<name>.style.yaml); "
                                                       "defaults to the job's client.")
    fps: float = Field(description="Output frame rate (snapped standard rate, e.g. 29.97).")
    width: int
    height: int
    aspect: str = "16:9"
    sample_rate: int = 48000
    language: str = "en"
    footage_type: str | None = Field(None, description="talking_head, podcast, screen_recording, vlog, mixed")
    changelog: list[ChangeEntry] = Field(default_factory=list, description="Full history up to this version.")

    model_config = {"populate_by_name": True}


class SourceRef(BaseModel):
    id: str
    path: str = Field(description="Job-relative or absolute path of the original (full-resolution) file.")
    hash: str
    duration: float
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    has_video: bool = True
    has_audio: bool = True
    proxy: str | None = None
    audio: str | None = Field(None, description="48 kHz analysis/render audio (FLAC).")
    words: str | None = Field(None, description="Edit-ready words file (refined timings).")


class Track(BaseModel):
    id: str
    kind: Literal["video", "audio", "graphics", "captions"]
    name: str = ""
    locked: bool = False
    muted: bool = False


class Keyframe(BaseModel):
    t: float = Field(description="Clip-relative time (s).")
    x: float = Field(0.5, description="Crop centre, 0..1 of source width.")
    y: float = Field(0.5, description="Crop centre, 0..1 of source height.")
    scale: float = Field(1.0, ge=1.0, description="Zoom factor (1 = no punch-in).")
    ease: Literal["linear", "ease_in_out", "hold"] = "ease_in_out"


class Transition(BaseModel):
    kind: Literal["cut", "crossfade", "dip_to_black", "zoom", "whip", "swipe", "glitch"] = "cut"
    duration: float = 0.0


class Effect(BaseModel):
    name: str
    params: dict = Field(default_factory=dict)


class Clip(BaseModel):
    id: str
    track: str = "V1"
    source: str
    src_in: float
    src_out: float
    start: float = Field(0.0, description="Timeline position (s); recomputed by ripple packing.")
    speed: float = Field(1.0, gt=0)
    video: bool = True
    audio: bool = True
    volume_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    transition_in: Transition | None = None
    reframe: list[Keyframe] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    reason: str = ""
    confidence: float | None = None
    locked: bool = Field(False, description="The agent must not alter a locked clip.")

    @model_validator(mode="after")
    def _check(self) -> "Clip":
        if self.src_out - self.src_in <= EPS:
            raise ValueError(f"clip {self.id}: src_out ({self.src_out}) must be after src_in ({self.src_in})")
        return self

    # The timeline's frame rate, set by the Timeline. The renderer cuts on the frame grid, so
    # durations and time mapping use frame-snapped edges: timeline time == rendered time.
    _fps: float | None = PrivateAttr(default=None)

    def _q(self, s: float) -> float:
        return round(s * self._fps) / self._fps if self._fps else s

    @property
    def duration(self) -> float:
        return (self._q(self.src_out) - self._q(self.src_in)) / self.speed

    @property
    def end(self) -> float:
        return self.start + self.duration

    def to_source(self, t: float) -> float:
        """Timeline time → source time inside this clip (as rendered)."""
        return self._q(self.src_in) + (t - self.start) * self.speed

    def to_timeline(self, s: float) -> float:
        return self.start + (s - self._q(self.src_in)) / self.speed


class CutDecision(BaseModel):
    id: str
    source: str
    src_in: float
    src_out: float
    kind: CutKind
    reason: str
    confidence: float | None = None
    text: str = Field("", description="Words removed, for readability.")
    tool: str
    version: int = Field(description="Version that introduced the cut.")
    restored: bool = False

    @property
    def duration(self) -> float:
        return self.src_out - self.src_in


class Graphic(BaseModel):
    id: str
    template: str = Field(description="Template id (templates/<id>.json).")
    variant: str | None = None
    params: dict = Field(default_factory=dict)
    start: float
    end: float
    track: str = "G1"
    anchor: str = "auto"
    placement: dict = Field(default_factory=dict, description="Resolved {anchor, x, y} (frame fractions).")
    animation: str | None = None
    z: int = 0
    transcript_span: tuple[float, float] | None = None
    facts: list[str] = Field(default_factory=list,
                             description="Where every number/claim shown comes from (transcript span or document).")
    fact_check: dict = Field(default_factory=dict, description="Numbers verified against the references when added.")
    attach: dict | None = Field(None, description="{source, t, offset}: the source moment the graphic is attached "
                                                 "to. Edits move it with its content (start = that moment + offset).")
    whole: bool = Field(False, description="Spans the whole video (progress bar, logo bug); follows its length.")
    hides_captions: bool = Field(False, description="Burned-in captions are hidden while it is on screen.")
    backing: bool | None = Field(None, description="Panel behind panel-less text: None = automatic (contrast of the "
                                                   "theme's text colours against bg_luminance), or forced on/off.")
    bg_luminance: list[float] | None = Field(None, description="Footage under the graphic: [25th, 75th] percentile WCAG "
                                                               "relative luminance (darkest look, brightest look).")
    reason: str = ""


class CaptionCue(BaseModel):
    start: float = Field(description="Timeline time the caption appears (s).")
    end: float = Field(description="Timeline time it disappears (s).")
    text: str
    words: list[dict] = Field(default_factory=list, description="[{text, start, end}] in timeline time.")
    lines: list[list[int]] = Field(default_factory=list, description="Word indices per line.")
    emphasis: list[int] = Field(default_factory=list, description="Indices of emphasised words.")
    placement: Literal["bottom", "top"] = "bottom"
    scrim: bool = Field(False, description="Darkening behind the text, added when the background is too bright.")


class Captions(BaseModel):
    style: str = "clean"
    safe_zone: str = "youtube_long"
    max_lines: int = 2
    burn_in: bool = Field(True, description="Draw the captions into the video (files are always exported too).")
    cues: list[CaptionCue] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


class AudioSpec(BaseModel):
    target_lufs: float = -14.0
    true_peak_dbtp: float = -1.0
    voice_chain: dict = Field(default_factory=dict)
    ducking: dict = Field(default_factory=dict)
    music: list[dict] = Field(default_factory=list, description="Each with path, licence, gain, start/end.")


class Marker(BaseModel):
    id: str
    t: float
    end: float | None = None
    kind: Literal["chapter", "hook", "note", "qa", "cta"]
    text: str


class Output(BaseModel):
    id: str
    kind: Literal["youtube_long", "youtube_short"]
    width: int
    height: int
    fps: float
    range: tuple[float, float] | None = Field(None, description="Timeline range for a derived output (Shorts).")
    title: str | None = None


class Timeline(BaseModel):
    meta: Meta
    sources: list[SourceRef]
    tracks: list[Track] = Field(default_factory=lambda: [Track(id="V1", kind="video", name="Main"),
                                                         Track(id="G1", kind="graphics", name="Graphics"),
                                                         Track(id="C1", kind="captions", name="Captions")])
    clips: list[Clip] = Field(default_factory=list)
    cuts: list[CutDecision] = Field(default_factory=list, description="Every removed source range, with reasons.")
    graphics: list[Graphic] = Field(default_factory=list)
    captions: Captions | None = None
    audio: AudioSpec = Field(default_factory=AudioSpec)
    markers: list[Marker] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)

    @model_validator(mode="after")
    def _frame_grid(self) -> "Timeline":
        self.attach_fps()
        return self

    def attach_fps(self) -> None:
        for c in self.clips:
            c._fps = self.meta.fps

    # ------------------------------------------------------------------ helpers
    def source(self, sid: str) -> SourceRef:
        for s in self.sources:
            if s.id == sid:
                return s
        raise KeyError(sid)

    def track_clips(self, track: str = "V1") -> list[Clip]:
        return sorted((c for c in self.clips if c.track == track), key=lambda c: c.start)

    @property
    def duration(self) -> float:
        return max((c.end for c in self.clips), default=0.0)

    def active_cuts(self) -> list[CutDecision]:
        return [c for c in self.cuts if not c.restored]

    def next_id(self, prefix: str, existing: list[str]) -> str:
        nums = [int(e[len(prefix):].split(".")[0]) for e in existing if e.startswith(prefix)
                and e[len(prefix):].split(".")[0].isdigit()]
        return f"{prefix}{(max(nums) + 1) if nums else 1:04d}"


def validate_timeline(tl: Timeline) -> list[str]:
    """Structural problems a schema cannot express. Empty list = valid."""
    problems = []
    ids = [c.id for c in tl.clips]
    if len(ids) != len(set(ids)):
        problems.append("duplicate clip ids")
    src = {s.id: s for s in tl.sources}
    tracks = {t.id for t in tl.tracks}
    for c in tl.clips:
        if c.source not in src:
            problems.append(f"clip {c.id}: unknown source {c.source}")
            continue
        if c.track not in tracks:
            problems.append(f"clip {c.id}: unknown track {c.track}")
        if c.src_in < -EPS or c.src_out > src[c.source].duration + 0.05:
            problems.append(f"clip {c.id}: source range {c.src_in:.3f}-{c.src_out:.3f} outside "
                            f"{c.source} (0-{src[c.source].duration:.3f})")
    for track in tracks:
        clips = tl.track_clips(track)
        for a, b in zip(clips, clips[1:]):
            if b.start < a.end - 0.001 and not (b.transition_in and b.transition_in.duration > 0):
                problems.append(f"clips {a.id} and {b.id} overlap on {track} without a transition")
    for g in tl.graphics:
        if g.end <= g.start:
            problems.append(f"graphic {g.id}: end before start")
    return problems


def timeline_warnings(tl: Timeline) -> list[str]:
    """Things that are valid but need the editor's attention (shown by `validate` and the review)."""
    out = []
    for g in tl.graphics:
        if g.attach and g.attach.get("detached"):
            out.append(f"graphic {g.id} ({g.template}): the moment it was attached to "
                       f"({g.attach['source']}@{g.attach['t']:.2f}s) is no longer in the edit")
        if g.fact_check.get("unsupported_numbers"):
            out.append(f"graphic {g.id}: numbers without a source: {g.fact_check['unsupported_numbers']}")
    return out
