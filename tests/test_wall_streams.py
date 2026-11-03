"""wall/streams.py: a YouTube watch page says live now / scheduled (with its start) / neither."""
from wall.streams import is_stream, parse


def test_parse_and_is_stream():
    assert parse(b'..."isLiveNow":true...') == {"live": True, "starts": None}
    assert parse(b'"isUpcoming":true,"scheduledStartTime":"1793761200"') == {"live": False, "starts": 1793761200}
    assert parse(b'"isLiveNow":false') == {"live": False, "starts": None}
    assert is_stream("https://www.youtube.com/@WCVB/live") and is_stream("https://www.youtube.com/watch?v=QRK30J9ouaU")
    assert not is_stream("http://127.0.0.1:7795/p/election/races")
