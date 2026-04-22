# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-04-22 22:02 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Temporal workflows should guard against replay of partial side effects (confidence: 41)
- Task loops swallow exceptions silently—unhandled errors kill loops permanently (confidence: 40)
- Validate line data completeness before processing (confidence: 38)
- Test with realistic data volumes; unit tests may miss batch edge cases (confidence: 32)
- Discord API calls should be idempotent or preceded by state checks (confidence: 32)
- Live data source exclusions may filter out entire dataset (confidence: 32)
- Don't ignore setuptools flat-layout error—it prevents wrong installs (confidence: 31)
- ValueError from max() on empty generators goes unhandled (confidence: 30)
- Rate-limit API calls to avoid odds provider blocking (confidence: 29)
- Generic code requires explicit per-instance startup calls (confidence: 27)

## Conventions & Preferences

- Document intentional trade-offs when reordering state updates and side effects (confidence: 35)
- Use try-except with fallback when filtering empty sequences (confidence: 34)
- Document which dirs are packages vs data/docs in discovery config (confidence: 34)
- Prioritize silent failures over duplicate notifications in bot integrations (confidence: 33)
- Document why fallback behavior is acceptable in code comments (confidence: 32)
- Use Discord embeds for rich, scannable alert formatting (confidence: 30)
- Extract repeated filter patterns into reusable utility functions (confidence: 30)
- Consider transaction-like patterns for multi-step operations (confidence: 29)
- Provide user feedback when data is incomplete or pending (confidence: 29)
- Check orchestration/startup files early in debugging (confidence: 27)

## Learned Patterns

- Copy-pasted error handling in similar code paths (confidence: 43)
- Async crash windows between message send and state persistence (confidence: 38)
- Empty sequence check before max()/min() on generators (confidence: 37)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 33)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 32)
- Stage data into collections before mutating embeds to enable slicing (confidence: 31)
- Sport-agnostic code needs all sport configs in startup (confidence: 29)
- Discord embed field limit (25) requires pre-validation before send() (confidence: 29)
- Chunk overflow data into continuation messages with consistent naming (confidence: 28)
- Threshold-based detection for significant value movements (confidence: 26)
- Flat-layout multi-package projects need explicit setuptools config (confidence: 26)
- Periodic polling with configurable intervals for sports data (confidence: 25)
- Update constant values and docstrings together (confidence: 24)
- Temporal workflows require explicit start commands per sport (confidence: 23)
- Database snapshots for historical tracking and analysis (confidence: 23)
