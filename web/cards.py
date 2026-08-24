"""Web API for the sports-card page (web/static/cards.*): browse, open packs, quick-sell,
and the trade market (list cards, make coin-sweetened offers, accept/decline). Mounted
under /api/v1/cards. See db/queries.py for storage."""

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


class SellBody(BaseModel):
    instance_id: int


@router.post("/sell")
async def sell_card(request: Request, body: SellBody):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "sign in to sell cards"}, status_code=401)
    try:
        card, coins = await queries.sell_instance(sess["id"], body.instance_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    balance = await queries.get_casino_balance(sess["id"]) or 0
    return {"card": card, "coins": coins, "balance": balance}


@router.get("/coins")
async def coin_history(request: Request):
    """Balance + recent coin gains (the 'where did my coins come from' history)."""
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"authenticated": False, "balance": 0, "ledger": []}, status_code=401)
    return {
        "balance": await queries.get_casino_balance(sess["id"]) or 0,
        "ledger": await queries.get_coin_ledger(sess["id"], min_amount=50),  # hide the sub-50 trickle from the page
    }


@router.get("/collectors")
async def collectors(request: Request):
    """Public directory of everyone who owns cards, ranked by collection value."""
    return {"collectors": await queries.list_collectors()}


@router.get("/collection/{user_id}")
async def user_collection(request: Request, user_id: str):
    """Read-only view of anyone's collection (public)."""
    cards_out, total = await queries.get_collection(user_id)
    owner = await queries.get_public_user(user_id)
    return {"owner": owner, "cards": cards_out, "collection_value": total}


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


# ── Trade market ────────────────────────────────────────────────────────────


class ListBody(BaseModel):
    instance_id: int
    note: str | None = None


class UnlistBody(BaseModel):
    instance_id: int


class TradeBody(BaseModel):
    want_ids: list[int]           # the listed card(s) being offered on (one owner)
    offer_ids: list[int] = []     # the caller's cards offered (may be empty)
    coins: int = 0                # signed sweetener: >0 caller adds coins, <0 caller wants coins


@router.get("/market")
async def market(request: Request):
    """Public trade board: all cards listed as open to offers."""
    return {"listings": await queries.list_trade_market()}


@router.post("/list")
async def list_for_trade(request: Request, body: ListBody):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "sign in to list cards"}, status_code=401)
    try:
        await queries.create_trade_listing(body.instance_id, sess["id"], body.note, _now_iso())
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True}


@router.post("/unlist")
async def unlist(request: Request, body: UnlistBody):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "sign in"}, status_code=401)
    await queries.remove_trade_listing(body.instance_id, sess["id"])
    return {"ok": True}


@router.post("/trade")
async def make_offer(request: Request, body: TradeBody):
    """Offer on listed card(s). Resolves the target owner from the listing, validates the
    caller owns their offered cards, then creates a pending directed trade."""
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "sign in to make an offer"}, status_code=401)
    uid = sess["id"]
    if not body.want_ids:
        return JSONResponse({"error": "pick a card to offer on"}, status_code=400)
    # Every wanted card must currently be listed, and all owned by the same person (≠ you).
    market_by_id = {m["instance_id"]: m for m in await queries.list_trade_market()}
    owners = set()
    for iid in body.want_ids:
        m = market_by_id.get(iid)
        if not m:
            return JSONResponse({"error": "that card is no longer listed for trade"}, status_code=400)
        owners.add(m["owner_id"])
    if len(owners) != 1:
        return JSONResponse({"error": "all cards must come from the same collector"}, status_code=400)
    owner = owners.pop()
    if owner == uid:
        return JSONResponse({"error": "that's your own card"}, status_code=400)
    if body.offer_ids:
        owned = await queries.get_owned_instances(uid, body.offer_ids)
        if len(owned) != len(set(body.offer_ids)):
            return JSONResponse({"error": "you must own every card you offer"}, status_code=400)
    if not body.offer_ids and body.coins <= 0:
        return JSONResponse({"error": "offer at least a card or some coins"}, status_code=400)
    if body.coins > 0 and (await queries.get_casino_balance(uid) or 0) < body.coins:
        return JSONResponse({"error": "you don't have that many coins"}, status_code=400)
    tid = await queries.create_card_trade(
        uid, owner, body.offer_ids, body.want_ids, _now_iso(), coins=body.coins
    )
    return {"trade_id": tid}


async def _decorate_trades(trades: list[dict]) -> list[dict]:
    """Attach card previews to each trade's offer_ids/want_ids in one query."""
    ids = [i for t in trades for i in (t["offer_ids"] + t["want_ids"])]
    prev = await queries.get_instances_public(ids)
    out = []
    for t in trades:
        out.append({
            **t,
            "offer_cards": [prev[i] for i in t["offer_ids"] if i in prev],
            "want_cards": [prev[i] for i in t["want_ids"] if i in prev],
        })
    return out


@router.get("/trades")
async def my_trades(request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"incoming": [], "outgoing": [], "authenticated": False}, status_code=401)
    uid = sess["id"]
    incoming = await _decorate_trades(await queries.list_incoming_card_trades(uid))
    outgoing = await _decorate_trades(await queries.list_outgoing_card_trades(uid))
    return {"incoming": incoming, "outgoing": outgoing}


@router.post("/trades/{trade_id}/accept")
async def accept_offer(trade_id: int, request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "sign in"}, status_code=401)
    try:
        await queries.accept_card_trade(trade_id, sess["id"])
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    balance = await queries.get_casino_balance(sess["id"]) or 0
    return {"ok": True, "balance": balance}


@router.post("/trades/{trade_id}/decline")
async def decline_offer(trade_id: int, request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "sign in"}, status_code=401)
    t = await queries.get_card_trade(trade_id)
    if not t or t["to_user"] != sess["id"]:
        return JSONResponse({"error": "that offer isn't addressed to you"}, status_code=400)
    if t["status"] == "pending":
        await queries.set_card_trade_status(trade_id, "declined")
    return {"ok": True}


@router.post("/trades/{trade_id}/cancel")
async def cancel_offer(trade_id: int, request: Request):
    sess = auth.read_session(request)
    if not sess:
        return JSONResponse({"error": "sign in"}, status_code=401)
    t = await queries.get_card_trade(trade_id)
    if not t or t["from_user"] != sess["id"]:
        return JSONResponse({"error": "that isn't your offer"}, status_code=400)
    if t["status"] == "pending":
        await queries.set_card_trade_status(trade_id, "cancelled")
    return {"ok": True}
