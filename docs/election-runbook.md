# Election-night runbook

The wall went live for the 2026 midterms. Use this to bring it back, including for 2028.

## Normal state (no election)

`wall/modes.py` decides what the plain link can watch, so `WALL_GUEST_OPEN=1` can stay set. A public viewer gets the stream only
on an election wall whose tiles are all our own pages or YouTube. Sports walls always need a login or the friend key.

## Settings for the night

| Where | Setting | Night value | Normal value |
|---|---|---|---|
| Homelab `.env.wall` | `WALL_MAX_HOURS` | 16 | 6 (code default) |
| Homelab `.env.wall` | `WALL_IDLE_S`, `WALL_FIRST_VIEWER_S` | 43200 | see `.env.wall.bak-pre-election` |
| Homelab `.env.wall` | `WALL_FPS`, `WALL_KBPS` | 30, 3500 | the same: the home uplink is the limit |
| TV `/settings/ambient` | Screensaver `idle_min` | 0 | 10 |
| VPS `.env` | `WALL_NO_VOTES` | 1 | the owner's call |
| VPS `.env` | `WALL_NEUTRAL_UNTIL` | end of neutral hours (epoch) | any past time |

No restarts while people are watching. Fix things at runtime and hold deploys.

## Update for the next cycle

These are written for 2026:

- `wall/election.py`: CNN URLs and `OFFICES`
- `wall/election_counties.py`: `NYT_BASE` (the election date)
- `wall/exitpoll.py`: the NBC exit poll URL
- `wall/turnout_sources.py`: state turnout URLs
- `wall/swing.py`, `wall/baseline2024.json`: the comparison baseline
- `shared/kalshi_election.py`: Kalshi series names (re-check them live)
- `wall/presets.py`: `ELECTION_SEED`, including PBS's fixed video id; `wall/spectrum.py`: the stream lineup

## Not built yet for a presidential year

- **President as an office.** `OFFICES` only has Senate, House and Governor. It needs electoral votes and the 270 line on the map,
  the board, `wall/events.py` and `web/wall_ask.py`.
- **Director choices** (`wall/director.py` `OPTIONS`) and **Jev's dashboards** (`web/wall_jev.py`) need the new map.

## Dry run

`WALL_ELECTION_REPLAY=tests/fixtures/wall/election:sim=900` replays 2024's night in 15 minutes, poll closings included.
