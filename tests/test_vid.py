import shutil
import subprocess
import time

import httpx

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


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(vid, "MEDIA_DIR", str(tmp_path / "media"))
    app = FastAPI(); app.include_router(vid.router)
    return TestClient(app), {auth.COOKIE_NAME: auth.make_session_cookie({"id": "9", "username": "Mallory"})}


def _wait(c, jid, me):
    for _ in range(120):
        s = c.get(f"/api/v1/vid/{jid}", cookies=me).json()
        if s["state"] in ("done", "error"):
            return s
        time.sleep(0.5)


def test_playlists_cant_pull_in_other_server_files(tmp_path, monkeypatch):
    # an HLS playlist whose segment is a file elsewhere on the server: if ffmpeg followed it, its contents would be
    # encoded into a video the uploader can download
    secret = tmp_path / "secret.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=2",
                    "-c:v", "libx264", str(secret)], check=True)
    c, me = _client(tmp_path, monkeypatch)
    for body in (f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\n{secret}\n#EXT-X-ENDLIST\n" + "#" * 1000,
                 f"ffconcat version 1.0\nfile '{secret}'\n" + "#" * 1000):
        jid = c.post("/api/v1/vid", content=body.encode(), cookies=me).json()["id"]
        s = _wait(c, jid, me)
        assert s["state"] == "error", s


def test_upload_cap_counts_in_flight_uploads(tmp_path, monkeypatch):
    c, me = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(vid, "_jobs", {k: {"uid": "9", "state": "uploading", "t": time.time()} for k in "ab"})
    assert c.post("/api/v1/vid", content=b"x" * 5000, cookies=me).status_code == 429


def test_low_disk_rejects_upload(tmp_path, monkeypatch):
    c, me = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(vid, "MIN_FREE", 10**18)
    r = c.post("/api/v1/vid", content=b"x" * 5000, cookies=me)
    assert r.status_code == 413 and "space" in r.json()["error"] and not vid._jobs.get(r.json().get("id", ""))


def test_send_is_rate_limited(tmp_path, monkeypatch):
    c, me = _client(tmp_path, monkeypatch)
    out = tmp_path / "v.mp4"; out.write_bytes(b"x")
    monkeypatch.setattr(vid, "_jobs", {"j": {"uid": "9", "user": "Mallory", "state": "done", "t": time.time(),
                                             "sends": 0, "name": "v", "out": str(out)}})
    monkeypatch.setattr(vid, "_last_send", {})

    async def chans():
        return [{"id": "5", "guild": "7"}]
    monkeypatch.setattr(vid, "_channels", chans)
    posts = []

    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kw):
            posts.append(url)
            return httpx.Response(200, json={"id": "m"})
    monkeypatch.setattr(vid.httpx, "AsyncClient", FakeClient)
    send = lambda ch="5": c.post("/api/v1/vid/j/send", json={"channel": ch}, cookies=me)
    assert send("666").status_code == 400                 # only listed channels
    assert send().json()["link"].endswith("/7/5/m")
    assert send().status_code == 429                      # cooldown
    for _ in range(2):
        vid._last_send.clear(); assert send().status_code == 200
    vid._last_send.clear(); assert send().status_code == 429   # per-video cap
    assert len(posts) == 3
