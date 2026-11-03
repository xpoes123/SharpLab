"""wall/director.py: the night's summary for Jev; its probabilities order the rotation (rules still apply); stale → cycle."""
from wall import director, rotate

B = "http://127.0.0.1:7795/p/election/"


def test_summary_and_ranked_rotation():
    now = 1_800_000_000.0
    snap = {"races": [{"id": "S-GA", "label": "Georgia", "state": "GA", "office": "S", "votes": 900, "pct": 40, "called": "", "watch": True,
                       "margin": 1.2, "leader": "D", "closes": now - 600},
                      {"id": "G-PA", "label": "Pennsylvania", "state": "PA", "office": "G", "votes": 0, "pct": 0, "called": "", "watch": True,
                       "margin": None, "leader": "", "closes": now + 1800}],
            "calls": [{"id": "S-FL", "t": now - 60, "text": "FL SEN · Scott (R) wins"}]}
    s = director.summary(snap, {"states": {}}, {"questions": []}, {}, now)
    assert "Polls closed in the last 20 minutes: GA" in s and "Scott (R) wins" in s and "Georgia Senate D+1.2" in s and "not released" in s
    calls = []

    def http(m, p, b):
        calls.append(p)
        return 200, {"probabilities": {"races": 0.6, "map?view=S": 0.3, "turnout": 0.1}}
    d = director.Director(http, clock=lambda: now)
    assert d.due()
    d.refresh(s)
    assert calls == ["/api/v1/wall/election/direct"] and rotate.RANK["order"][:2] == ["races", "map?view=S"]
    assert rotate.next_url([B + "turnout", B + "map?view=H"], 0, now) == B + "races"            # Jev's first pick
    assert rotate.next_url([B + "races", B + "map?view=H"], 1, now) == B + "map?view=S"         # its next, kinds kept apart
    assert rotate.next_url([B + "turnout", B + "map?view=H"], 0, now + 999) == B + "exitpoll"   # stale → the plain cycle
    rotate.set_rank([], 0.0)
