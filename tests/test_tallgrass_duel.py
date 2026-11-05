"""Tall Grass duels: blind armies stay hidden, budgets are enforced by the real engine, both browsers get one war."""
import asyncio
import shutil

import pytest

import web.g_pokehoops as gp
import web.g_tallgrass as gt

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


class _Req:
    cookies: dict = {}
    headers: dict = {}
    client = None


def _run(coro):
    return asyncio.run(coro)


def _army(side, num=19, count=20, level=19, **extra):
    return [{"num": num, "count": count, "level": level, "x": 30 if side == 0 else 90, "y": 36, **extra}]


async def _pair(budget=50000):
    gt._rooms.clear()
    gp._hits.clear()    # the per-IP room limiter would otherwise trip across tests
    host = await gt.duel_create(_Req(), gt.DuelBody(name="Ash", budget=budget, biome="meadow"))
    guest = await gt.duel_join(_Req(), host["code"], gt.DuelBody(name="Gary"))
    return host["code"], host["key"], guest["key"]


def test_armies_and_seed_stay_hidden_until_both_lock():
    async def go():
        code, ka, kb = await _pair()
        a = await gt.duel_lock(code, gt.DuelBody(squads=_army(0)), ka)
        assert a["phase"] == "build" and a["army"][0]["num"] == 19
        b = await gt.duel_state(code, kb)
        assert b["opp_locked"] is True
        # nothing about the locked army reaches the other seat
        assert b["army"] is None and b["armies"] is None and b["seed"] is None and b["result"] is None
        assert "19" not in str({k: v for k, v in b.items() if k not in ("code", "budget", "round", "seat")})
        done = await gt.duel_lock(code, gt.DuelBody(squads=_army(1, num=25, level=25)), kb)
        assert done["phase"] == "war" and done["seed"] is not None
        seen_a, seen_b = await gt.duel_state(code, ka), await gt.duel_state(code, kb)
        assert seen_a["armies"] == seen_b["armies"] and seen_a["seed"] == seen_b["seed"] and seen_a["result"] == seen_b["result"]
        assert sum(seen_a["wins"]) == (0 if seen_a["result"]["winner"] is None else 1)
    _run(go())


@pytest.mark.parametrize("army,msg", [
    (_army(0, count=1000), "over your"),                      # too expensive
    (_army(0, level=20), "must be level"),                    # Rattata evolves at 20
    (_army(0, num=149, level=20), "must be level"),           # no low-level Dragonite
    (_army(0, moves=["surf"]), "can't know"),                 # illegal move
    (_army(0, x=100), "outside your side"),                   # deployed in the enemy zone
    (_army(0, num=9999), "Unknown"),
    (_army(0, count=-5), "Bad count"),
])
def test_bad_armies_are_rejected_by_the_engine(army, msg):
    async def go():
        code, ka, _ = await _pair()
        res = await gt.duel_lock(code, gt.DuelBody(squads=army), ka)
        assert res.status_code == 400 and msg in res.body.decode()
    _run(go())


def test_validated_army_drops_unknown_fields():
    async def go():
        code, ka, _ = await _pair()
        res = await gt.duel_lock(code, gt.DuelBody(squads=[{**_army(0)[0], "ability": "hugepower", "side": 1, "id": 999, "hacked": True}]), ka)
        squad = res["army"][0]
        assert squad["side"] == 0 and "ability" not in squad and "hacked" not in squad and squad["id"] == 1
    _run(go())


def test_unlock_lets_you_change_your_mind_until_the_war_starts():
    async def go():
        code, ka, kb = await _pair()
        await gt.duel_lock(code, gt.DuelBody(squads=_army(0)), ka)
        res = await gt.duel_unlock(code, ka)
        assert res["army"] is None
        assert (await gt.duel_state(code, kb))["opp_locked"] is False
        await gt.duel_lock(code, gt.DuelBody(squads=_army(0)), ka)
        await gt.duel_lock(code, gt.DuelBody(squads=_army(1)), kb)
        assert (await gt.duel_unlock(code, ka)).status_code == 409
    _run(go())


def test_rematch_needs_both_and_reveals_only_last_rounds_armies():
    async def go():
        code, ka, kb = await _pair()
        await gt.duel_lock(code, gt.DuelBody(squads=_army(0)), ka)
        await gt.duel_lock(code, gt.DuelBody(squads=_army(1, num=25, level=25)), kb)
        first = await gt.duel_rematch(code, ka)
        assert first["phase"] == "war" and first["rematch"] == [True, False]
        second = await gt.duel_rematch(code, kb)
        assert second["phase"] == "build" and second["round"] == 2 and second["army"] is None and second["seed"] is None
        assert second["last"][1][0]["num"] == 25
    _run(go())


def test_room_access():
    async def go():
        code, ka, _ = await _pair()
        assert (await gt.duel_state(code, "wrong")).status_code == 403
        assert (await gt.duel_join(_Req(), code, gt.DuelBody(name="Misty"))).status_code == 409
        assert (await gt.duel_create(_Req(), gt.DuelBody(name="A", budget=123, biome="meadow"))).status_code == 400
        assert (await gt.duel_create(_Req(), gt.DuelBody(budget=50000, biome="meadow"))).status_code == 400
        left = await gt.duel_leave(code, ka)
        assert left["phase"] == "left" and left["left"] == 0
    _run(go())
