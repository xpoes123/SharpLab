# Debug Discord Interaction Issues

This skill codifies the 8+ recurring Discord.py bugs found in git history. Run through these checks when a Discord command or game interaction is broken.

## Common Pitfalls (ordered by frequency)

### 1. Interaction deferral timing
The #1 recurring bug. Discord gives you 3 seconds to respond to an interaction.

**Rule**: If ANY work happens before responding (DB query, HTTP call, computation), you MUST defer first.
```python
await interaction.response.defer(ephemeral=True)  # for slash commands
# ... do work ...
await interaction.followup.send(embed=embed)
```

**But**: Don't defer if you're about to show a modal — `interaction.response.send_modal()` IS the response.

**Anti-pattern** (caused 3 fix commits):
```python
# WRONG: defer then try to send_modal — can't respond twice
await interaction.response.defer()
await interaction.response.send_modal(modal)  # FAILS: already responded
```

### 2. Modal field limits
Discord enforces hard limits that aren't obvious until runtime:
- **Label**: max 45 characters (caused commit 6eb26e4)
- **Value/placeholder**: max 100 characters
- **Fields per modal**: max 5

**Always check**: `len(label) <= 45` before creating a `TextInput`.

### 3. Button/emoji validation
Discord rejects invalid emoji in button labels silently or with cryptic errors.
- Standard Unicode emoji: ✅ works
- Custom emoji: needs `<:name:id>` format from your guild
- **No emoji + text combos that exceed 80 chars**
- Commit 10aa749: used an invalid emoji for Join button — test buttons locally first

### 4. View/button timeout
Default timeout is 180s. After that, buttons go dead with no error.
```python
view = MyView(timeout=300)  # explicit timeout
# Always handle on_timeout:
async def on_timeout(self):
    for item in self.children:
        item.disabled = True
    await self.message.edit(view=self)
```

### 5. Thread-based game sessions
Thread games have caused 15+ fix commits. Key patterns:

**Always use try/finally for cleanup**:
```python
try:
    thread = await channel.create_thread(...)
    # ... game logic ...
finally:
    sessions.pop(thread.id, None)  # prevent session leaks
```

**Race condition on join**: Multiple users clicking Join simultaneously. Use a lock:
```python
if game_id in self._join_locks:
    return
self._join_locks.add(game_id)
try:
    # process join
finally:
    self._join_locks.discard(game_id)
```

### 6. Followup vs response
- `interaction.response.send_message()` — first response only, within 3s
- `interaction.followup.send()` — after defer or first response, within 15 min
- `interaction.edit_original_response()` — edit the deferred "thinking" message

**Common mistake**: calling `.response.send_message()` after already deferring → "interaction already responded"

### 7. Autocomplete errors are silent
If your autocomplete callback raises an exception, Discord shows... nothing. No error, no choices.
- Always wrap autocomplete in try/except that returns `[]` on failure
- Log the error so you can debug it

### 8. Guild sync vs global sync
- `tree.sync(guild=guild)` — instant, use for development
- `tree.sync()` — takes up to 1 hour, use for production only if needed
- If commands aren't appearing: check you're syncing to the right guild
