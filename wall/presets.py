"""Wall presets (name + 1–4 URLs + an optional board, or a live league), shared by every wall instance.

Same schema as the TV's old TV_STATE_DIR/wall.json, so the file moves over as is: on first start the store imports
WALL_IMPORT_FROM (the TV's wall.json) when there's no wall.json of its own yet.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator, model_validator

from wall.live import LEAGUES

SEED = [{"name": "Sample", "urls": ["https://www.youtube.com/watch?v=aqz-KE-bpKQ", "https://www.youtube.com/watch?v=4xDzrJKXOOY",
                                     "https://www.twitch.tv/lofigirl", "https://commons.wikimedia.org/wiki/File:Big_Buck_Bunny_4K.webm"]}]
LIVE_SEED = [{"name": f"Live {LEAGUES[lg]}", "urls": [], "overlay": lg, "live": lg} for lg in LEAGUES]


def check_url(u: str) -> str:
    """These become chromium --app= arguments: never a flag, never a file."""
    if not (isinstance(u, str) and u.startswith(("http://", "https://")) and len(u) <= 500 and not any(c.isspace() for c in u)):
        raise ValueError(f"not an http(s) URL: {str(u)[:60]!r}")
    return u


class Preset(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    urls: list[str] = Field(max_length=4)   # a live preset's are its fallback when no game is on (may be empty)
    overlay: str = Field(default="", pattern=r"^[a-z]{0,16}$")   # a board id that opens with this wall; "" = none
    live: Literal["", "nfl", "nba"] = ""
    tiles: int = Field(default=4, ge=1, le=4)   # how many live games, at most

    @model_validator(mode="after")
    def _some_tile(self):
        if not self.urls and not self.live:
            raise ValueError("needs 1–4 URLs (or live games)")
        return self

    @field_validator("urls")
    @classmethod
    def _http(cls, urls: list[str]) -> list[str]:
        for u in urls:
            check_url(u)
        return urls


PRESETS = TypeAdapter(list[Preset])


def clean_url(text: str) -> str:
    """Typed text → a tile URL, or ValueError. A bare host ("topstreamer.site/nfl/bills") gets https://."""
    u = text.strip()
    if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(/|$)", u, re.I):
        u = "https://" + u
    try:
        return check_url(u)
    except ValueError:
        raise ValueError("Not a web address: needs http:// or https://") from None


def errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())[:400]


class PresetStore:
    def __init__(self, state_dir: str, import_from: str = "") -> None:
        self.path = Path(state_dir) / "wall.json" if state_dir else None
        src = self.path if self.path and self.path.exists() else Path(import_from) if import_from and Path(import_from).exists() else None
        try:
            self.presets: list[dict] = json.loads(src.read_text()) if src else [*SEED, *LIVE_SEED]
        except ValueError:
            self.presets = [*SEED, *LIVE_SEED]
        if src is not None and src != self.path and self.path:
            _write(self.path, self.presets)

    def put(self, data: object) -> list[dict]:
        """Replace the whole list. Raises ValidationError."""
        self.presets = [p.model_dump(exclude_defaults=True) for p in PRESETS.validate_python(data)][:50]
        if self.path:
            _write(self.path, self.presets)
        return self.presets

    def find(self, key: int | str) -> dict | None:
        if isinstance(key, int) and not isinstance(key, bool):
            return self.presets[key] if 0 <= key < len(self.presets) else None
        return next((p for p in self.presets if p["name"].lower() == str(key).lower()), None)


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)
