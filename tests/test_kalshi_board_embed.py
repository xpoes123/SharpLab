import asyncio
from types import SimpleNamespace

from bot.cogs import markets as mk


def test_board_embed_ranks_and_calls_out_best_worst(monkeypatch):
    settles = {
        "1": [{"ticker": "W", "settled_time": "2026-11-01T00:00:00Z", "revenue": 1000,
               "yes_count_fp": "10", "yes_total_cost_dollars": "4"}],
        "2": [{"ticker": "L", "settled_time": "2026-11-01T00:00:00Z", "no_count_fp": "10",
               "no_total_cost_dollars": "5"}],
    }

    async def users():
        return ["1", "2"]

    async def acct(uid):
        return ("kid", b"enc")

    async def all_(c, kid, key, path, field):
        return settles[current.pop(0)]

    async def titles(tickers):
        return {"W": "Lakers win", "L": "Over 7.5"}

    current = ["1", "2"]
    monkeypatch.setattr(mk.queries, "list_kalshi_account_users", users)
    monkeypatch.setattr(mk.queries, "get_kalshi_account", acct)
    monkeypatch.setattr(mk.ka, "decrypt", lambda b: "pem")
    monkeypatch.setattr(mk.ka, "load_key", lambda p: None)
    monkeypatch.setattr(mk.ka, "_all", all_)
    monkeypatch.setattr(mk.ka, "market_titles", titles)
    bot = SimpleNamespace(get_user=lambda i: SimpleNamespace(display_name={1: "harsha", 2: "aitik"}[i]))
    e = asyncio.run(mk._board_embed(bot, 0, None, "🏆 test"))
    assert e.description.splitlines()[0].startswith("🥇 **harsha** **+6.00** · 1-0 · +150% ROI")
    assert "🥈 **aitik** **-5.00** · 0-1" in e.description
    fields = {f.name: f.value for f in e.fields}
    assert "Lakers win" in fields["🎯 Biggest hit"] and "Over 7.5" in fields["💀 Worst beat"]
    assert "/kalshi link" in e.footer.text
