"""Chess.com RSS parsing for the chess-news cog: fields, guid fallback, dates."""
from __future__ import annotations

from datetime import timezone

from bot.cogs.chessnews import _parse_rss, _parse_pubdate

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Chess.com News</title>
  <item>
    <title>Carlsen Loses To Praggnanandhaa At Norway Chess</title>
    <description>A shocking classical result in round eight.</description>
    <link>https://www.chess.com/news/view/2026-norway-chess-round-8</link>
    <pubDate>Tue, 02 Jun 2026 12:54:00 -0700</pubDate>
    <guid>https://www.chess.com/news/view/2026-norway-chess-round-8</guid>
  </item>
  <item>
    <title>No Guid Item</title>
    <description>desc</description>
    <link>https://www.chess.com/news/view/no-guid</link>
    <pubDate>Tue, 02 Jun 2026 08:00:00 -0700</pubDate>
  </item>
</channel></rss>"""


def test_parse_rss_fields():
    arts = _parse_rss(SAMPLE)
    assert len(arts) == 2
    a = arts[0]
    assert a["id"] == "https://www.chess.com/news/view/2026-norway-chess-round-8"
    assert a["headline"].startswith("Carlsen Loses")
    assert a["url"].endswith("/2026-norway-chess-round-8")
    assert a["description"]


def test_guid_falls_back_to_link():
    arts = _parse_rss(SAMPLE)
    # second item has no <guid> → id falls back to <link>
    assert arts[1]["id"] == "https://www.chess.com/news/view/no-guid"


def test_parse_pubdate_is_tz_aware_utc_convertible():
    dt = _parse_pubdate("Tue, 02 Jun 2026 12:54:00 -0700")
    assert dt is not None and dt.tzinfo is not None
    assert dt.astimezone(timezone.utc).hour == 19  # 12:54 PT == 19:54 UTC


def test_parse_garbage_is_safe():
    assert _parse_rss("not xml at all") == []
    assert _parse_pubdate("") is None
