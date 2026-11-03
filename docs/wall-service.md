# Wall service — one video-wall engine for the TV and for Discord watch parties

Status: phase 1 design + phase 2 build (branches `wall-service`, `wall-web`, `wall-bot` here; `wall-client` in xpoes123/tv).

## Why

The TV OS (xpoes123/tv) has a 1–4 tile video wall: Chromium windows tiled by KWin scripts, each tile's
video fullscreened over CDP, sound on one tile via pactl, a Qt layer-shell overlay (boards: fantasy / NFL / NBA)
on top. David wants the same wall for Discord watch parties: friends watch one shared wall, roughly in sync,
without anyone screen-sharing. So the engine moves out of the TV into this standalone service, which runs on
the homelab and can drive two walls at once:

| instance | where it draws | who drives it |
|---|---|---|
| `tv` | the real KWin session on the TV (`WAYLAND_DISPLAY=wayland-0`, the session D-Bus) | the TV OS (phone UX, `/settings/wall`) |
| `discord` | a nested headless `kwin_wayland --virtual` 1920×1080, captured to HLS | `/wall` in Discord + votes on the watch page |

## Pieces and hosts

```
 phone ── TV OS router (homelab :7790) ──loopback──┐
                                                   ▼
             sharplab-wall (homelab, user unit, :7795)  ── Chromium tiles, KWin scripts, CDP, pactl, qml6 overlay
                 ▲  control API (bearer)     │ HLS (bearer)            └─ discord: nested kwin + gst/NVENC + ffmpeg → HLS
                 │ Tailscale only            ▼
 Discord ── sharplab-bot (VPS) ──────► sharplab-web (VPS)  /wall.html, /api/v1/wall/hls/*, /ws/wall (chat, viewers, votes)
                                             ▲ SharpLab session cookie (Discord OAuth, guild-gated)
                                         friends' browsers (public, via the existing sharplab.djiang.xyz Caddy site)
```

* **sharplab-wall** (`wall/`, this repo) runs on the homelab `cachyos-homelab` as a **user** systemd unit
  (`deploy/sharplab-wall.service`), deployed by `git clone`/`git pull` of this repo into `~/SharpLab` and a
  `uv sync`. It listens on `127.0.0.1:7795` and the box's Tailscale address `100.67.232.59:7795`. Nothing on it
  is public.
* **sharplab-web** (VPS) owns everything viewer-facing: the watch page, the SharpLab-session gate, chat, the
  viewer list and voting. It fetches HLS from the wall service over Tailscale with the service token and serves it
  under `/api/v1/wall/hls/*` only to logged-in members. So the existing `sharplab.djiang.xyz` Caddy site already reaches
  it — no new public hostname, and the homelab is never exposed. (`deploy/caddy-wall.snippet` holds the optional
  Caddy change for review — not applied.)
* **sharplab-bot** (VPS) has `/wall`. It talks to the wall service over Tailscale (control API, bearer token)
  and to sharplab-web (viewers, votes, login links) with the existing `WEB_API_SECRET`.

## Auth

| caller → callee | how |
|---|---|
| TV router → wall service | loopback (127.0.0.1) is trusted |
| bot / web (VPS) → wall service | `Authorization: Bearer $WALL_API_TOKEN` (Tailscale address only) |
| browser → web `/wall.html`, `/api/v1/wall/hls/*`, `/ws/wall` | the SharpLab session cookie (`sharplab_session`, Discord OAuth, guild-gated — `web/auth.py`); no session → redirect to `/api/v1/auth/discord/login?next=/wall.html` |
| bot → web `/api/v1/wall/*` | `X-API-Key: $WEB_API_SECRET` (as other bot↔web calls) |

A SharpLab account *is* a Discord identity in the SharpLab guild (there is no separate signup). The bot's
**Get my link** button mints a 10-minute signed login link (`/api/v1/wall/login?t=…`, itsdangerous, own salt) for the
clicking member; opening it sets the normal session cookie, exactly as the OAuth callback would. Someone not in
the guild cannot click the button; on the web they hit the OAuth guild gate.

The control API (`/presets`, `/i/*`) is only on loopback + Tailscale and always needs the bearer from Tailscale.
HLS on the service needs the bearer too; only sharplab-web holds it.

## Wall service HTTP API (`wall/server.py`, port 7795)

All JSON. `{inst}` is `tv` or `discord`. Errors: `{"error": "…"}` with 4xx.

### Presets (shared by both instances; file `$WALL_STATE_DIR/wall.json`, same schema as the TV's)

* `GET /presets` → `[{name, urls[], overlay?, live?, tiles?}]`
* `PUT /presets` (whole list) → the saved list, or 422 with the validation message. First start imports the TV's
  `~/.local/state/tv/wall.json` when `WALL_IMPORT_FROM` points at it.
* `GET /live/{league}?n=4` → `[{id, label, state, url}]` best games on now (league `nfl`|`nba`).
* `GET /teams` → `{"stream": "https://topstreamer.site", "nfl": [...slugs], "nba": [...]}`

### Instances

* `GET /i` → `{tv: state, discord: state}`
* `GET /i/{inst}` → state:
  ```json
  {"inst": "discord", "running": true, "name": "Live NFL", "urls": ["…"], "sound": 0, "rev": 12,
   "preset": 3, "live": {"league": "nfl", "games": ["401…", null], "pinned": [0],
                          "labels": ["BAL 7–10 BUF · Q2", "…"]} | null,
   "overlay": {"board": "nfl", "visible": true, "layout": "lbar", "view": "lbar"},
   "started": 1762130000.0, "stream": {"hls": "/hls/discord/index.m3u8", "up": true, "last_fetch": 1762130100.0},
   "error": ""}
  ```
  `rev` increases on every change, so clients poll cheaply.
* `POST /i/{inst}/start` body one of `{"preset": 3}` / `{"preset": "Live NFL"}` / `{"urls": [...], "live": "nfl", "tiles": 4,
  "board": "nfl", "layout": "lbar"}` → state. A live preset plans its tiles from ESPN right away (no page checks; those
  run in the background every 120 s and swap finished games, as on the TV today).
* `POST /i/{inst}/stop` → state
* `POST /i/{inst}/tile` `{"i": 1, "url": "…"}` → swap tile i (i == len → add; ≤ 4). A live tile set this way leaves the auto-swap.
* `DELETE /i/{inst}/tile/{i}` → remove (never the last one)
* `POST /i/{inst}/move` `{"i": 0, "j": 2}`
* `POST /i/{inst}/sound` `{"i": 2}` → sound (and key focus) to tile i
* `POST /i/{inst}/pin` `{"i": 0}` → toggle pin of a live tile
* `POST /i/{inst}/board` `{"board": "nfl", "visible": true, "layout": "lbar"|"full"}` → the service-owned overlay
  (used by `discord`; its board data come from the service's own ESPN poller).
* `POST /i/{inst}/overlay` `{"view": "lbar"|"full"|"matchup"|"", "lbar": true}` → low level, used by `tv`, whose overlay
  state + board data stay in the TV router (fantasy is TV config): the service only runs the `qml6` process with
  `--api http://127.0.0.1:7790` and re-tiles for the L-bar.
* `POST /i/{inst}/keys` `{"codes": [...]}` → not on the service: the TV keeps its own uinput injector.
* Overlay data for service-owned overlays: `GET /i/{inst}/api/overlay`, `GET /i/{inst}/api/overlay/view` (the paths
  `Overlay.qml` polls under its `--api`).

### HLS (discord only)

* `GET /hls/discord/index.m3u8`, `GET /hls/discord/{seg}.ts` — bearer. 1 s segments (GOP = 30 frames @ 30 fps),
  `EXT-X-PROGRAM-DATE-TIME` on every segment, a 30-segment window.
* Every playlist fetch counts as a viewer heartbeat (only sharplab-web fetches, and only while someone watches).

### Budget / auto-stop

* **GPU:** `discord` refuses to start when free VRAM (`nvidia-smi`) is under `WALL_MIN_FREE_MB` (default 2500); the
  stream is 1080p30 NVENC (`preset=low-latency-hq`, ~4.5 Mb/s CBR), and the nested KWin renders with the GPU. The TV
  OS already unloads Ollama models while its own wall runs (`llm_guard`); the service does not touch Ollama.
* **Idle stop:** `discord` stops when no playlist fetch for `WALL_IDLE_S` (default 600 s) after the first viewer, or
  15 min after start with no viewer at all, and in any case after `WALL_MAX_HOURS` (default 6).
* **Priority:** the TV's wall wins: `discord` never touches the real session or its audio (its tiles play into a
  private null sink, `wall_discord`).

## Headless `discord` session (verified on the box 2026-11-02)

Everything is already installed; no new binaries.

1. `dbus-run-session -- kwin_wayland --virtual --width 1920 --height 1080 --socket wall-discord --no-lockscreen
   --no-global-shortcuts --exit-with-session=<hold script>` with `KWIN_WAYLAND_NO_PERMISSION_CHECKS=1`. It gets its
   own D-Bus (so its `org.kde.KWin` never collides with the TV's) and renders with GLES on the 2080 Ti
   (`supportInformation`: "Compositing Type: OpenGL ES, NVIDIA"). Output name `Virtual-0`. The KWin tiling scripts load
   through `qdbus6` on that private bus unchanged.
2. Tiles: the same Chromium launch with `--ozone-platform=wayland`, `WAYLAND_DISPLAY=wall-discord`, the private
   `DBUS_SESSION_BUS_ADDRESS`, `PULSE_SINK=wall_discord`, CDP ports 9241–9244 (tv keeps 9231–9234), profiles
   `chromium-wall-discord-<slot>`.
3. Overlay: the same `qml6 Overlay.qml` (layer-shell) on the nested display with `--api http://127.0.0.1:7795/i/discord`.
4. Capture: a screencast stream of `Virtual-0` is requested with KDE's own `ScreencastingRequest` QML type
   (`org.kde.taskmanager`, `wall/qml/Cast.qml`, `zkde_screencast_unstable_v1`); KWin creates a PipeWire node.
5. Encode: `gst-launch-1.0 pipewiresrc path=<node> ! videorate ! 30 fps ! videoconvert ! NV12 ! nvh264enc gop-size=30
   ! h264parse ! mpegtsmux` (+ audio from the `wall_discord.monitor` via `pipewiresrc` → `avenc_aac`) → stdout →
   `ffmpeg -c copy -f hls -hls_time 1 -hls_flags delete_segments+program_date_time+independent_segments`.
   ffmpeg has no PipeWire input and gst-plugins-good (mp4mux, pulsesrc) is not installed, which is why the split.
6. Audio focus = which tile is unmuted inside the private sink (same pactl logic, filtered to that instance's pids).

**Verified end to end on the box (2026-11-02, from a throwaway checkout, TV untouched — it stayed on Dreamscape):**
`POST /i/discord/start {"live":"nba","tiles":4,"board":"nba"}` planned OKC–CLE, ATL–IND, CHI–PHI, BKN–NY from ESPN,
opened their topstreamer pages in the nested session, tiled them into the L-bar corner with the NBA board + ticker drawn
by the layer-shell overlay, and the HLS segments came out as h264 1920×1080 + AAC 48 kHz stereo (max −8 dB: the tile with
the sound is audible), ~4.5 Mb/s, 1.000 s each. GPU cost of the whole headless wall with 4 live 1080p tiles: ~+550 MB
VRAM, ~13 % GPU. Stop left no process behind and unloaded the null sink. Over Tailscale: no token → 401, `/i/tv` → 403.

**PDT offset:** ffmpeg's `EXT-X-PROGRAM-DATE-TIME` runs a constant ~4 s ahead of the box clock (no drift — an offset
fixed at encoder start). So viewers are synced to the *live edge* the web server sees in the playlist, not to wall-clock
time minus a delay (see Sync below); that cancels the offset.

Found missing (not needed, so nothing to install): gst `pulsesrc`/`mp4mux` (gst-plugins-good), `wf-recorder`,
`grim`, `Xvfb`, `pywayland`.

## Watch page, sync, chat, viewers, votes (sharplab-web, `web/wall.py`)

* `GET /wall.html` — the player page (static; `/wall` needs the optional Caddy snippet) (hls.js vendored at `web/static/vendor/hls.min.js`, no CDN). Login required.
* `GET /api/v1/wall/hls/{file}` — session-gated proxy (under /api because the live Caddy only forwards /api/* and /ws/* to the app) to the service; a playlist is cached ~0.5 s and segments ~60 s in
  memory, so N viewers cost one upstream fetch.
* **Sync:** every viewer is pinned to the same instant of the stream: the web server reads the live edge from the
  playlist it proxies (last segment's PDT + its duration, observed at server time T) and target = edge + (server_now − T)
  − `WALL_SYNC_DELAY_S` (default 6 s), compared with the player's `EXT-X-PROGRAM-DATE-TIME` position
  (`hls.playingDate`). The server clock comes from the socket (`hello`/`pong` with server time; the client keeps the
  median offset). Error > 3 s → seek; otherwise playbackRate
  0.95–1.05 proportional to the error. hls.js `liveSyncDuration` is set to the same delay so the first load lands
  near the target. The page shows "in sync ±0.3 s".
* **Socket `/ws/wall`** (session cookie checked at the handshake, closed with 4401 otherwise): chat (≤ 300 chars,
  plain text rendered with `textContent`, 1 msg / 1.5 s and 8 / 30 s per user, last 200 kept), presence (heartbeat
  every 15 s with `hidden`; away = hidden or no heartbeat for 60 s), viewer list with names + avatars/initials, votes,
  moderation (owner + `WALL_ADMIN_IDS`: delete a message, mute a user for N minutes). Join/leave notices off by default.
* **Votes:** anyone proposes; screen changes (tile swap, layout, board, start, stop) pass on a strict majority of the
  *active* viewers (yes > active/2; ties fail), open 60 s, proposer counts yes. Moving the sound needs
  `WALL_SOUND_APPROVALS` (default 2) yes including the proposer, or 1 when only one viewer is active. The owner
  (`WALL_OWNER_ID`, David) applies instantly. One open proposal per user, 1 proposal / 20 s per user. Proposals are
  posted into chat. Discord `/wall` changes by non-owners become proposals on the same table.
* **Bot API:** `GET /api/v1/wall/state` (viewers, open proposals), `POST /api/v1/wall/propose`, `POST /api/v1/wall/vote`,
  `POST /api/v1/wall/link` (login link for a Discord member).
* Discord ↔ web chat mirroring: **not built** (it would need the bot to relay both ways and map identities; easy to add
  later with the same bot API).

## `/wall` in Discord (`bot/cogs/wall.py`)

`/wall start [preset]`, `/wall stop`, `/wall status`, `/wall set` (layout / board / sound / tile from a live game, a
team or a URL), and an interactive view (preset select, live-game select, team picker, layout + board + sound
buttons). One embed per channel, edited in place: what's on each tile, layout/board/sound, "Watching: David,
Steph, +3", open proposals, the link, and **Get my link** (ephemeral login link). Permission: `WALL_PERMISSION=anyone`
or `roles` with `WALL_ROLE_IDS`; the owner always. For non-owners, changes are proposals (votes from the page).

## The TV as a client (xpoes123/tv, branch `wall-client`)

`src/tv/wall.py` keeps the phone UX state machine (picker, editor pages, live view) but its `Driver` becomes
`RemoteDriver` (HTTP to `http://127.0.0.1:7795/i/tv/…`), its `PresetStore` reads and writes the service's `/presets`
(recent URLs stay local), and the live-game planning runs in the service (`tick` mirrors the service state).
`Overlay.qml`, `Board.qml`, `board.js`, `lbar.js` move to `wall/qml/`; the TV keeps a copy of `lbar.js`'s `TILES` for
the phone's tile map. `deploy/kwin-wall-rule.sh` moves here (it's a box setting the engine needs).

## Rollout (David)

1. Homelab: `git clone` this repo to `~/SharpLab`, `uv sync`, `.env` with `WALL_API_TOKEN`, install
   `deploy/sharplab-wall.service` as a user unit, start it.
2. Merge `wall-client` in tv, `git pull` `~/tv`, restart `tv.service` (while nothing is playing).
3. VPS: merge `wall-web` + `wall-bot`, set `WALL_SERVICE_URL=http://100.67.232.59:7795`, `WALL_API_TOKEN`,
   `WALL_OWNER_ID`, `WALL_ADMIN_IDS`, restart web + bot.
4. Review `deploy/caddy-wall.snippet` (optional; the default route already works through the existing site).
