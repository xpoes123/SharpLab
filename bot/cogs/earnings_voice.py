"""Stream a company's LIVE earnings call into a Discord voice channel on request.

Audio source: the EarningsCall live-streaming API — `GET /live-events` returns the
calls streaming right now, each with an HLS (.m3u8) `streamingUrl`. FFmpeg plays
that straight into the voice channel. With no key configured the `demo` key exposes
a sample stream, so the whole path works out of the box for testing.

Env:
  EARNINGSCALL_API_KEY   — your key (defaults to "demo": sample stream only)
  EARNINGSCALL_LIVE_URL  — override the endpoint (defaults to prod)
"""
import logging
import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

LIVE_EVENTS_URL = os.getenv("EARNINGSCALL_LIVE_URL", "https://prod.earningscall.dev/live-events")
API_KEY = os.getenv("EARNINGSCALL_API_KEY", "demo")
# Always stream into this dedicated voice channel, regardless of where the caller is.
EARNINGS_VC_ID = int(os.getenv("EARNINGS_VOICE_CHANNEL_ID", "1477512816863740093"))
COLOUR = 0x1ABC9C

# Live HLS can hiccup — reconnect rather than drop the call. No video.
_FFMPEG_BEFORE = "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
_FFMPEG_OPTS = "-vn"


async def _fetch_live_events() -> list[dict]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(LIVE_EVENTS_URL, params={"apikey": API_KEY})
        r.raise_for_status()
        return r.json().get("events", [])


def _label(e: dict) -> str:
    return f"**{e.get('symbol', '?')}** — {e.get('companyName', 'Unknown')} (Q{e.get('quarter', '?')} {e.get('year', '')})"


class EarningsVoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    earnings = app_commands.Group(name="earnings", description="Live earnings-call audio")

    @earnings.command(name="live", description="List the earnings calls streaming right now")
    async def live(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            events = await _fetch_live_events()
        except Exception:
            log.exception("earnings: live-events fetch failed")
            await interaction.followup.send("Couldn't reach the earnings-call feed.", ephemeral=True)
            return
        if not events:
            await interaction.followup.send("No earnings calls are live right now.", ephemeral=True)
            return
        body = "\n".join(f"🎙️ {_label(e)}" for e in events)
        await interaction.followup.send(f"**Live earnings calls**\n{body}\n\n`/earnings listen <ticker>` to tune in.", ephemeral=True)

    @earnings.command(name="listen", description="Stream a company's live earnings call into your voice channel")
    @app_commands.describe(ticker="Ticker symbol, e.g. AAPL")
    async def listen(self, interaction: discord.Interaction, ticker: str) -> None:
        await interaction.response.defer()
        channel = interaction.guild.get_channel(EARNINGS_VC_ID) if interaction.guild else None
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            await interaction.followup.send("The earnings voice channel isn't set up correctly.", ephemeral=True)
            return

        sym = ticker.strip().upper()
        try:
            events = await _fetch_live_events()
        except Exception:
            log.exception("earnings: live-events fetch failed")
            await interaction.followup.send("Couldn't reach the earnings-call feed.", ephemeral=True)
            return

        ev = next((e for e in events if e.get("symbol", "").upper() == sym), None)
        if ev is None:
            live = ", ".join(e.get("symbol", "?") for e in events) or "nothing right now"
            await interaction.followup.send(
                f"**{sym}** isn't streaming live. Currently live: {live}.", ephemeral=True)
            return
        url = ev.get("streamingUrl")
        if not url:
            await interaction.followup.send(f"No stream URL available for {sym} yet.", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        try:
            if vc is None:
                vc = await channel.connect()
            elif vc.channel.id != channel.id:
                await vc.move_to(channel)
        except discord.ClientException:
            await interaction.followup.send("I'm already streaming in another voice channel — `/earnings stop` first.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send(f"I don't have permission to join **{channel.name}**.", ephemeral=True)
            return

        if vc.is_playing():
            vc.stop()
        source = discord.FFmpegPCMAudio(url, before_options=_FFMPEG_BEFORE, options=_FFMPEG_OPTS)
        vc.play(source, after=lambda err: log.error("earnings: stream error: %s", err) if err else None)

        embed = discord.Embed(
            title=f"🎙️ {ev.get('symbol')} — {ev.get('companyName')}",
            description=(f"Q{ev.get('quarter')} {ev.get('year')} earnings call · live in **{channel.name}**\n"
                        "`/earnings stop` to end."),
            colour=COLOUR,
        )
        await interaction.followup.send(embed=embed)

    @earnings.command(name="stop", description="Stop the earnings-call stream and leave the voice channel")
    async def stop(self, interaction: discord.Interaction) -> None:
        vc = interaction.guild.voice_client
        if vc is None:
            await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)
            return
        await vc.disconnect(force=True)
        await interaction.response.send_message("⏹️ Stopped the earnings call and left the channel.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EarningsVoiceCog(bot))
