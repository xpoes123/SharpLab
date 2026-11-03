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
  `{"mirror": true}` = stream the TV's wall and follow it (see Mirror mode); `"origin": {"guild", "channel"}` on any start
  is kept in the state so the bot posts the link where the start came from.
* `POST /i/{inst}/follow` → mirror the TV again (after a change on this wall stopped the following)
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

## Mirror mode (the discord wall streams the TV's wall)

A bare `/wall start` (the web page's "Same as the TV", the panel's first choice) sends `{"mirror": true}`; any start
while the TV is on its wall mirrors too. The service then:

* **Captures the TV's real output** (`WALL_TV_OUTPUT`, default `HDMI-A-1`, 2560×1440 on the box): `Cast.qml` asks the
  user's KWin for a zkde_screencast of that output (no portal, unattended — verified on the box), and the same
  gst → ffmpeg path encodes it, scaled to 1080p on the GPU (`cudaupload ! cudaconvertscale ! nvh264enc`), with the TV's
  default sink's monitor as the sound (`WALL_TV_SINK`). No tiles of its own: the tiles, overlay and sound are exactly
  what the TV shows, so every TV change is on the stream by itself. State: `"mirror": true, "capture": "screen"`.
* **Never streams another TV app:** the TV router's `/healthz` is checked every 3 s; when the TV leaves its wall the
  capture ends — while someone watches (a playlist fetch in the last 90 s) the stream falls back to tiles of its own
  with the last URLs, else the wall stops.
* **Refused pages:** if a TV tile may not be streamed (`public_url`: LAN / tailnet / loopback), the screen can't be,
  so the mirror runs its own tiles without that one and posts a notice; when it's gone, back to the screen.
* **Tiles mode** (no capture output set, a refused page, the TV left its wall) follows the TV's `rev`: swaps, adds,
  removes, moves (moved, not reloaded), sound and overlay are copied on the next tick. The TV stopping leaves the last
  tiles up.
* **Breaking / restoring:** any change made on the discord wall itself (a vote that passed, the owner) stops the
  following (`"mirror": false`, a notice "No longer mirroring the TV"); a screen mirror turns into tiles first.
  `POST /i/discord/follow` (the "Follow the TV" button / `{"op": "follow"}` proposal) restores it.
* Switching between screen and tiles keeps the session (`started`, `origin`), so the bot doesn't post the link again.

## Box safety

* **No crash storms.** The nested session's private bus (`wall/nested-bus.conf`) has no service directories, so its
  clients can't D-Bus-activate xdg-desktop-portal-kde / ksecretd / kwalletd (they crash-looped there on 2026-11-02, each
  crash a systemd-coredump compressing 15 MB at full CPU). Tiles get `--password-store=basic`, `GTK_USE_PORTAL=0`;
  every child runs with `RLIMIT_CORE=0`; tiles close via CDP `Browser.close` before SIGTERM (3 s) / SIGKILL.
* **Hardware decode.** Chromium 154 only uses nvidia-vaapi-driver with `VaapiOnNvidiaGPUs,VaapiVideoDecoder` enabled
  (CDP Media: `FFmpegVideoDecoder` → `VaapiVideoDecoder`; a live topstreamer NFL tile: renderer 47.6 % → 5.3 % CPU,
  29 fps, NVDEC 0 → 5 %). Same flags for the tv instance.
* **Resource guard.** The discord wall won't start above load 16 or under 3 GB available (`WALL_MAX_LOAD`,
  `WALL_MIN_AVAIL_MB`) and auto-stops after 30 s past either.
* **The local LLM steps aside** (`wall/llm.py`, `WALL_LLM_GUARD=unload|stop|off`): with any wall up, Ollama's models are
  unloaded every 10 s (and before a discord start, so the VRAM check counts them), warmed back after. Sage's
  `local_chat` keeps working meanwhile (it reloads the model, which the next tick evicts); `stop` mode stops
  ollama.service instead so Sage goes straight to its cloud path (needs `deploy/ollama-wall.polkit.rules`).
* **Stall watchdog** (the CDP loop, every 2 s per tile): a video stuck 3 s jumps to the next buffered range + 0.3 s,
  after 10 s with nothing ahead to the live edge − 3 s, after 30 s the tile reloads.

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

## The bands around the tiles (`wall/bands.py`, `wall/qml/Bands.qml`)
2 and 3 tiles leave bands above and below the 16:9 arrangement (with or without the L-bar; 1 and 4 fill their area).
The overlay process (its `--bets http://127.0.0.1:7795/i/<name>`, the same service) maps up to two more OVERLAY layer-shell
surfaces sized exactly to those bands (`lbar.js wallBands`; exclusive zone 0 so the bottom one sits on the L-bar's band),
hidden while the full / matchup board is up. Every 2 s it reads `GET /i/<name>/bands`:
`{n, sound, lbar, view, title, league, tiles: [{url, host, delay, src, game, synced}], ticker, delay, chat}`.
* Header strip against each tile (the top band; 3-up's lower small tile gets its header below it): teams + colours,
  score, period + clock, a speaker on the sound tile, the tile's delay.
* Spoilers: `ScoreTape` keeps each scoreboard with the time it was first seen; a header shows the one as of
  now − the tile's delay, the strip the one as of now − the default. No snapshot that old yet (a wall that just
  started) → teams + "syncing", never a score. Delays are the SyncStore's (`sync.delay(url)`) and the tile → game
  mapping is `sync.tile_games`, the same as `/api/overlay/bets` hands the L-bar's boards. Where the overlay's own
  delayed board holds the game, the header takes that copy (`qml/bands.js headerGame`), so header and sidebar agree.
* Backdrop: dark base tinted by each tile's team colours, faint court arcs (NBA) / yard lines (NFL), a drift stepped
  every 2 s over a 4-minute period. Nothing flashes or pulses; the strip changes page every 14 s with a slow fade.
* Bottom band without the L-bar: the other games (delayed) + the watch party's chat (the L-bar's sidebar has the chat
  when the L-bar is up).
* One OVERLAY surface, top-left anchored, exclusive zone −1 (an exclusive-zone-0 bottom-anchored surface landed at
  the screen's bottom on the real KWin when the L-bar wasn't up). It owns the L-bar's corner while the L-bar is drawn,
  else the whole screen: tiles placed for the L-bar with no L-bar on screen leave nothing of the kiosk / panel showing.
  Only 2 and 3 tiles.
* A tile with no live game shows its ended game ("Final") or the page's team ("Clippers · LAC").
* TV OS gate: the tv instance asks the TV OS `/healthz` `active` every 2 s. Every overlay surface (boards, bets, bands)
  draws only while it is `"wall"` (`on` in `/bands` and `/api/overlay/bets`); a wall the TV OS didn't start (the
  service API) stops after 8 s in another app (Dreamscape, YouTube, News…).
* Preview: `QT_QPA_PLATFORM=offscreen qml6 wall/qml/Preview.qml -- --layout none --n 2 --bets http://127.0.0.1:7795/i/tv --out /tmp/b.png`.

## Watch page, sync, chat, viewers, votes (sharplab-web, `web/wall.py`)

* `GET /wall.html` — the player page (static; `/wall` needs the optional Caddy snippet) (hls.js vendored at `web/static/vendor/hls.min.js`, no CDN). Login required.
* **Phones** (most viewers): portrait pins the page to the visual viewport (`--vvh`/`--vvtop` from `visualViewport`) —
  video stuck on top, a slim bar (🔇 ⛶ ⧉, sync, watching), then chat filling the rest with the input above the keyboard;
  "Change the wall" and who's watching are collapsed into bottom sheets (an avatar strip opens the list). Landscape is
  the video full screen with chat as a see-through overlay on the right that peeks in on a message and hides after 5 s,
  plus a 💬 toggle with an unread badge. Nothing flashes (fades only). The media queries live in `wall.css` and
  `wall.js` (`PORTRAIT`/`LANDSCAPE`) and must stay in step.
* **Chat ding:** a ~150 ms two-note Web Audio chime (no file, gain 0.05) for other people's messages only, when the chat
  isn't in view (tab hidden, scrolled up, landscape overlay hidden, full screen, a sheet open): first message of a burst
  (10 s quiet ends one), at most once per 20 s. The AudioContext is made on the first tap; 🔔/🔕 in the chat header,
  kept in `localStorage["wall.ding"]`. `window.__wallDing.count` counts chimes for tests.
* **Picture-in-picture:** ⧉ in the bar (`requestPictureInPicture`, or iPhone Safari's `webkitSetPresentationMode`; hidden
  where neither exists), `autopictureinpicture` + a media-session `enterpictureinpicture` handler for Chrome's automatic
  pop-out on an app switch, and media-session metadata (wall name, `/wall-art.png`) with play/pause. Nothing pauses
  on `visibilitychange`, so hls.js and the sync loop keep running in PiP.
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
  plain text rendered with `textContent`, a token bucket per sender: 6 in a burst then 1 / 2 s, the same text again
  within 10 s dropped, last 200 kept), presence (heartbeat
  every 15 s with `hidden`; away = hidden or no heartbeat for 60 s), viewer list with names + avatars/initials, votes,
  moderation (owner + `WALL_ADMIN_IDS`: delete a message, mute a user for N minutes). Join/leave notices off by default.
* **Abuse guard** (`web/wall_guard.py`, in memory, one worker): every `/api/v1/wall*` route and socket message goes
  through per-identity token buckets (user / guest id, else IP; IP keys looser): reads 10/s burst 40, writes 1/s burst 8
  (429 + Retry-After, or an `error` frame). The same proposal / vote from the same identity within 1.5 s counts once.
  More than 20 refusals in 60 s = a 10-min timeout (writes + socket actions refused, watching still works), logged and
  told to the owner + admins as a private chat line. Lift: `/lift <id or name>` in chat (`/timeouts` lists), unmute,
  or `POST /api/v1/wall/abuse/lift {who}` (X-Api-Key; `GET /abuse` lists). Exempt: owner, admins, X-Api-Key, the wall token.
* **Votes:** anyone proposes; screen changes (tile swap, layout, board, start, stop) pass on a strict majority of the
  *active* viewers (yes > active/2; ties fail), open 60 s, proposer counts yes. Moving the sound needs
  `WALL_SOUND_APPROVALS` (default 2) yes including the proposer, or 1 when only one viewer is active. The owner
  (`WALL_OWNER_ID`, David) applies instantly. One open proposal per user, 1 proposal / 20 s per user. Proposals are
  posted into chat. Discord `/wall` changes by non-owners become proposals on the same table.
* **Bot API:** `GET /api/v1/wall/state` (viewers, open proposals), `POST /api/v1/wall/propose`, `POST /api/v1/wall/vote`,
  `POST /api/v1/wall/link` (login link for a Discord member), `GET`/`POST /api/v1/wall/chat` (the TV wall's chat, below).
* **Demo mode** (`WALL_DEMO=1` + `WALL_DEMO_KEY`, ≥ 16 random chars, on sharplab-web; off by default). The watch link
  becomes `/wall.html?k=<key>` (the bot posts that one: it takes `watch_url` from `GET /api/v1/wall/state`). With the
  key and no SharpLab login, the page asks for a nickname (2–24 letters / digits / space . ' - _, made unique among
  who's watching: "Kim (guest)") → `POST /api/v1/wall/guest` sets a signed 24 h guest cookie bound to the key and marked
  `"t": "friend"` (rotating the key or turning demo off ends every guest session; a cookie without the friend mark —
  issued before tiers — counts as public). **Friends have a member's powers on the watch page**: the stream, chat,
  votes, every proposal (start / stop / tiles / spotlight / favorites …), the Bets panel (read-only: no Kalshi key to
  share) and every Kalshi view. Owner / admin powers (moderation, `/lift`, auto ad-skip, bets sync) and the other pages
  stay Discord-only. They show with a "guest" tag in the viewer list and chat. Limits: 5 nicknames / 5 min and 10 socket connects / min per IP (behind
  Caddy: X-Forwarded-For), the chat limits as before; the owner and admins still mute and delete. Without the key the
  page is the normal login gate. Proxy cost stays bounded: every viewer, guest or not, shares the same 0.5 s playlist /
  60 s segment cache (≈ 60 segments × ~0.6 MB), so N guests cost one upstream fetch — only the VPS's egress grows
  (~4.5 Mb/s per viewer).
* **Public mode** (`WALL_PUBLIC=1`; the old `WALL_GUEST_OPEN=1` means the same): the plain `/wall.html` (no key, no
  login, no cookie) is a read-only data page — the election map, state sheets, Kalshi markets / charts / Pulse, opted-in
  members' positions and the Bets panel, the live updates feed, and the chat **read-only** over the socket (no Discord
  ids or avatars in it). No stream (`/hls` 401s), no live-games list, no viewer list (a head count only, and public
  viewers are never in the list or the TV's count), no votes / proposals / spotlight: a public socket's every message
  but a ping is refused, `POST /election/spotlight` and `POST /bets/me|sync` 403. The page says "Live election data ·
  watch party is invite-only". Off: the plain page is the Discord login gate, as before. The keyed link is unaffected.
* **Nav:** the watch page and `/pulse.html` show the top nav (Server, Games …) to logged-in Discord members only.
* **Chat on the TV wall.** While the discord wall runs, the service polls `GET /api/v1/wall/chat?after=<id>` (bot API,
  `X-Api-Key`) every 1.5 s → `{messages (after id), ids (still in the window: a moderator's delete drops out), last,
  viewers [{name, guest, away}], watching, active}`; a muted user's messages are left out while the mute lasts. It keeps
  the last 20 (`wall/chat.py`) and serves them on loopback only: `GET /chat` → `{on, rev, messages, viewers, watching,
  …}`, `POST /chat {"text"}` → `POST /api/v1/wall/chat {discord_id: WALL_OWNER_ID, text}` (the socket's limits: 429 too
  fast, 403 muted, 422 empty). The TV router reads `/chat` every second: the wall overlay gets a drawn-ready model in
  `GET /api/overlay/view` → `"chat": {count, who, lines: [{id, name, guest, text, sys, fresh}]}` (the L-bar sidebar's
  lower half; lines dim after 20 s, no animation beyond a 2 s fade), and the phone's **💬 Chat** page posts through
  `POST /chat` as David (TV phones have no linked accounts). Config on the homelab: `WALL_WEB_URL`, `WALL_WEB_KEY`
  (= `WEB_API_SECRET`), `WALL_OWNER_ID`, `WALL_OWNER_NAME`; unset = no chat. The web app listens on the VPS's loopback
  only, so the private path is the tailnet-only Caddy listener in `deploy/caddy-wall.snippet`
  (`http://100.81.31.34:8796`, only `/api/v1/wall/chat`); `https://sharplab.djiang.xyz` works too.
* Discord ↔ web chat mirroring: **not built** (it would need the bot to relay both ways and map identities; easy to add
  later with the same bot API).

## Bets on the wall + the spoiler delay

Members' open Kalshi positions (from the keys `/kalshi link` stores) on the watch page and on the TV overlay — and, since
the stream is a capture of the TV, on the stream too.

**Privacy.** Opt-in per member, default off: `/kalshi wall share:True [exact:True]` or the toggle on the watch page
(table `kalshi_wall_optin`). Only members who share *and* have a linked key are read at all. Guests (demo nicknames)
never get the panel (403). Sizes are buckets (`<10`, `10+`, `50+`, `250+`, `1k+` contracts) and P&L is a % unless the
member also chose exact numbers. Balances are never read. Turning sharing off drops that member's history at once.

**Spoilers.** Kalshi reacts to a play within a second; the video shows it 20–60 s later, and a web viewer is a further
`WALL_SYNC_DELAY_S` behind the TV. So nothing is shown "now":

* Every price and position the poller sees is kept with the time it was seen (`shared/kalshi_wall.Tape`); a view is
  built **as of** a past instant and can't contain anything seen after it — not the price, not the P&L, not the ▲/▼.
* **D** = how far a tile's picture runs behind real life, per tile page (`wall/sync.py`, `$WALL_STATE_DIR/sync.json`):
  manual (− 5 s / + 5 s, or **Score just changed**) > auto (scorebug OCR) > default `WALL_BETS_DELAY_S` (45 s).
* TV overlay: each tile's badge as of `now − D(tile)`. Watch page: as of `(this viewer's picture) − D(tile)`, where the
  picture is the stream's program-date-time it is playing, mapped to the server clock with the playlist offset the page
  already tracks for sync (`window.__wallSync`), capped at `now − WALL_SYNC_DELAY_S`. Other live games: the default D.
* The **scoreboard + ticker** are held back the same way (`board.js` `delayed()`): the overlay keeps a few minutes of
  board polls and draws every game from the newest poll at least that game's tile delay old ("Syncing · 45 s" until it
  has one). The fantasy board uses the default D. (The matchup panel is not delayed.)

**Auto-calibration (scorebug OCR).** Every 2 s, for each tile showing a live game, while bets or a board are shown, the
service grabs the tile's frame read-only over CDP (`Page.captureScreenshot` with a clip scale, so the video comes at its
own resolution, ~1920 px across; once the bug is found only its box is grabbed) and reads the scorebug with tesseract
(`wall/scorebug.py`: three image versions — as is, dark ink, light ink; a digits-only one-line read per score cell, two
versions must agree). Finding the bug = the bottom (then top) band's numbers that form a recent ESPN score, side by side
or stacked. When the on-screen score changes to a score in ESPN's play-by-play (`summary?event=`, `wallclock` of each
play) and reads the same twice running, lag = first sighting − that play's wall clock; D = median of the last 5, 0–120 s.
A bug unreadable for ~30 s (replays, ads) is searched for again (at most every 20 s, alternating 1× / 1.5× upscale for
small 720p bugs). Measured on the box 2026-11-03: Warriors broadcast 38–42 s behind ESPN's play clock. Hard case: the
Clippers' dark-digits-on-grey bug reads on some grabs only (the retries cover it; the fixture test is a non-strict xfail).
No tesseract → no auto; manual + default still apply.

**Tap calibration.** "Score just changed" (owner on the watch page; the TV's phones can `POST /sync {"url", "tap": true}`
on loopback) → the scoring play whose wall clock is closest to `tap − current D` within 2.5 min → D = tap − its wall
clock, as a manual setting. "Auto" goes back to the OCR value.

**Kalshi load.** One poller in sharplab-web for everyone, only while someone looks (the page or the TV feed asked in the
last 2 min): every 20 s one signed `GET /portfolio/positions` per sharer + one public `/markets?tickers=` batch, through a
token bucket (5/s, burst 10). Viewers read the shared book; requests never reach Kalshi.

**Paths.**

| who | call |
|---|---|
| watch page (members) | `GET /api/v1/wall/bets?at=<picture ms>` → `{games: [{key, label, tile, url, delay, src, as_of, rows}], show, default, owner}` |
| watch page (members) | `GET`/`POST /api/v1/wall/bets/me` `{share, exact}` |
| watch page (owner/admins) | `POST /api/v1/wall/bets/sync` `{show}` / `{default}` / `{url, nudge: ±1}` / `{url, auto: true}` / `{url, tap: ms}` → wall service `POST /sync` (bearer) |
| wall service → web | `POST /api/v1/wall/bets/feed` (X-Api-Key) `{games: [{league, id, away, home, start, delay}]}` → rows already delayed per game, every 5 s while a wall runs and bets are shown |
| overlay → wall service | `GET /i/{inst}/api/overlay/bets` (loopback) → `{show, n, view, tiles: [{i, delay, src, label, rows}], delays: {espnId: D}, default, ticker, auto_lbar, on}`; the overlay process gets `--bets http://127.0.0.1:7795/i/{inst}` |
| TV phones / web → wall service | `GET /sync`, `POST /sync` (loopback, or tailnet + bearer); `{"ticker": bool}` = a bets line in the L-bar's ticker too |
| wall state | `GET /i/{inst}` carries `sync: {show, default, tiles: [{url, delay, src, samples, game}]}` |

**How the overlay data reaches the TV.** The TV's overlay (`qml6 Overlay.qml`, started by this service for both walls)
polls `--api` (the TV router, :7790) for its view + board as before, and now also `--bets` (this service on loopback) every
2 s. The service gets the rows from sharplab-web over `WALL_WEB_URL` with `WALL_WEB_KEY` (the chat's path).

**Where the bets draw: the L layout, never over the video** (the tile-corner badges are gone — they covered the game):
* L-bar up: a solid **Bets** section in the sidebar above the chat — per position the tile number, who, side + market,
  entry → now, ▲/▼ % (a calm green / red row tint easing over 1.5 s, nothing flashes). With `ticker` on, the bottom
  band's crawl carries a bets line too ("David YES GSW −3.5 42¢→51¢ ▲21%").
* No L-bar, 2 or 3 tiles: each band header gets a "2 BETS ▲" chip; the bottom band shows the rows (above the
  "Elsewhere" strip, or in the strip itself when there's no room).
* No L-bar, 1 or 4 tiles (no bands): while bets are shared and shown, the service brings the L-bar up by itself
  (`auto_lbar`: the tiles re-tile into the corner, the overlay draws the L-bar) and lets it go 30 s after the last one.
The mirror stream captures the TV's screen, bets included — hence the opt-in.

**Not built here:** the TV phone remote's buttons (xpoes123/tv — the service API above is ready: `GET /sync`,
`POST /sync {"show"}`, `{"url", "nudge"}`, `{"url", "tap": true}`), price-jump annotations with the play that caused them.

## Data tiles + Election night (2026-11-03)

**Data tiles.** A tile may be one of this service's own pages, `http://127.0.0.1:7795/p/<page>` (`wall/pages/`), instead of a
website. `engine.own_page(url)` marks them: the CDP round (fullscreen + stall watchdog) skips them, so a page without video can
never fall into "stalled → reload"; a data tile never takes the sound (`sound_tile`: a start, or a tap on it on the phone,
leaves the sound on the first tile with video); the discord wall may stream them (`public_url` lets `/p/` through — nothing
private there). The gate lets GET/HEAD `/p/*` through from browser contexts on loopback (the tile reading its own
`data.json`); every other path still refuses them.

**Pages.** `/p/election/map` — the rotating results map: Senate → House → Governors every 40 s (`?every=`, `?view=S|H|G` pins
one), a 2 s crossfade, states coloured won / leading (dimmed) / no votes yet / no race, the balance bar (won + not up, leading,
not counted, the control line), net change, flips, the clock. `/p/election/races` — key races (CNN's races to watch: Senate,
Governors, then the closest House ones once counting), 5 a page every 12 s: candidates, %, bars, % in, margin, ✓ + whose call.
Both read `/p/election/data.json` every 30 s and say "Unofficial results · calls: <desk>". Calm: no blinking, colours ease.

**Feed** (`wall/election.py`, `Results`): CNN (`politics.api.cnn.io/results/national-races/2026-{SG,GG,HG}.json` +
`bop/2026-*-US.json`; CNN Decision Desk calls) → NYT (`static01.nyt.com/elections-assets/pages/data/2026-11-03/results-*.json`,
AP calls) → Fox (`feeds-elections.foxnews.com/…/2026_Generals/*/national_level_summary/file.json`). The first source that
answers with races wins each round; all three are the public JSON behind those sites' own results pages (no key, no login). It
polls every 30 s only while a page / the board asked in the last 3 min. New calls are logged with their time
(`$WALL_STATE_DIR/election-calls.json`). `WALL_ELECTION_REPLAY=<dir>[:share]` replays recorded CNN files (2024's final night is in
`tests/fixtures/wall/election`; `:0.6` = as if 60 % were in) for renders and a dry run.

**Board** `election` (BOARDS): the L-bar sidebar = balance per office (Senate, House, Governors) + the latest calls; the band's
ticker = race calls as they happen (then close races, then the next poll closings); full = the three offices side by side +
calls. Not held back by the spoiler delay (`board.js delayed`). The TV router reads it from `GET /p/election/board.json`.

**Kalshi election trades** (`shared/kalshi_election.py`). Opted-in members' midterm positions, by the same rule as sports bets: nobody shows until they run
`/kalshi wall share:True`, with sizes bucketed unless they also chose `exact`. The bets poller in sharplab-web now also keeps
positions in midterm markets. These go through the same token bucket and the same single `/markets` batch, and each market
is mapped to a race in the results feed:
* Kalshi series, checked live on 2026-11-03:
  * Senate: `SENATE{ST}[S]-26` (`S` = special: FL, OH), `KXSENATE{ST}-26NOV`, `KX{ST}SENATE-26…` (one market per person).
  * Governor: `GOVPARTY{ST}`, `KXGOVPARTY{ST}`, `KXGOV{ST}`.
  * House: `HOUSE{ST}{N|AL}`, `KXHOUSE{ST}{NN}`, `KXHOUSERACE-{ST}{NN|AL}-26` (AL = district 1).
  * Control: `CONTROLS-2026` / `CONTROLH-2026`.
  * Seat counts: `KX{R|D}{HOUSE|SENATE}WON-26NOV03` (seats won tonight) and `…SEATS…-27` (seats held in the new Congress).
* Kalshi's year codes aren't reliable: `GOVPARTYNH-28` and `GOVPARTYKS-27` are 2026 races. A market that names a person
  therefore counts only if that person is on tonight's ballot in our results. If the market sits under the wrong state
  (`SENATELA-26` lists Kentucky's candidates), it is found by first and last name. A party-only market needs the year
  26. Positions in markets we can't place (combos, turnout, 2028) don't show.
* No spoiler delay. Results aren't a picture, so election rows are built as of now, on the page and on the TV.
* While an election board or page was looked at in the last 3 min, the service's 5-second feed call adds
  `"election": {races, offices}`. These are the results for the races the web named in `need` on the previous call, so
  the watch page shows the same status. The web answers with `election: [rows]`.
* Where it shows:
  * The Election board's sidebar: a **Trades** section under the balance of power. The offices shrink to make room, and
    the calls are left whatever space remains (they're in the ticker anyway).
  * The ticker: race calls and positions take turns; a call on a race someone holds comes first.
  * The key-races tile: a chip per holder (`data.json` `holders`).
  * The watch page's Bets panel: an "Election night" box.
* Once a held race is called, the row and the ticker say "✓ CALLED · Hansen wins +38%". It turns calm green for a win and
  muted for a loss, and nothing flashes. Control markets resolve when a party reaches control. "At least N seats won"
  resolves once N is reached; the others resolve when every race is called.
* Turning "Show bets on the TV overlay" off hides the trades and the chips too. Nothing is ever drawn over video.

**Election map + Kalshi on the watch page** (`web/wall_election.py`, `shared/election_markets.py`, `web/static/wall-election.*`).
An "Election" panel under the player: a US map coloured like the TV's (won / leading / no votes yet / no race), Senate / House /
Gov toggles, the balance bar, and "Control of Senate / House" chips. Click or tap a state (or pick it from the list — small
states are hard to tap) → a sheet (a drawer on desktop, a bottom sheet on phones) with each race: candidates, % in, margin,
✓ + whose call; its Kalshi markets (bid / ask / last, implied %, 24 h change from Kalshi's day-ago book); a chart of implied %
(hourly candles for the last 4 days on the left 36 %, minute candles for the last 12 h on the rest; poll close and the call
time marked); and, for members only, the opted-in members' positions there. Senate / Governor charts load with the sheet,
House ones on "Show price chart". On phones the panel itself is a sheet opened from the bar's 🗺 button.
* Results: sharplab-web runs its own `wall.election.Results` (same sources, same order, only while someone looks).
* Which markets: Kalshi's Elections series list (`GET /series?category=Elections`, cached 6 h) → the series naming the state
  (`SENATEGA`, `GOVPARTYPA`, `HOUSEPA7`…) + `KXHOUSERACE` (every district, one call); each series is one public
  `GET /markets?series_ticker=` (cached 45 s); a market goes to a race by `kalshi_election.resolve`.
* Charts: `GET /series/{s}/markets/{t}/candlesticks` (public, no auth; checked 2026-11-03): today's window cached 45 s, the
  older days 15 min. A candle's % = the mid of its closing yes bid / ask when the spread is ≤ 15¢, else its last trade.
* Load: everything on demand (a state opened, its sheet's 30 s refresh, a chart's 60 s refresh), cached and shared across
  viewers with single flight, through the bets poller's token bucket, at most 3 Kalshi calls at once, one retry on a 429.
* API (`/api/v1/wall/election/…`): `map`, `shapes`, `state/{ST}`, `control/{S|H}`, `chart/{race id | CTRL-S | CTRL-H}`
  (only that race's own markets) — any viewer, guests included (public data); `positions` only in a member's answer.
  `POST spotlight {state}` (members) and `GET spot/{ST}` (the wall service, `X-Api-Key`) are the TV spotlight's.
  `GET odds` (the wall service, `X-Api-Key`) → `{race id: {who, party, prob, chg24}}`: every race to watch's Kalshi
  favourite, live (no spoiler delay) — one batched `/markets?tickers=…` per 5 s (100 tickers a call), the race → tickers
  map from the series walk every 5 min. The wall service asks every 10 s while an election page is looked at and serves
  it in `/p/election/data.json` (`odds`) and `/p/election/odds.json` (the races tile re-reads that every 10 s in place).

**Vote updates + race detail** (`wall/election_feed.py`, `wall/election_counties.py`, `web/wall_election_live.py`,
`web/static/wall-election-live.*`).
* Every poll is diffed per race into updates: a drop ("+12,400 votes · Talarico +1.2 → +0.8"), a lead change, a call, poll
  closings (from CNN's `assignedPollsClose`). Ranked: lead change > big drop (≥ 25 K or 5 %) in a close race (< 5 pts) > call
  / polls closing > routine drop. History: `$WALL_STATE_DIR/election-history.json` (web: `data/`), rolling (200 updates a
  Senate / Governor race, 30 a House race), with each race's last state so a restart resumes the diff.
* The watch page: a "Live updates" stream under the map (key races / all, newest first, tap → the state sheet; before
  polls close it lists the next closings). In the state sheet each Senate / Governor race (House on "Vote details") shows
  votes, the margin in votes, % of the expected vote in, votes left, its counties (closest / most vote out / swing /
  biggest) and its timeline with the Kalshi price at each update (from the markets cache only — no extra Kalshi call).
* Per race, CNN has county votes + `percentReporting` + `voteTimestamp` (`results/county-races/<key>.json`; no expected
  vote, no history; swing = against the same file for the last presidential race). NYT/AP's race page
  (`results-<state>-us-senate.json`) adds the expected vote (`eevp`, `total_expected_vote`) per state and county and their
  own 2024/2020 comparisons — used when it answers.
* Fast lane: a race open in a sheet is polled every 10 s from CNN's one-race file (`results/race/<key>.json`, ~700 B)
  while it's open; the national poll stays 30 s.
* TV: the Election board's sidebar says **LATEST** (the most significant updates of the last 45 min); lead changes and
  big drops lead the ticker.
* `WALL_ELECTION_REPLAY=tests/fixtures/wall/election:sim=900` replays 2024's night from 5 pm to 2 am ET in 900 s (county
  files for PA, AZ, MI, WI, NV, OH, MT, TX Senate + NC Governor), poll closings and all.
* API: `GET live?scope=key|all`, `GET race/{race id}`.

**Presets** (seeded once, `wall-election-seeded` marker): "Election Night" (map, key races, NBC News NOW, PBS News), "Election +
NBA" and "Election map + NBA" (`fixed: true`: the preset's URLs stay as fixed tiles, live NBA games fill the rest).

## Ad-skip: commercials, breaks and favorites (`wall/adwatch.py`, `wall/onair.py`)

**Listening** — `python -m wall.adwatch`, its own user unit `deploy/sharplab-adwatch.service` (same `.env.wall`). Idle while
no wall is up. It follows the streams on the walls (+ the originals the policy swapped away + the favorites) and a pool of
national news channels (`WALL_ADWATCH_POOL`, default NBC News NOW, PBS NewsHour, ABC News, CBS News, LiveNOW from FOX;
each checked live with `wall.streams.check` every 10 min), plus the live Spectrum lineup (`wall/spectrum.py`, listed in each election-mode wall state), at most `WALL_ADWATCH_MAX` (16; walls first, then Spectrum, then the pool) at once.
* Per stream: `yt-dlp -g` HLS URL, refreshed every 20 min or on failure. Text: CEA-608/708 captions when ffprobe sees
  "Closed Captions" on the lowest rendition (lavfi `movie=…[out0+subcc]`) — YouTube's live HLS carried none in a check
  on 2026-11-03 — else 15 s mono 16 kHz audio chunks → faster-whisper `small.en` on the GPU (`int8_float16`, VAD, one
  model for every stream; `uv sync --extra adwatch`). No CUDA / no extra → the homelab's CPU whisper service
  (`WALL_ADWATCH_WHISPER_URL`, serialized, ~realtime: enough for one or two streams). Chunks older than 40 s are dropped.
* Every 20 s per stream, the last ~45 s: a keyword pass (ad cues: "ask your doctor", "side effects", "terms apply",
  "% APR", "call now", "brought to you by"…; coverage cues: election, polls, votes, Senate, projected…; ≥ 60 % silence =
  break); anything not clear-cut → sharplab-web `POST /api/v1/wall/election/classify` (X-Api-Key; Claude Haiku
  `claude-haiku-5-5`, effort low, 8 s timeout → "unknown"; ≤ 1 call per stream per 15 s, cached by text hash). No answer →
  a weaker keyword call.
* Every 5 s: `POST /adwatch {streams: {url: {label, confidence, since, text_tail}}}` (loopback only).

**Acting** (`wall/onair.py`, in the wall service; only while on: `WALL_ADWATCH=1` at start, or `POST /i/{inst}/adwatch
{on}` through `target()` — the watch page's "Auto ad-skip" toggle, owner / admins only):
* Sound: the sound tile's stream at ad/break ≥ 10 s → to a tile on air (a favorite first, then the last manual pick, then
  the surest). After an automatic move it goes back to a favorite on air / the manual pick once that's coverage ≥ 10 s.
* Tiles: a stream tile (never a /p/ page, never tiles 1–2 in election mode) at ad/break/off-topic ≥ 60 s swaps in place to
  the best pool stream on air (≥ 10 s) not already up (favorites first). It goes home — to its favorite once that is
  coverage ≥ 20 s, else the original after ≥ 30 s. At most one swap per tile per 2 min. Changes by people (votes, the
  owner, the TV's phones, the default screens) are noticed by diffing the tiles: that tile's auto-swaps pause 10 min and
  its remembered original is dropped.
* Favorites: `POST /i/{inst}/favorite {url, on}` (op `favorite`, ★ on every stream tile and pool stream in the panel), two
  at most (a third replaces the oldest), `$WALL_STATE_DIR/favorites.json`. Starring a stream that isn't up puts it on
  tile 3 or 4 (whichever doesn't hold the other favorite).
* Break log + clock (`wall/breaks.py`): every break the board sees (coverage/off-topic → ad/break and back, ≤ 15 min) is
  a row `{stream, start, end, label}` in `$WALL_STATE_DIR/breaks.jsonl`. Per stream, from that log only (≥ 3 breaks):
  `{next_likely: "~:48", p_soon, n_breaks}` — p_soon = the share of watched hours with a break start within ±2 min of
  now + 60–90 s.
* Preemptive sound (never tiles): the listener sends the last ~20 s as `recent`; classify also asks Jev "about to cut to
  a commercial within the next minute?" (soon / not) → `soon` (Haiku's own estimate when Jev has none). The sound moves
  to an on-air tile before the break when its stream is coverage and soon ≥ 0.85, or soon ≥ 0.6 with clock p_soon ≥ 0.6
  — never on the clock alone — and then only if a last look says nothing can't-miss is on: the wall service posts
  `{check: "important", stream, text}` to classify (Jev yes/no on breaking news / a race call / a candidate speaking /
  results; 1.5 s timeout); yes ≥ 0.5, no answer or a timeout = stay (re-checked ≤ every 5 s). Marked automatic: once the
  break comes and ends the sound returns as usual; no break within 2 min → it goes back, and that stream isn't preempted
  again for 5 min. The watch page adds "· break soon" / "· break likely ~:48" to an on-air badge.
* State: `onair: [{label, confidence, since, tail, soon, clock} | null per tile]`, `favorites: [url]`, `adwatch: {on, orig: {tile:
  url}, pool: [{url, label, …}]}` (a mirror shows the TV's). The watch page shows "● on air" / "⏸ break" / "ad" per tile.

**Stream director** (`wall/streamdir.py`): Jev runs the TV's stream tiles 3–4 and the sound while the TV wall is in
election mode, ad-skip is on and the "Auto-director" toggle is on (`POST /i/{inst}/director {on}`, owner / admins;
`$WALL_STATE_DIR/stream-director-on`, default on). Every 60 s: the fresh coverage streams (≥ 20 s, ≤ 12) go to
`/api/v1/wall/election/direct` with a `question` as choices s1…s12 ("name (group) — saying, as of 30s ago: …"); the state =
the night summary + what's on tiles 3–4 / the sound + the last 5 min of watch-party chat (aged, plain text, marked
untrusted). Picks: top 2 (+0.08 for a favorite; a different Spectrum group / outlet when within 0.1). Sticky: a tile
changes only for ≥ +0.2, after 4 min on the tile, 10 min after a hand change, never at a break (ad-skip's) or while its
stream's `important` ≥ 0.5 (unknown = hold); the pick becomes the tile's new home. Sound: to the better of 3–4 for ≥ +0.2,
2 min after its last move, not after a hand move (10 min), not mid-break, not mid-moment. Jump: a stream off the wall with
`important` ≥ 0.8 (the classify call's third Jev question — no extra call) while both tiles are < 0.5 → onto the
lower-rated unpaused tile with the sound at once + a "🎬 Switching to …" chat notice (≤ 1 / 3 min, ≤ 1 / stream / 10 min;
chat never triggers a jump). State: `stream_director: {on, active, last: {t, picks: [{url, name, p}]}, next_in}`.

## `/wall` in Discord (`bot/cogs/wall.py`)

`/wall start [preset]`, `/wall stop`, `/wall status`, `/wall set` (layout / board / sound / tile from a live game, a
team or a URL), and an interactive view (preset select, live-game select, team picker, layout + board + sound
buttons). When the wall starts (a /wall command, the panel, a vote that passed, the web page) the embed is posted in the channel
the start came from (web-page starts: the last channel used, else `WALL_CHANNEL_ID`); embed locations persist in
`WALL_BOT_STATE` (default `data/wall_bot.json`) and the panel is a persistent view. A bare `/wall start` = "same as the
TV". One embed per channel, edited in place: what's on each tile, layout/board/sound, "Watching: David,
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
