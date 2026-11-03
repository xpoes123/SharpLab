import asyncio
import os
import tempfile

from db import queries as q
from db import schema as sch


def test_request_lifecycle():
    sch.DB_PATH = q.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")

    async def go():
        await sch.init_db()
        a = await q.add_feature_request("u1", "show my daily streak in /hq")
        b = await q.add_feature_request("u2", "blackjack side bets")
        await q.set_feature_request_message(a, "10", "20")
        assert (await q.get_feature_request(a))["message_id"] == "20"
        assert [r["id"] for r in await q.list_feature_requests()] == [a, b]
        assert await q.mark_feature_request_done(a, "live now")
        assert not await q.mark_feature_request_done(a, "again")          # already done
        assert not await q.mark_feature_request_done(999, None)           # missing
        assert [r["id"] for r in await q.list_feature_requests()] == [b]
        assert (await q.list_feature_requests("done"))[0]["note"] == "live now"

    asyncio.run(go())
