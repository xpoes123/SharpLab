"""wall/turnout.py: each new E-day report becomes a batch; corrections don't; margins; history survives a restart."""
from wall.turnout import Turnout, margin


def test_batches_margins_and_restart(tmp_path):
    t = [100.0]
    tn = Turnout(sources={}, state_dir=tmp_path, clock=lambda: t[0])
    tn.take("FL", "Florida DOS", "u", {"eday": {"N": 400}, "advance": {"D": 60, "R": 30, "O": 10}})   # baseline: no batch
    tn.take("FL", "Florida DOS", "u", {"eday": {"N": 1000}})
    t[0] = 200.0
    tn.take("FL", "Florida DOS", "u", {"eday": {"N": 1500}, "as_of": "1:40 PM ET"})
    tn.take("FL", "Florida DOS", "u", {"eday": {"N": 1400}})          # a correction: no batch
    s = tn.snapshot("FL")["state"]
    assert [b["N"] for b in s["batches"]] == [500, 600] and s["eday"] == {"D": 0, "R": 0, "O": 0, "N": 1400}
    assert margin({"D": 60, "R": 30, "O": 10}) == 0.3 and margin({"N": 5}) is None
    tn._save()
    assert len(Turnout(sources={}, state_dir=tmp_path).snapshot("FL")["state"]["batches"]) == 2
