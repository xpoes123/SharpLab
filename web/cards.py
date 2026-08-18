"""Read-only web API for the sports-card collection page (web/static/cards.*).
Buying/opening happens in Discord; this just serves browse views. Mounted under
/api/v1/cards (Caddy only proxies /api/* to uvicorn). See db/queries.py for storage."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import queries
from shared import cards as engine
from web import auth

router = APIRouter(prefix="/api/v1/cards")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/mine")
async def my_cards(request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"authenticated": False, "cards": [], "collection_value": 0}, status_code=401)
    cards_out, total = await queries.get_collection(sess["id"])
    return {"cards": cards_out, "collection_value": total}


@router.get("/sets")
async def sets(request: Request):
    return {"sets": await queries.list_card_sets()}


@router.get("/catalog")
async def catalog(request: Request, set_id: int = Query(...)):
    cat = await queries.get_catalog(set_id)
    if not cat:
        return JSONResponse({"error": "no such set"}, status_code=404)
    designs = cat["designs"]
    total = sum(d["total_copies"] for d in designs) or 1
    s = cat["set"]
    return {
        "set": {"name": s["name"], "sport": s["sport"], "season": s["season"]},
        "total_cards": total,
        "designs": designs,
        "odds": engine.set_odds(designs),
    }


class OpenBody(BaseModel):
    sport: str
    season: int
    n: int = 1


async def _reveal_payload(uid: str, cset: dict, cards: list[dict]) -> dict:
    """Sort ascending for the reveal, attach the set's pull-rate odds, and report the new
    balance so the page can update the wallet without a refetch."""
    cat = await queries.get_catalog(cset["set_id"])
    odds = engine.set_odds(cat["designs"]) if cat else {}
    balance = await queries.get_casino_balance(uid) or 0
    return {"cards": engine.reveal_order(cards), "odds": odds, "balance": balance}


@router.post("/open")
async def open_pack(request: Request, body: OpenBody):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "sign in to open packs"}, status_code=401)
    uid = sess["id"]
    n = max(1, min(10, body.n))
    cset = await queries.get_card_set(body.sport, body.season)
    if not cset:
        return JSONResponse({"error": "no such set"}, status_code=404)
    cards: list[dict] = []
    try:
        for _ in range(n):
            cards += await queries.mint_pack(uid, cset["set_id"], 5, "paid", _now_iso())
    except ValueError as e:
        if not cards:  # nothing opened — surface why (sold out / insufficient coins)
            return JSONResponse({"error": str(e)}, status_code=400)
    return await _reveal_payload(uid, cset, cards)


@router.get("/daily/status")
async def daily_status(request: Request):
    sess = auth.read_session(request)
    if not sess:
        return {"authenticated": False, "claimed": False}
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {"authenticated": True, "claimed": await queries.has_claimed_daily_pack(sess["id"], day)}


@router.post("/daily")
async def open_daily(request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "sign in to open packs"}, status_code=401)
    uid = sess["id"]
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if await queries.has_claimed_daily_pack(uid, day):
        return JSONResponse({"error": "already claimed today"}, status_code=409)
    sets = await queries.list_card_sets(include_closed=False)
    if not sets:
        return JSONResponse({"error": "no sets available"}, status_code=404)
    cset = max(sets, key=lambda s: (s["season"], s["set_id"]))  # newest season
    try:
        cards = await queries.mint_pack(uid, cset["set_id"], 5, "daily", _now_iso())
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    await queries.record_daily_pack_claim(uid, day)
    return await _reveal_payload(uid, cset, cards)
