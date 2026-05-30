"""One-shot: post the SharpLab self-assign role panels and register their
emoji→role bindings. Run once on the VPS after deploying the reactionroles cog,
then restart the bot so it loads the new panel ids.

    python -m temporal.setup_reaction_roles            # safe: skips if already set up
    python -m temporal.setup_reaction_roles --force    # post anyway (may duplicate)
"""
from __future__ import annotations

import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

from db import schema, queries

load_dotenv("/opt/sharplab/.env")

CHANNEL_ID = 1510113941148405780

# (title, description, [(emoji, role_id, label)])
PANELS = [
    ("\U0001f4cb Sports Leagues", "React to follow a league:", [
        ("\U0001f3c0", "1510114648358256730", "NBA"),
        ("⚾", "1510114684999565362", "MLB"),
        ("\U0001f3c8", "1510114702565445904", "NFL"),
        ("\U0001f3df️", "1510114720101695498", "CFB"),
        ("\U0001f393", "1510114739861323776", "CBB"),
    ]),
    ("\U0001f3a8 Name Color", "React to pick a color:", [
        ("\U0001f7e2", "1510114788338827364", "green"),
        ("\U0001f7e3", "1510114847864520744", "purple"),
        ("\U0001f535", "1510114863479783474", "blue"),
        ("\U0001f7e1", "1510114902705049761", "yellow"),
    ]),
    ("\U0001f514 Interests & Pings", "React for access / notifications:", [
        ("\U0001f389", "1510115036050231296", "Watch Party"),
        ("\U0001f3ae", "1510115083735535709", "Bot Games"),
        ("\U0001f4c8", "1510115118652981358", "Rohan Stock Picks"),
        ("\U0001f52c", "1510115215038218332", "Science Bowl"),
    ]),
]


async def main(force: bool) -> None:
    await schema.init_db()
    existing = await queries.get_reaction_role_message_ids()
    if existing and not force:
        print(f"Already set up ({len(existing)} panel(s)). Use --force to post again.")
        return

    token = os.environ["DISCORD_BOT_TOKEN"]
    guild_id = os.environ["DISCORD_GUILD_ID"].split(",")[0]
    client = discord.Client(intents=discord.Intents.default())

    @client.event
    async def on_ready():
        try:
            channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
            for title, desc, binds in PANELS:
                body = desc + "\n\n" + "\n".join(f"{e} — **{label}**" for e, _, label in binds)
                msg = await channel.send(embed=discord.Embed(
                    title=title, description=body, colour=0x5865F2))
                for emoji, role_id, _ in binds:
                    await msg.add_reaction(emoji)
                    await queries.add_reaction_role(
                        str(msg.id), emoji, role_id, guild_id, str(CHANNEL_ID))
                print(f"posted {title!r} ({len(binds)} roles) -> message {msg.id}")
            print("done. Restart the bot so it loads the new panels.")
        finally:
            await client.close()

    await client.start(token)


if __name__ == "__main__":
    asyncio.run(main("--force" in sys.argv))
