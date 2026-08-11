"""Read-only web API for the sports-card collection page (web/static/cards.*).
Buying/opening happens in Discord; this just serves browse views. Mounted under
/api/v1/cards (Caddy only proxies /api/* to uvicorn). See db/queries.py for storage."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from db import queries
from shared import cards as engine
from web import auth

router = APIRouter(prefix="/api/v1/cards")


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
    by_rarity: dict[str, int] = {}
    for d in designs:
        by_rarity[d["rarity"]] = by_rarity.get(d["rarity"], 0) + d["total_copies"]
    s = cat["set"]
    return {
        "set": {"name": s["name"], "sport": s["sport"], "season": s["season"]},
        "total_cards": total,
        "designs": designs,
        "odds": {
            "holo_pct": round(engine.HOLO_RATE * 100, 1),
            "pull_rates": {r: round(100 * by_rarity.get(r, 0) / total, 1) for r in engine.RARITIES if by_rarity.get(r)},
            "gems": {name: {"one_in": den, "mult": mult} for name, (den, mult, _f) in engine.GEMS.items()},
        },
    }
