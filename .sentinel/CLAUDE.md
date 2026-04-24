# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-04-24 18:50 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Task loops swallow exceptions silently—unhandled errors kill loops permanently (confidence: 46)
- Temporal workflows should guard against replay of partial side effects (confidence: 44)
- Validate line data completeness before processing (confidence: 37)
- Test with realistic data volumes; unit tests may miss batch edge cases (confidence: 32)
- Discord API calls should be idempotent or preceded by state checks (confidence: 31)
- Live data source exclusions may filter out entire dataset (confidence: 31)
- Don't ignore setuptools flat-layout error—it prevents wrong installs (confidence: 30)
- ValueError from max() on empty generators goes unhandled (confidence: 29)
- Rate-limit API calls to avoid odds provider blocking (confidence: 29)
- Generic code requires explicit per-instance startup calls (confidence: 27)

## Conventions & Preferences

- Document intentional trade-offs when reordering state updates and side effects (confidence: 35)
- Prioritize silent failures over duplicate notifications in bot integrations (confidence: 34)
- Document why fallback behavior is acceptable in code comments (confidence: 33)
- Use try-except with fallback when filtering empty sequences (confidence: 33)
- Document which dirs are packages vs data/docs in discovery config (confidence: 33)
- Use Discord embeds for rich, scannable alert formatting (confidence: 29)
- Extract repeated filter patterns into reusable utility functions (confidence: 29)
- Consider transaction-like patterns for multi-step operations (confidence: 28)
- Provide user feedback when data is incomplete or pending (confidence: 28)
- Check orchestration/startup files early in debugging (confidence: 26)

## Learned Patterns

- Copy-pasted error handling in similar code paths (confidence: 42)
- Async crash windows between message send and state persistence (confidence: 41)
- Empty sequence check before max()/min() on generators (confidence: 36)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 33)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 32)
- Stage data into collections before mutating embeds to enable slicing (confidence: 30)
- Discord embed field limit (25) requires pre-validation before send() (confidence: 29)
- Sport-agnostic code needs all sport configs in startup (confidence: 28)
- Chunk overflow data into continuation messages with consistent naming (confidence: 28)
- Flat-layout multi-package projects need explicit setuptools config (confidence: 26)
- Threshold-based detection for significant value movements (confidence: 25)
- Periodic polling with configurable intervals for sports data (confidence: 25)
- Update constant values and docstrings together (confidence: 23)
- Temporal workflows require explicit start commands per sport (confidence: 22)
- Database snapshots for historical tracking and analysis (confidence: 22)
