"""One-off: trim ~/code/pokemon-cards/data.json into data/pokemon_cards.json.

Keeps {id, name, cards:[{name, rarity, price_usd, img}]} — max price across variants;
`img` is the source-relative path (e.g. "img/en_sv_sv03.5_001.webp"), served in prod at
https://pokemon.djiang.xyz/<img>. Run locally (has the source project), commit the output:
    python scripts/extract_pokemon_data.py
"""
import json
import os

SRC = os.path.expanduser("~/code/pokemon-cards/data.json")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "pokemon_cards.json")


def _max_price(card: dict) -> float:
    prices = card.get("prices") or {}
    return max(prices.values()) if prices else 0.0


def main() -> None:
    d = json.load(open(SRC))
    out = []
    for s in d["sets"]:
        out.append({
            "id": s["id"], "name": s["name"],
            "cards": [{"name": c["name"], "rarity": c.get("rarity", "Common"),
                       "price_usd": round(_max_price(c), 2), "img": c.get("img", "")}
                      for c in s.get("cards", [])],
        })
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"sets": out}, open(OUT, "w"), ensure_ascii=False)
    print(f"wrote {OUT}: {len(out)} sets, {sum(len(s['cards']) for s in out)} cards")


if __name__ == "__main__":
    main()
