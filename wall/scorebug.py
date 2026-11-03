"""Read a broadcast's scorebug off a tile's frame: where the two team scores are, and what they say now.

Used to measure how far a tile's video runs behind real life (wall/sync.py): when the score on screen changes, the
ESPN play that produced that score has a wall-clock time, and the difference is the tile's delay.

OCR is the tesseract CLI (on the homelab: /usr/bin/tesseract 5.5, eng data) fed by Pillow (already a dependency via
matplotlib). No OpenCV. Bugs differ a lot (white digits on team colours, dark digits on light grey), so every pass runs
on three versions of the image: as is, dark ink only, light ink only.

* find(img, pairs): searches the bottom and top bands for two numbers that are a score ESPN reported recently
  (away, home) — side by side or stacked, similar size, close together → a Bug with each score's place as fractions of
  the frame (so the same bug still reads when the tile is resized).
* read(img, bug): OCRs just the two score cells (one line each, digits only) → (away, home).
"""
from __future__ import annotations

import io
import shutil
import subprocess
from dataclasses import dataclass

from PIL import Image, ImageOps

BANDS = ((0.62, 1.0), (0.0, 0.3))   # bottom band first: almost every NBA/NFL bug lives there
TARGET_W = 1920                     # sync.grab takes tiles at the video's resolution; anything smaller is upscaled to it


@dataclass(frozen=True)
class Word:
    n: int
    x: float   # centre, frame fractions
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class Bug:
    away: Word
    home: Word
    box: tuple[float, float, float, float]   # x0, y0, x1, y1 (fractions): both cells with room to grow — what's grabbed
    width: int = TARGET_W                    # the frame width it was found at (a small bug reads upscaled)

    @property
    def h(self) -> float:
        return max(self.away.h, self.home.h)


def available() -> bool:
    return shutil.which("tesseract") is not None


def variants(g: Image.Image) -> list[Image.Image]:
    """As is, dark ink only (digits on light grey), light ink only (digits on team colours)."""
    return [g, g.point(lambda v: 0 if v < 90 else 255), g.point(lambda v: 0 if v > 170 else 255)]


def _prep(img: Image.Image, frac_w: float = 1.0, width: int = TARGET_W) -> Image.Image:
    """Grey, and upscaled when the frame it comes from would be under `width` px across (contrast is stretched per crop)."""
    g = ImageOps.grayscale(img)
    k = width * frac_w / max(1, img.width)
    if k > 1.05:
        g = g.resize((round(g.width * k), round(g.height * k)), Image.LANCZOS)
    return g


def tesseract_tsv(img: Image.Image, psm: int = 11, digits: bool = False, timeout: float = 8) -> str:
    """tesseract's TSV for img. No digit whitelist on a wide area: it turns team names into digits glued to the score
    ("106 MIA" → "1067…"); a single cell (psm 7) can have it."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    cmd = ["tesseract", "stdin", "stdout", "--psm", str(psm), *(["-c", "tessedit_char_whitelist=0123456789"] if digits else []), "tsv"]
    r = subprocess.run(["nice", "-n", "10", *cmd], input=buf.getvalue(), capture_output=True, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def parse_tsv(tsv: str, min_conf: float = 30) -> list[tuple[int, float, float, float, float]]:
    """tesseract TSV → the numbers it read (1–3 digits): (n, x, y, w, h) in pixels of what it read."""
    out = []
    for line in tsv.splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 12 or f[0] != "5":
            continue
        text = f[11].strip().strip(".,|'\"’")
        try:
            conf = float(f[10])
        except ValueError:
            continue
        if not text.isdigit() or len(text) > 3 or conf < min_conf:
            continue
        out.append((int(text), *(float(v) for v in f[6:10])))
    return out


class _Frame:
    """img = the whole frame, or only its `part` (fractions) when only that was grabbed; maps fractions ↔ pixels."""

    def __init__(self, img: Image.Image, part: tuple[float, float, float, float], width: int = TARGET_W) -> None:
        self.part = part
        self.pw, self.ph = part[2] - part[0], part[3] - part[1]
        self.g = _prep(img, self.pw, width)
        self.sx, self.sy = self.g.width / self.pw, self.g.height / self.ph   # px per whole frame

    def crop(self, box) -> tuple[Image.Image, int, int] | None:
        x0 = max(0, round((box[0] - self.part[0]) * self.sx))
        y0 = max(0, round((box[1] - self.part[1]) * self.sy))
        x1 = min(self.g.width, round((box[2] - self.part[0]) * self.sx))
        y1 = min(self.g.height, round((box[3] - self.part[1]) * self.sy))
        return (ImageOps.autocontrast(self.g.crop((x0, y0, x1, y1))), x0, y0) if x1 - x0 >= 8 and y1 - y0 >= 8 else None

    def word(self, n, x0, y0, x, y, w, h) -> Word:
        return Word(n, self.part[0] + (x0 + x + w / 2) / self.sx, self.part[1] + (y0 + y + h / 2) / self.sy, w / self.sx, h / self.sy)


def words(img: Image.Image, box, ocr=tesseract_tsv, part=(0.0, 0.0, 1.0, 1.0), width: int = TARGET_W) -> list[Word]:
    """Every number tesseract finds inside box (frame fractions), over the three image versions."""
    fr = _Frame(img, part, width)
    c = fr.crop(box)
    if not c:
        return []
    crop, x0, y0 = c
    out: list[Word] = []
    for v in variants(crop):
        for n, x, y, w, h in parse_tsv(ocr(v)):
            wd = fr.word(n, x0, y0, x, y, w, h)
            if not any(o.n == wd.n and abs(o.x - wd.x) < o.w and abs(o.y - wd.y) < o.h for o in out):
                out.append(wd)
    return out


def _paired(a: Word, h: Word) -> bool:
    """Two scores of one bug: same row (or stacked in one column), digits of similar height, close together."""
    if a is h or not 0.6 < a.h / h.h < 1.6:
        return False
    dh = max(a.h, h.h)
    row = abs(a.y - h.y) < 0.6 * dh and abs(a.x - h.x) < 0.45
    col = abs(a.x - h.x) < 1.5 * max(a.w, h.w) and 0.8 * dh < abs(a.y - h.y) < 4 * dh
    return row or col


def find(img: Image.Image, pairs: list[tuple[int, int]], ocr=tesseract_tsv, width: int = TARGET_W) -> Bug | None:
    """The bug showing one of `pairs` (recent (away, home) scores, newest first), or None. A small bug (a 720p
    broadcast's) only reads with width=1.5 × TARGET_W: the tracker alternates."""
    for y0, y1 in BANDS:
        ws = words(img, (0.0, y0, 1.0, y1), ocr, width=width)
        for a_s, h_s in pairs:
            best = min(((abs(a.x - h.x) + abs(a.y - h.y), a, h) for a in ws if a.n == a_s for h in ws if h.n == h_s and _paired(a, h)),
                       key=lambda t: t[0], default=None)
            if best:
                _, a, h = best
                return Bug(a, h, _grow(_cell(a, 0.5), _cell(h, 0.5)), width)
    return None


def _cell(w: Word, grow: float = 0.0) -> tuple[float, float, float, float]:
    """A score's cell: its digits and a little air (neighbours like BONUS / the clock confuse a one-line read).
    grow > 0 = room for another digit (the area that's grabbed)."""
    half = w.w * (0.62 + grow) + w.h * 0.08
    return (w.x - half, w.y - 0.62 * w.h, w.x + half, w.y + 0.62 * w.h)


def _grow(a, b) -> tuple[float, float, float, float]:
    return (max(0.0, min(a[0], b[0])), max(0.0, min(a[1], b[1])), min(1.0, max(a[2], b[2])), min(1.0, max(a[3], b[3])))


def read_cell(fr: _Frame, w: Word, ocr) -> int | None:
    """One score, digits only: the value two of the image versions agree on (as cropped, then on a margin — which helps
    some bugs and hurts others), trying the next version only while there's no agreement. Usually 2–3 OCR calls."""
    for grow in (0.0, 0.25):   # a score that just gained a digit (99 → 101) is wider than the one we found
        c = fr.crop(_cell(w, grow))
        if not c:
            return None
        votes: dict[int, int] = {}
        for border in (False, True):
            for v in variants(c[0]):
                if border:
                    light = sum(v.histogram()[128:]) * 2 > v.width * v.height
                    v = ImageOps.expand(v, border=max(6, v.height // 4), fill=255 if light else 0)
                got = [n for n, *_ in parse_tsv(ocr(v, psm=7, digits=True), min_conf=40)]
                if len(got) == 1:
                    votes[got[0]] = votes.get(got[0], 0) + 1
                    if votes[got[0]] >= 2:
                        return got[0]
    return None


def read(img: Image.Image, bug: Bug, ocr=tesseract_tsv, part=(0.0, 0.0, 1.0, 1.0)) -> tuple[int, int] | None:
    """(away, home) as the bug shows them now, or None (bug gone: a replay, an ad, a full-screen graphic). img = the
    frame, or only its `part` (e.g. bug.box, grabbed alone)."""
    fr = _Frame(img, part, bug.width)
    a = read_cell(fr, bug.away, ocr)
    h = read_cell(fr, bug.home, ocr) if a is not None else None
    return (a, h) if a is not None and h is not None else None


def load(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")
