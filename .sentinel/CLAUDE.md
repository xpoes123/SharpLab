# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-05-01 07:06 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Task loops swallow exceptions silently—unhandled errors kill loops permanently (confidence: 127)
- Temporal workflows should guard against replay of partial side effects (confidence: 89)
- Validate line data completeness before processing (confidence: 67)
- Test with realistic data volumes; unit tests may miss batch edge cases (confidence: 65)
- Don't mutate embeds mid-loop without prior bounds checking (confidence: 52)
- Discord API calls should be idempotent or preceded by state checks (confidence: 51)
- Race condition: bot restart between send() and DB mark causes dupes (confidence: 50)
- Generic code requires explicit per-instance startup calls (confidence: 48)
- Rate-limit API calls to avoid odds provider blocking (confidence: 44)
- Avoid assuming code is broken if infrastructure exists (confidence: 42)

## Conventions & Preferences

- Document intentional trade-offs when reordering state updates and side effects (confidence: 77)
- Consider transaction-like patterns for multi-step operations (confidence: 59)
- Use Discord embeds for rich, scannable alert formatting (confidence: 54)
- Extract repeated filter patterns into reusable utility functions (confidence: 54)
- Document why fallback behavior is acceptable in code comments (confidence: 51)
- Test edge cases where filters exclude all data sources (confidence: 50)
- Scan for duplicated code blocks during review (confidence: 48)
- Prioritize silent failures over duplicate notifications in bot integrations (confidence: 46)
- Document which dirs are packages vs data/docs in discovery config (confidence: 42)
- Use try-except with fallback when filtering empty sequences (confidence: 41)

## Learned Patterns

- Async crash windows between message send and state persistence (confidence: 105)
- Copy-pasted error handling in similar code paths (confidence: 93)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 60)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 53)
- Chunk overflow data into continuation messages with consistent naming (confidence: 48)
- Stage data into collections before mutating embeds to enable slicing (confidence: 45)
- Discord embed field limit (25) requires pre-validation before send() (confidence: 42)
- Database snapshots for historical tracking and analysis (confidence: 39)
- Empty sequence check before max()/min() on generators (confidence: 41)
- Sport-agnostic code needs all sport configs in startup (confidence: 40)
- Update constant values and docstrings together (confidence: 33)
- Periodic polling with configurable intervals for sports data (confidence: 29)
- Call coin-awarding logic immediately after defer() for consistency (confidence: 29)
- Change user-facing messages when altering reward intervals (confidence: 27)
- Distinguish self vs other users with is_self check for conditional logic (confidence: 25)
