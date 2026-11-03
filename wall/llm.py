"""The box's local LLM (Ollama, :11434: Sage's gemma/qwen) steps out of the GPU's way while any wall runs.

Same mechanism as the TV OS's LLMGuard (xpoes123/tv src/tv/video.py), which covers the TV's own apps: unload what's
loaded (keep_alive 0). Nothing is warmed back when the walls stop: the next real request loads the model (and Ollama's
own keep-alive unloads it again), so an idle GPU never holds a model on our account. It runs
every tick, so a model a caller reloads mid-wall (Sage's local_chat keeps working, it just reloads) is evicted again
within one tick.

mode "stop" goes further: `systemctl stop ollama` while a wall runs and start it after — Sage's local_chat then fails
fast and takes its cloud path. That needs the polkit rule in deploy/ollama-wall.polkit.rules (not installed by default).
"""
from __future__ import annotations

import json
import logging
import subprocess
import urllib.request

log = logging.getLogger("wall.llm")


def _call(base: str, path: str, body: dict | None = None, timeout: float = 120) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _systemctl(verb: str) -> None:
    subprocess.run(["systemctl", verb, "ollama.service"], capture_output=True, timeout=60, check=True)


class LLMGuard:
    def __init__(self, base: str = "http://127.0.0.1:11434", mode: str = "unload", call=_call, systemctl=_systemctl) -> None:
        self.base, self.mode, self.call, self.systemctl = base.rstrip("/"), mode, call, systemctl
        self.parked: list[str] = []
        self.stopped = False

    def park(self) -> None:
        """Free the GPU now (before a wall starts, so its VRAM check sees what the LLM held)."""
        if self.mode == "off":
            return
        try:
            for m in self.call(self.base, "/api/ps", timeout=5).get("models", []):
                self.call(self.base, "/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=30)
                if m["name"] not in self.parked:
                    self.parked.append(m["name"])
                log.info("unloaded %s for the wall", m["name"])
        except Exception as e:  # noqa: BLE001 — Ollama down / busy: next tick
            log.warning("llm guard: %s", e)
        if self.mode == "stop" and not self.stopped:
            try:
                self.systemctl("stop")
                self.stopped = True
                log.info("stopped ollama for the wall")
            except Exception as e:  # noqa: BLE001
                log.warning("llm guard: can't stop ollama: %s", e)

    def tick(self, walls_up: bool) -> None:
        if self.mode == "off":
            return
        if walls_up:
            self.park()
            return
        if self.stopped:
            try:
                self.systemctl("start")
                self.stopped = False
                log.info("started ollama again")
            except Exception as e:  # noqa: BLE001
                log.warning("llm guard: can't start ollama: %s", e)
                return
        self.parked.clear()   # no warm-back: the next real request loads it — no idle GPU hold
