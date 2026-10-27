import shutil
import subprocess
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web import auth, vid

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def test_plan_fits_long_clips_into_the_budget():
    p = vid.plan({"w": 1920, "h": 1080, "fps": 60, "dur": 120, "audio": True}, 5)
    assert p["short"] <= 480 and (p["vk"] + p["ak"]) * 120 / 8 / 1000 <= 5
    portrait = vid.plan({"w": 1080, "h": 1920, "fps": 30, "dur": 10, "audio": False}, 5)
    assert portrait["w"] == 720 and portrait["h"] == -2 and portrait["ak"] == 0


def test_upload_shrinks_under_5mb_and_only_the_owner_sees_it(tmp_path, monkeypatch):
    src = tmp_path / "clip.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=20",
                    "-f", "lavfi", "-i", "sine=duration=20", "-c:v", "libx264", "-b:v", "20M", "-c:a", "aac", str(src)], check=True)
    assert src.stat().st_size > 5_000_000
    monkeypatch.setattr(vid, "MEDIA_DIR", str(tmp_path / "media"))
    app = FastAPI(); app.include_router(vid.router)
    c = TestClient(app)
    me = {auth.COOKIE_NAME: auth.make_session_cookie({"id": "1", "username": "David"})}
    assert c.post("/api/v1/vid", content=b"x" * 5000).status_code == 401
    r = c.post("/api/v1/vid", content=src.read_bytes(), headers={"X-Filename": "clip.mov"}, cookies=me)
    jid = r.json()["id"]
    for _ in range(240):
        s = c.get(f"/api/v1/vid/{jid}", cookies=me).json()
        if s["state"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert s["state"] == "done", s
    assert s["mb"] <= 5 and s["url"].endswith(f"{jid}.mp4")
    f = c.get(f"/api/v1/vid/{jid}/file", cookies=me)
    assert f.status_code == 200 and len(f.content) <= 5_000_000 and "clip.mp4" in f.headers["content-disposition"]
    other = {auth.COOKIE_NAME: auth.make_session_cookie({"id": "2", "username": "Gary"})}
    assert c.get(f"/api/v1/vid/{jid}", cookies=other).status_code == 404
    assert not list((tmp_path / "media").glob(".*.src"))   # the upload is dropped after encoding
