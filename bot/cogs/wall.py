"""/wall — the shared Discord video wall (a headless wall on the homelab, streamed to sharplab.djiang.xyz/wall).

One group, four subcommands (start / stop / status / set) plus an interactive panel on the embed. The owner
(WALL_OWNER_ID) applies changes at once; anyone else allowed (WALL_PERMISSION=anyone, or =roles with WALL_ROLE_IDS)
makes a proposal that viewers vote on (sharplab-web runs the vote; see docs/wall-service.md). One embed per guild,
edited in place on every change and every 30 s while the wall runs.

When the wall starts (a /wall command, the panel, a vote that passed, the web page), the embed with the watch link and
Get my link is posted in the channel the start came from (a start carries its origin through the vote to the wall
service); a start from the web page goes to the last channel used, else WALL_CHANNEL_ID. Where the embeds are, the last
channel and the last start announced persist in WALL_BOT_STATE, so a restart keeps editing them and the panel's
buttons keep working (a persistent view).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.wall_client import WallError, WallService, WallWeb

log = logging.getLogger(__name__)

OWNER_ID = os.environ.get("WALL_OWNER_ID", "")
PERMISSION = os.environ.get("WALL_PERMISSION", "anyone")   # anyone | roles
ROLE_IDS = {r.strip() for r in os.environ.get("WALL_ROLE_IDS", "").split(",") if r.strip()}
CHANNEL_ID = os.environ.get("WALL_CHANNEL_ID", "")
# a Discord channel whose messages also go to the watch-party chat (so the TV's chat dock / the stream): one id or none
RELAY_CHANNEL_ID = os.environ.get("WALL_RELAY_CHANNEL_ID", "")
STATE_PATH = os.environ.get("WALL_BOT_STATE", "data/wall_bot.json")
TV_CHOICE = "__tv__"   # the panel's "Same as the TV" start
MAX_TILES = 4
LAYOUTS = ("lbar", "full")
BOARDS = ("nfl", "nba")
COLOR = 0x7AA2F7


# ---- pure helpers (tested) ---------------------------------------------------------------------------------------------
def may_use(user_id: str, role_ids: set[str], permission: str | None = None, allowed: set[str] | None = None,
            owner: str | None = None) -> bool:
    """Who may change the wall at all (directly or by proposing). None = the env setting."""
    permission = PERMISSION if permission is None else permission
    allowed = ROLE_IDS if allowed is None else allowed
    owner = OWNER_ID if owner is None else owner
    if owner and user_id == owner:
        return True
    if permission == "roles":
        return bool(allowed & set(role_ids))
    return True


def route(user_id: str, owner: str | None = None) -> str:
    """"apply" = straight to the wall service (the owner), "propose" = a vote on the watch page."""
    owner = OWNER_ID if owner is None else owner
    return "apply" if owner and user_id == owner else "propose"


def clean_url(text: str) -> str:
    """A typed address → an http(s) URL, or ValueError (same rule as the TV: never a flag, never a file)."""
    u = (text or "").strip()
    if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(/|$)", u, re.I):
        u = "https://" + u
    if not (u.startswith(("http://", "https://")) and len(u) <= 500 and not any(c.isspace() for c in u)):
        raise ValueError("Not a web address: needs http:// or https://")
    return u


def parse_urls(texts: list[str | None]) -> list[str]:
    """The /wall start url1..url4 options → 1–4 clean URLs (ValueError when there are none or too many)."""
    urls = [clean_url(t) for t in texts if t and t.strip()]
    if len(urls) > MAX_TILES:
        raise ValueError(f"At most {MAX_TILES} tiles.")
    return urls


def tile_index(n: int | None, count: int, adding: bool = True) -> int:
    """A 1-based tile number from Discord → the service's 0-based index (count = tiles now; adding allows count+1)."""
    if n is None:
        raise ValueError("Pick a tile.")
    top = min(count + (1 if adding else 0), MAX_TILES)
    if not 1 <= n <= max(top, 1):
        raise ValueError(f"Tile must be 1–{max(top, 1)}.")
    return n - 1


def watching_line(viewers: list[dict], shown: int = 3) -> str:
    """"Watching: David, Steph, Kim, +3" — active viewers first; away ones are counted, and named only if there's room."""
    if not viewers:
        return "Watching: nobody yet"
    ordered = sorted(viewers, key=lambda v: bool(v.get("away")))
    names = [v.get("name") or "?" for v in ordered[:shown]]
    names = [f"{n} (away)" if v.get("away") else n for n, v in zip(names, ordered)]
    rest = len(ordered) - len(names)
    return "Watching: " + ", ".join(names) + (f", +{rest}" if rest else "")


def tile_label(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/?#]+)(/[^?#]*)?", url or "")
    if not m:
        return url or "?"
    tail = (m.group(2) or "").rstrip("/").rsplit("/", 1)[-1]
    return tail if tail and len(tail) <= 24 else m.group(1)


def describe(action: dict) -> str:
    """One line for a proposal / confirmation."""
    op = action.get("op")
    if op == "start":
        if action.get("mirror"):
            return "start the wall: same as the TV"
        what = action.get("preset") if action.get("preset") is not None else \
            f"live {action['live'].upper()}" if action.get("live") else ", ".join(tile_label(u) for u in action.get("urls", []))
        return f"start the wall: {what}"
    if op == "stop":
        return "stop the wall"
    if op == "follow":
        return "follow the TV again"
    if op == "tile":
        return f"tile {action['i'] + 1} → {tile_label(action['url'])}"
    if op == "remove":
        return f"remove tile {action['i'] + 1}"
    if op == "sound":
        return f"sound → tile {action['i'] + 1}"
    if op == "board":
        bits = []
        if "layout" in action:
            bits.append(f"layout {action['layout']}")
        if "board" in action:
            bits.append(f"board {action['board'] or 'off'}")
        if "visible" in action and "board" not in action:
            bits.append("overlay on" if action["visible"] else "overlay off")
        return ", ".join(bits) or "overlay"
    return str(op)


def build_embed(state: dict | None, web: dict | None, watch_url: str, error: str = "") -> discord.Embed:
    st, web = state or {}, web or {}
    running = bool(st.get("running"))
    e = discord.Embed(title=f"📺 Wall · {st.get('name') or 'off'}" if running else "📺 Wall · off",
                      url=watch_url, color=COLOR if running else 0x565F89)
    if running:
        urls, live = st.get("urls") or [], st.get("live") or {}
        labels = live.get("labels") or []
        lines = []
        for i, u in enumerate(urls):
            name = labels[i] if i < len(labels) and labels[i] else tile_label(u)
            lines.append(f"`{i + 1}` {name}{' 🔊' if i == st.get('sound') else ''}")
        e.add_field(name="Tiles", value="\n".join(lines) or "—", inline=False)
        ov = st.get("overlay") or {}
        board = ov.get("board") if ov.get("visible") else "off"
        e.add_field(name="Layout", value=f"{ov.get('layout') or 'lbar'} · board {board or 'off'}", inline=True)
        if st.get("mirror"):
            e.add_field(name="📺 Mirroring the TV", value="follows the living-room wall live" +
                        (" (its screen)" if st.get("capture") == "screen" else ""), inline=True)
        if live.get("league"):
            e.add_field(name="Live", value=f"{live['league'].upper()} games auto-swap", inline=True)
    else:
        e.description = "Nothing on. `/wall start` or the panel below."
    e.add_field(name="Viewers", value=watching_line(web.get("viewers") or []), inline=False)
    props = web.get("proposals") or []
    if props:
        now = time.time()
        e.add_field(name="Open votes", value="\n".join(
            f"• {p.get('summary') or p.get('kind')} — ✓{p.get('yes', 0)} ✗{p.get('no', 0)} / needs {p.get('needed', '?')}"
            f" · {max(0, int((p.get('expires') or now) - now))} s" for p in props[:5]), inline=False)
    e.add_field(name="Watch", value=f"[{watch_url}]({watch_url}) — log in with Discord, or press **Get my link**.", inline=False)
    if error or st.get("error"):
        e.set_footer(text=(error or st.get("error"))[:200])
    return e


def relay_line(m: dict) -> str:
    """A watch-chat message as a Discord line: people's chat, and the trade feed; Jev's notes and other notices stay on the page."""
    text = discord.utils.escape_markdown(str(m.get("text") or ""))
    if m.get("kind") == "chat":
        return f"**{discord.utils.escape_markdown(str(m.get('name') or 'Guest'))}**: {text}"
    return text if str(m.get("text") or "").startswith("Trade:") else ""


def user_dict(user: discord.abc.User) -> dict:
    return {"id": str(user.id), "name": getattr(user, "display_name", None) or user.name,
            "avatar": user.avatar.key if getattr(user, "avatar", None) else None}


def role_ids(user) -> set[str]:
    return {str(r.id) for r in getattr(user, "roles", []) or []}


def split_teams(slugs: list[str], parts: int = 2) -> list[list[str]]:
    """Discord selects hold 25 options: 30–32 teams → two alphabetical halves."""
    s = sorted(slugs)
    k = -(-len(s) // parts) if s else 0
    return [s[i:i + k] for i in range(0, len(s), k)] if k else []


def load_state(path: str) -> dict:
    """{"embeds": {guild: [channel, message]}, "last": {"guild", "channel"} | None, "announced": started}."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        d = {}
    embeds = {}
    for g, v in (d.get("embeds") or {}).items():
        try:
            embeds[int(g)] = (int(v[0]), int(v[1]))
        except (TypeError, ValueError, IndexError):
            pass
    last = d.get("last") if isinstance(d.get("last"), dict) else None
    return {"embeds": embeds, "last": last, "announced": d.get("announced") or 0}


def save_state(path: str, embeds: dict, last: dict | None, announced: float) -> None:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"embeds": {str(g): list(v) for g, v in embeds.items()}, "last": last, "announced": announced}))
        tmp.replace(p)
    except OSError as e:
        log.warning("wall state not saved: %s", e)


def announce_target(st: dict, last: dict | None, default_channel: str = "") -> int | None:
    """Where a start's link goes: the channel it came from, else the last channel used, else WALL_CHANNEL_ID."""
    for o in (st.get("origin"), last):
        if isinstance(o, dict) and str(o.get("channel") or "").isdigit():
            return int(o["channel"])
    return int(default_channel) if default_channel.isdigit() else None


def origin_of(interaction) -> dict | None:
    if getattr(interaction, "channel_id", None) is None:
        return None
    return {"guild": str(interaction.guild_id or ""), "channel": str(interaction.channel_id)}


# ---- the cog -----------------------------------------------------------------------------------------------------------
class WallCog(commands.Cog):
    wall = app_commands.Group(name="wall", description="The shared video wall — watch parties at sharplab.djiang.xyz/wall")

    def __init__(self, bot: commands.Bot, service: WallService | None = None, web: WallWeb | None = None) -> None:
        self.bot = bot
        self.svc = service or WallService()
        self.web = web or WallWeb()
        self.relayed: set[int] = set()        # watch-chat ids that came from the relay channel
        self.relay_after: int | None = None   # the newest watch-chat id seen (None: not yet)
        self.state_path = STATE_PATH
        saved = load_state(self.state_path)
        self.embeds: dict[int, tuple[int, int]] = saved["embeds"]   # guild id → (channel id, message id)
        self.last: dict | None = saved["last"]                      # the last channel /wall was used in
        self.announced: float = saved["announced"]                  # the last start ("started") its link was posted for
        self.votes: dict[str, tuple[int, int]] = {}     # proposal id → its vote message (channel, message)
        self.target: dict[str, int] = {}                # user id → the tile the panel acts on (0-based)
        self._presets: list[dict] = []

    def watch_url(self, web: dict | None) -> str:
        """The link to post: sharplab-web's (keyed in demo mode, so guests can join with a nickname), else the plain page."""
        u = (web or {}).get("watch_url")
        return u if isinstance(u, str) and u.startswith("https://") else self.web.watch_url

    def save(self) -> None:
        save_state(self.state_path, self.embeds, self.last, self.announced)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """WALL_RELAY_CHANNEL_ID: a person's message there → the watch-party chat under their name (same limits as the page)."""
        if not RELAY_CHANNEL_ID or str(message.channel.id) != RELAY_CHANNEL_ID or message.author.bot:
            return
        text = " ".join(message.clean_content.split())[:300]
        if not text:
            return
        user = user_dict(message.author)
        user = {**user, "name": f"{user['name'][:30]} (Discord)", "avatar_url": str(message.author.display_avatar.url)}
        try:
            r = await self.web.chat(user, text)
        except Exception as e:  # noqa: BLE001 — a rate limit / web hiccup drops that one line, never the bot
            log.info("wall relay: %s", e)
            return
        mid = ((r or {}).get("msg") or {}).get("id")
        if mid is not None:
            self.relayed.add(mid)   # never echoed back into the channel

    @tasks.loop(seconds=2)
    async def relay_out(self) -> None:
        """The other way: the watch-party chat (people + trade lines) → WALL_RELAY_CHANNEL_ID. Starts at the newest line:
        a bot restart never replays the night."""
        if not RELAY_CHANNEL_ID:
            return
        try:
            feed = await self.web.chat_feed(self.relay_after or 0)
        except Exception as e:  # noqa: BLE001
            log.debug("wall relay out: %s", e)
            return
        if self.relay_after is None:
            self.relay_after = int(feed.get("last") or 0)
            return
        ch = self.bot.get_channel(int(RELAY_CHANNEL_ID))
        for m in feed.get("messages") or []:
            self.relay_after = max(self.relay_after, int(m["id"]))
            line = relay_line(m)
            if ch is None or m["id"] in self.relayed or not line:
                continue
            try:
                await ch.send(line, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as e:
                log.info("wall relay out: %s", e)

    async def cog_load(self) -> None:
        try:
            self.bot.add_view(PanelView(self, [], [], persistent=True))   # the panel's buttons survive a restart
        except Exception as e:  # noqa: BLE001
            log.warning("wall panel view: %s", e)
        self.refresh_loop.start()
        self.watch_loop.start()
        self.relay_out.start()

    async def cog_unload(self) -> None:
        self.refresh_loop.cancel()
        self.watch_loop.cancel()
        self.relay_out.cancel()

    # ---- doing things ----
    async def act(self, interaction: discord.Interaction, action: dict) -> str:
        """Apply (owner) or propose (everyone else allowed); returns the line to tell the user."""
        uid = str(interaction.user.id)
        if not may_use(uid, role_ids(interaction.user)):
            return "You're not allowed to change the wall (ask David for a wall role)."
        origin = origin_of(interaction)
        if origin:
            self.last = origin
            self.save()
        if action.get("op") == "start" and origin:
            action = {**action, "origin": origin}   # the link gets posted here when it starts (now, or when the vote passes)
        try:
            if route(uid) == "apply":
                st = await self.svc.apply(action)
                msg = f"Done: {describe(action)}."
                if action.get("op") == "start" and isinstance(st, dict) and st.get("running"):
                    await self.announce(st)
            else:
                p = await self.web.propose(user_dict(interaction.user), action)
                if p.get("applied"):
                    msg = f"Done: {describe(action)}."
                else:
                    msg = f"Proposed: {describe(action)} — viewers vote on the watch page (60 s)."
                    if p.get("id"):
                        await self.post_vote(interaction.channel, p)
        except WallError as e:
            return f"Couldn't: {e}"
        await self.refresh(interaction.guild_id)
        return msg

    async def post_vote(self, channel, p: dict) -> None:
        if channel is None or p["id"] in self.votes:
            return
        try:
            m = await channel.send(f"🗳️ **Vote:** {p.get('summary') or 'wall change'} — needs {p.get('needed', '?')} ✓ within 60 s",
                                   view=VoteView(self, p["id"]))
            self.votes[p["id"]] = (m.channel.id, m.id)
        except discord.HTTPException as e:
            log.warning("wall vote post failed: %s", e)

    async def snapshot(self) -> tuple[dict | None, dict | None, str]:
        st = web = None
        err = []
        try:
            st = await self.svc.state()
        except WallError as e:
            err.append(f"wall: {e}")
        try:
            web = await self.web.state()
        except WallError as e:
            err.append(f"web: {e}")
        return st, web, " · ".join(err)

    async def refresh(self, guild_id: int | None) -> None:
        if guild_id is None or guild_id not in self.embeds:
            return
        ch_id, msg_id = self.embeds[guild_id]
        ch = self.bot.get_channel(ch_id)
        if ch is None:
            return
        st, web, err = await self.snapshot()
        try:
            await ch.get_partial_message(msg_id).edit(embed=build_embed(st, web, self.watch_url(web), err))
        except discord.NotFound:
            self.embeds.pop(guild_id, None)
            self.save()
        except discord.HTTPException as e:
            log.warning("wall embed edit failed: %s", e)
        await self._sync_votes(ch, web)

    async def _sync_votes(self, ch, web: dict | None) -> None:
        """Post a vote message for every open proposal (from the page too); close the ones that ended."""
        open_ids = {p["id"] for p in (web or {}).get("proposals") or [] if p.get("id")}
        for p in (web or {}).get("proposals") or []:
            if p.get("id") and p["id"] not in self.votes:
                await self.post_vote(ch, p)
        if web is None:
            return
        for pid in [k for k in self.votes if k not in open_ids]:
            c_id, m_id = self.votes.pop(pid)
            c = self.bot.get_channel(c_id)
            if c:
                try:
                    await c.get_partial_message(m_id).edit(content="🗳️ Vote closed.", view=None)
                except discord.HTTPException:
                    pass

    async def announce(self, st: dict) -> bool:
        """The wall just started: post the embed (watch link + Get my link) where the start came from."""
        started = st.get("started") or 0
        if not st.get("running") or started == self.announced:
            return False
        self.announced = started
        self.save()
        ch_id = announce_target(st, self.last, CHANNEL_ID)
        same = [g for g, (c, _) in self.embeds.items() if c == ch_id]
        if same:   # that channel already has the /wall embed (restarts, redeploys, repeat starts): update it, no new message
            for gid in same:
                await self.refresh(gid)
            return False
        ch = self.bot.get_channel(ch_id) if ch_id else None
        if ch is None:
            log.info("wall started, but no channel to post the link in (%s)", ch_id)
            return False
        _, web, err = await self.snapshot()
        try:
            m = await ch.send(embed=build_embed(st, web, self.watch_url(web), err),
                              view=PanelView(self, await self._preset_list(), await self._games()))
        except discord.HTTPException as e:
            log.warning("wall start post failed: %s", e)
            return False
        await self._place(getattr(getattr(ch, "guild", None), "id", None), m)
        return True

    @tasks.loop(seconds=5)
    async def watch_loop(self) -> None:
        """Catch starts the bot didn't make itself (a vote that passed, the web page) and post their link."""
        try:
            st = await self.svc.state()
        except WallError:
            return
        if st.get("running") and (st.get("started") or 0) != self.announced:
            await self.announce(st)

    @watch_loop.before_loop
    async def _before_watch(self) -> None:
        await self.bot.wait_until_ready()

    async def post_embed(self, interaction: discord.Interaction) -> None:
        st, web, err = await self.snapshot()
        m = await interaction.followup.send(embed=build_embed(st, web, self.watch_url(web), err),
                                            view=PanelView(self, await self._preset_list(), await self._games()), wait=True)
        await self._place(interaction.guild_id, m)

    async def _place(self, guild_id: int | None, m) -> None:
        """m is now the guild's /wall embed: remember it (persisted) and retire the previous one."""
        if guild_id is None:
            return
        old = self.embeds.get(guild_id)
        self.embeds[guild_id] = (m.channel.id, m.id)
        self.last = {"guild": str(guild_id), "channel": str(m.channel.id)}
        self.save()
        if old and old[1] != m.id:   # one embed per guild: retire the previous one
            c = self.bot.get_channel(old[0])
            if c:
                try:
                    await c.get_partial_message(old[1]).edit(content="(moved — see the newest /wall message)", embed=None, view=None)
                except discord.HTTPException:
                    pass

    async def _preset_list(self) -> list[dict]:
        try:
            self._presets = await self.svc.presets()
        except WallError:
            pass
        return self._presets

    async def _games(self) -> list[dict]:
        out = []
        for lg in ("nfl", "nba"):
            try:
                out += [{**g, "league": lg} for g in await self.svc.live(lg, 4)]
            except WallError:
                pass
        return out

    @tasks.loop(seconds=30)
    async def refresh_loop(self) -> None:
        for gid in list(self.embeds):
            try:
                await self.refresh(gid)
            except Exception as e:  # noqa: BLE001 — one bad edit never kills the loop
                log.warning("wall refresh %s: %s", gid, e)

    @refresh_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    # ---- slash commands ----
    def _channel_ok(self, interaction: discord.Interaction) -> bool:
        return not CHANNEL_ID or str(interaction.channel_id) == CHANNEL_ID

    @wall.command(name="start", description="Start the wall: a preset, live games, or up to 4 URLs")
    @app_commands.describe(preset="A saved preset", live="Fill with live games", tiles="How many live games (1–4)",
                           url1="Tile 1 URL", url2="Tile 2 URL", url3="Tile 3 URL", url4="Tile 4 URL",
                           layout="Overlay layout", board="Overlay board")
    @app_commands.choices(live=[app_commands.Choice(name="NFL", value="nfl"), app_commands.Choice(name="NBA", value="nba")],
                          layout=[app_commands.Choice(name="L-bar", value="lbar"), app_commands.Choice(name="Full", value="full")],
                          board=[app_commands.Choice(name="NFL", value="nfl"), app_commands.Choice(name="NBA", value="nba"),
                                 app_commands.Choice(name="None", value="")])
    async def start(self, interaction: discord.Interaction, preset: str | None = None, live: str | None = None,
                    tiles: app_commands.Range[int, 1, 4] | None = None, url1: str | None = None, url2: str | None = None,
                    url3: str | None = None, url4: str | None = None, layout: str | None = None, board: str | None = None) -> None:
        try:
            action = start_action(preset, live, tiles, [url1, url2, url3, url4], layout, board)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self.act(interaction, action), ephemeral=True)

    @start.autocomplete("preset")
    async def _preset_ac(self, interaction: discord.Interaction, current: str):
        presets = self._presets or await self._preset_list()
        return [app_commands.Choice(name=p["name"][:100], value=p["name"][:100]) for p in presets
                if current.lower() in p["name"].lower()][:25]

    @wall.command(name="stop", description="Stop the wall")
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self.act(interaction, {"op": "stop"}), ephemeral=True)

    @wall.command(name="status", description="Post the wall panel: what's on, who's watching, the link")
    async def status(self, interaction: discord.Interaction) -> None:
        if not self._channel_ok(interaction):
            await interaction.response.send_message(f"The wall lives in <#{CHANNEL_ID}>.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.post_embed(interaction)

    @wall.command(name="set", description="Change one thing: a tile, the sound, the layout or the board")
    @app_commands.describe(tile="Tile number (1–4) for url / team / game / remove", url="A web address for the tile",
                           team="A team's stream page (e.g. nfl/bills)", game="A live game for the tile",
                           remove="Remove that tile", sound="Move the sound to this tile",
                           layout="Overlay layout", board="Overlay board (or None to hide)")
    @app_commands.choices(layout=[app_commands.Choice(name="L-bar", value="lbar"), app_commands.Choice(name="Full", value="full")],
                          board=[app_commands.Choice(name="NFL", value="nfl"), app_commands.Choice(name="NBA", value="nba"),
                                 app_commands.Choice(name="None", value="")])
    async def set_(self, interaction: discord.Interaction, tile: app_commands.Range[int, 1, 4] | None = None,
                   url: str | None = None, team: str | None = None, game: str | None = None, remove: bool = False,
                   sound: app_commands.Range[int, 1, 4] | None = None, layout: str | None = None, board: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            st = await self.svc.state()
        except WallError as e:
            await interaction.followup.send(f"Couldn't: {e}", ephemeral=True)
            return
        try:
            actions = set_actions(st, tile, url, team, game, remove, sound, layout, board)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        out = [await self.act(interaction, a) for a in actions]
        await interaction.followup.send("\n".join(out), ephemeral=True)

    @set_.autocomplete("team")
    async def _team_ac(self, interaction: discord.Interaction, current: str):
        try:
            t = await self.svc.teams()
        except WallError:
            return []
        opts = [f"{lg}/{s}" for lg in ("nfl", "nba") for s in t.get(lg, [])]
        return [app_commands.Choice(name=o, value=o) for o in opts if current.lower() in o][:25]

    @set_.autocomplete("game")
    async def _game_ac(self, interaction: discord.Interaction, current: str):
        games = await self._games()
        return [app_commands.Choice(name=f"{g['league'].upper()} · {g['label']}"[:100], value=g["url"][:100])
                for g in games if current.lower() in g["label"].lower()][:25]


def start_action(preset: str | None, live: str | None, tiles: int | None, urls: list[str | None],
                 layout: str | None, board: str | None) -> dict:
    a: dict = {"op": "start"}
    if preset:
        a["preset"] = preset
    else:
        clean = parse_urls(urls)
        if not clean and not live:   # bare /wall start: the service streams the TV's wall when it's on (else it says what to pick)
            a["mirror"] = True
            return a
        a["urls"] = clean
        if live:
            a["live"] = live
            a["tiles"] = tiles or 4
    if layout:
        a["layout"] = layout
    if board is not None:
        a["board"] = board
    return a


def set_actions(st: dict, tile: int | None, url: str | None, team: str | None, game: str | None, remove: bool,
                sound: int | None, layout: str | None, board: str | None, stream: str = "https://topstreamer.site") -> list[dict]:
    """/wall set's options → the actions to run (each a separate apply/proposal)."""
    count = len(st.get("urls") or [])
    out: list[dict] = []
    target = url or game or (f"{stream}/{team.strip('/')}" if team else None)
    if target:
        if not st.get("running"):
            raise ValueError("The wall is off: /wall start first.")
        out.append({"op": "tile", "i": tile_index(tile if tile is not None else count + 1, count), "url": clean_url(target)})
    elif remove:
        if count < 2:
            raise ValueError("Can't remove the last tile (stop the wall instead).")
        out.append({"op": "remove", "i": tile_index(tile, count, adding=False)})
    if sound is not None:
        out.append({"op": "sound", "i": tile_index(sound, count, adding=False)})
    if layout or board is not None:
        a = {"op": "board"}
        if layout:
            a["layout"] = layout
        if board is not None:
            a["board"], a["visible"] = board, bool(board)
        out.append(a)
    if not out:
        raise ValueError("Nothing to change: give a url / team / game with a tile, sound, layout or board.")
    return out


# ---- views -------------------------------------------------------------------------------------------------------------
class VoteView(ui.View):
    def __init__(self, cog: WallCog, pid: str) -> None:
        super().__init__(timeout=90)
        self.cog, self.pid = cog, pid

    async def _vote(self, interaction: discord.Interaction, yes: bool) -> None:
        try:
            r = await self.cog.web.vote(str(interaction.user.id), self.pid, yes)
            msg = "Passed — applying." if r.get("passed") else f"Vote counted ({'✓' if yes else '✗'})."
        except WallError as e:
            msg = f"Couldn't vote: {e}"
        await interaction.response.send_message(msg, ephemeral=True)
        await self.cog.refresh(interaction.guild_id)

    @ui.button(label="✓", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        await self._vote(interaction, True)

    @ui.button(label="✗", style=discord.ButtonStyle.danger)
    async def no(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        await self._vote(interaction, False)


class UrlModal(ui.Modal, title="Put a site on a tile"):
    url = ui.TextInput(label="URL", placeholder="https://… or topstreamer.site/nfl/bills", max_length=500)

    def __init__(self, cog: WallCog, i: int) -> None:
        super().__init__()
        self.cog, self.i = cog, i

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            u = clean_url(str(self.url))
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self.cog.act(interaction, {"op": "tile", "i": self.i, "url": u}), ephemeral=True)


class TeamView(ui.View):
    """Ephemeral: NFL + NBA teams in alphabetical halves (a select holds 25)."""

    def __init__(self, cog: WallCog, i: int, teams: dict) -> None:
        super().__init__(timeout=120)
        self.cog, self.i, stream = cog, i, teams.get("stream", "https://topstreamer.site")
        for lg in ("nfl", "nba"):
            for part in split_teams(teams.get(lg, [])):
                sel = ui.Select(placeholder=f"{lg.upper()} {part[0][:1].upper()}–{part[-1][:1].upper()}",
                                options=[discord.SelectOption(label=s, value=f"{stream}/{lg}/{s}") for s in part])
                sel.callback = self._make_cb(sel)
                self.add_item(sel)

    def _make_cb(self, sel: ui.Select):
        async def cb(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send(await self.cog.act(interaction, {"op": "tile", "i": self.i, "url": sel.values[0]}), ephemeral=True)
        return cb


class PanelView(ui.View):
    """Under the embed: preset, live game → tile, which tile, team / URL / sound, layout + board, start/stop, link."""

    def __init__(self, cog: WallCog, presets: list[dict], games: list[dict], persistent: bool = False) -> None:
        """persistent: the copy registered at startup (bot.add_view) that answers every posted panel by custom_id —
        values carry what they mean (a preset's name, a game's URL), so it needs no lists of its own."""
        super().__init__(timeout=None)
        self.cog = cog
        ps = ui.Select(placeholder="▶ Start: same as the TV, or a preset…", row=0, custom_id="wall:preset",
                       options=[discord.SelectOption(label="📺 Same as the TV", value=TV_CHOICE),
                                *(discord.SelectOption(label=p["name"][:100], value=p["name"][:100]) for p in presets[:24])])
        ps.callback = self._preset_cb(ps)
        self.add_item(ps)
        if games or persistent:
            gs = ui.Select(placeholder="🏈 Put a live game on the chosen tile…", row=1, custom_id="wall:game",
                           options=[discord.SelectOption(label=f"{g['league'].upper()} · {g['label']}"[:100],
                                                         value=g["url"] if len(g["url"]) <= 100 else str(i))
                                    for i, g in enumerate(games[:25])] or [discord.SelectOption(label="—", value="-")])
            gs.callback = self._game_cb(gs, games)
            self.add_item(gs)
        ts = ui.Select(placeholder="Tile for game / team / URL / sound (default: next free)", row=2, custom_id="wall:tile",
                       options=[discord.SelectOption(label=f"Tile {k}", value=str(k - 1)) for k in range(1, MAX_TILES + 1)])
        ts.callback = self._tile_cb(ts)
        self.add_item(ts)

    def _tile_of(self, interaction: discord.Interaction, count: int) -> int:
        return min(self.cog.target.get(str(interaction.user.id), count), count, MAX_TILES - 1)

    def _preset_cb(self, sel: ui.Select):
        async def cb(interaction: discord.Interaction) -> None:
            v = sel.values[0]
            await interaction.response.defer(ephemeral=True)
            action = {"op": "start", "mirror": True} if v == TV_CHOICE else {"op": "start", "preset": v}
            await interaction.followup.send(await self.cog.act(interaction, action), ephemeral=True)
        return cb

    def _game_cb(self, sel: ui.Select, games: list[dict]):
        async def cb(interaction: discord.Interaction) -> None:
            v = sel.values[0]
            if not v.startswith("http") and not (v.isdigit() and int(v) < len(games)):
                await interaction.response.send_message("That list is old — press ↻ Refresh or /wall status.", ephemeral=True)
                return
            g = {"url": v} if v.startswith("http") else games[int(v)]
            await interaction.response.defer(ephemeral=True)
            st = await self._state(interaction)
            if st is None:
                return
            if not st.get("running"):
                msg = await self.cog.act(interaction, {"op": "start", "urls": [g["url"]]})
            else:
                msg = await self.cog.act(interaction, {"op": "tile", "i": self._tile_of(interaction, len(st.get("urls") or [])), "url": g["url"]})
            await interaction.followup.send(msg, ephemeral=True)
        return cb

    def _tile_cb(self, sel: ui.Select):
        async def cb(interaction: discord.Interaction) -> None:
            self.cog.target[str(interaction.user.id)] = int(sel.values[0])
            await interaction.response.send_message(f"Tile {int(sel.values[0]) + 1} chosen for your next game / team / URL / sound.", ephemeral=True)
        return cb

    async def _state(self, interaction: discord.Interaction) -> dict | None:
        try:
            return await self.cog.svc.state()
        except WallError as e:
            await interaction.followup.send(f"Couldn't: {e}", ephemeral=True)
            return None

    @ui.button(label="Team…", style=discord.ButtonStyle.secondary, row=3, custom_id="wall:team")
    async def team(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        try:
            st, teams = await self.cog.svc.state(), await self.cog.svc.teams()
        except WallError as e:
            await interaction.response.send_message(f"Couldn't: {e}", ephemeral=True)
            return
        if not st.get("running"):
            await interaction.response.send_message("The wall is off: start a preset first.", ephemeral=True)
            return
        i = self._tile_of(interaction, len(st.get("urls") or []))
        await interaction.response.send_message(f"Team for tile {i + 1}:", view=TeamView(self.cog, i, teams), ephemeral=True)

    @ui.button(label="URL…", style=discord.ButtonStyle.secondary, row=3, custom_id="wall:url")
    async def url(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        try:
            st = await self.cog.svc.state()
        except WallError as e:
            await interaction.response.send_message(f"Couldn't: {e}", ephemeral=True)
            return
        if not st.get("running"):
            await interaction.response.send_message("The wall is off: start a preset first.", ephemeral=True)
            return
        await interaction.response.send_modal(UrlModal(self.cog, self._tile_of(interaction, len(st.get("urls") or []))))

    @ui.button(label="🔊 Sound here", style=discord.ButtonStyle.secondary, row=3, custom_id="wall:sound")
    async def sound(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        st = await self._state(interaction)
        if st is None:
            return
        n = len(st.get("urls") or [])
        i = self.cog.target.get(str(interaction.user.id))
        if i is None or i >= n:
            await interaction.followup.send("Pick a tile (1–%d) in the tile menu first." % max(n, 1), ephemeral=True)
            return
        await interaction.followup.send(await self.cog.act(interaction, {"op": "sound", "i": i}), ephemeral=True)

    @ui.button(label="Layout", style=discord.ButtonStyle.secondary, row=3, custom_id="wall:layout")
    async def layout(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        st = await self._state(interaction)
        if st is None:
            return
        cur = (st.get("overlay") or {}).get("layout") or "lbar"
        nxt = "full" if cur == "lbar" else "lbar"
        await interaction.followup.send(await self.cog.act(interaction, {"op": "board", "layout": nxt}), ephemeral=True)

    @ui.button(label="Board", style=discord.ButtonStyle.secondary, row=3, custom_id="wall:board")
    async def board(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        st = await self._state(interaction)
        if st is None:
            return
        ov = st.get("overlay") or {}
        cycle = ["", *BOARDS]
        cur = ov.get("board") if ov.get("visible") else ""
        nxt = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else ""
        await interaction.followup.send(await self.cog.act(interaction, {"op": "board", "board": nxt, "visible": bool(nxt)}), ephemeral=True)

    @ui.button(label="⏹ Stop", style=discord.ButtonStyle.danger, row=4, custom_id="wall:stop")
    async def stop(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self.cog.act(interaction, {"op": "stop"}), ephemeral=True)

    @ui.button(label="↻ Refresh", style=discord.ButtonStyle.secondary, row=4, custom_id="wall:refresh")
    async def refresh(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        await interaction.response.defer()
        await self.cog.refresh(interaction.guild_id)

    @ui.button(label="📺 Follow the TV", style=discord.ButtonStyle.secondary, row=4, custom_id="wall:follow")
    async def follow(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self.cog.act(interaction, {"op": "follow"}), ephemeral=True)

    @ui.button(label="🔗 Get my link", style=discord.ButtonStyle.primary, row=4, custom_id="wall:link")
    async def link(self, interaction: discord.Interaction, _b: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await link_message(self.cog.web, interaction.user), ephemeral=True)


async def link_message(web: WallWeb, user) -> str:
    """The ephemeral reply to Get my link: a personal 10-minute login link, or how to sign in otherwise."""
    try:
        url = await web.link(user_dict(user))
    except WallError as e:
        return (f"Couldn't make your link ({e}). Log in at {web.watch_url} with Discord — your SharpLab account is "
                "your Discord account in this server; if it says you're not a member, join the server first.")
    # <…>: no link preview. Discord's unfurler would GET the link first and spend it (it's single-use).
    return f"Your watch link (valid 10 min, one use, just for you — don't share it): <{url}>"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WallCog(bot))
