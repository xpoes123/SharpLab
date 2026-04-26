# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-04-26 00:29 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Task loops swallow exceptions silently—unhandled errors kill loops permanently (confidence: 72)
- Temporal workflows should guard against replay of partial side effects (confidence: 61)
- Validate line data completeness before processing (confidence: 48)
- Discord API calls should be idempotent or preceded by state checks (confidence: 44)
- Test with realistic data volumes; unit tests may miss batch edge cases (confidence: 43)
- Generic code requires explicit per-instance startup calls (confidence: 39)
- Race condition: bot restart between send() and DB mark causes dupes (confidence: 38)
- Rate-limit API calls to avoid odds provider blocking (confidence: 37)
- Don't mutate embeds mid-loop without prior bounds checking (confidence: 37)
- Test both self-view and other-user-view paths thoroughly (confidence: 32)

## Conventions & Preferences

- Document intentional trade-offs when reordering state updates and side effects (confidence: 50)
- Prioritize silent failures over duplicate notifications in bot integrations (confidence: 46)
- Document why fallback behavior is acceptable in code comments (confidence: 44)
- Document which dirs are packages vs data/docs in discovery config (confidence: 41)
- Extract repeated filter patterns into reusable utility functions (confidence: 40)
- Consider transaction-like patterns for multi-step operations (confidence: 38)
- Use Discord embeds for rich, scannable alert formatting (confidence: 38)
- Scan for duplicated code blocks during review (confidence: 34)
- Use try-except with fallback when filtering empty sequences (confidence: 32)
- Test edge cases where filters exclude all data sources (confidence: 30)

## Learned Patterns

- Async crash windows between message send and state persistence (confidence: 62)
- Copy-pasted error handling in similar code paths (confidence: 61)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 45)
- Empty sequence check before max()/min() on generators (confidence: 42)
- Sport-agnostic code needs all sport configs in startup (confidence: 40)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 39)
- Chunk overflow data into continuation messages with consistent naming (confidence: 38)
- Database snapshots for historical tracking and analysis (confidence: 31)
- Stage data into collections before mutating embeds to enable slicing (confidence: 30)
- Discord embed field limit (25) requires pre-validation before send() (confidence: 28)
- Flat-layout multi-package projects need explicit setuptools config (confidence: 26)
- Threshold-based detection for significant value movements (confidence: 25)
- Periodic polling with configurable intervals for sports data (confidence: 25)
- Update constant values and docstrings together (confidence: 23)
- Temporal workflows require explicit start commands per sport (confidence: 22)
