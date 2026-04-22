# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-04-22 21:02 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Temporal workflows should guard against replay of partial side effects (confidence: 37)
- Task loops swallow exceptions silently—unhandled errors kill loops permanently (confidence: 36)
- Validate line data completeness before processing (confidence: 34)
- Discord API calls should be idempotent or preceded by state checks (confidence: 29)
- Live data source exclusions may filter out entire dataset (confidence: 29)
- Test with realistic data volumes; unit tests may miss batch edge cases (confidence: 28)
- Don't ignore setuptools flat-layout error—it prevents wrong installs (confidence: 28)
- ValueError from max() on empty generators goes unhandled (confidence: 27)
- Rate-limit API calls to avoid odds provider blocking (confidence: 26)
- Handle timezone differences in game timestamps carefully (confidence: 25)

## Conventions & Preferences

- Use try-except with fallback when filtering empty sequences (confidence: 31)
- Document which dirs are packages vs data/docs in discovery config (confidence: 31)
- Document intentional trade-offs when reordering state updates and side effects (confidence: 31)
- Prioritize silent failures over duplicate notifications in bot integrations (confidence: 29)
- Document why fallback behavior is acceptable in code comments (confidence: 29)
- Use Discord embeds for rich, scannable alert formatting (confidence: 27)
- Extract repeated filter patterns into reusable utility functions (confidence: 27)
- Consider transaction-like patterns for multi-step operations (confidence: 26)
- Provide user feedback when data is incomplete or pending (confidence: 26)
- Check orchestration/startup files early in debugging (confidence: 24)

## Learned Patterns

- Copy-pasted error handling in similar code paths (confidence: 40)
- Async crash windows between message send and state persistence (confidence: 35)
- Empty sequence check before max()/min() on generators (confidence: 34)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 30)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 28)
- Stage data into collections before mutating embeds to enable slicing (confidence: 28)
- Sport-agnostic code needs all sport configs in startup (confidence: 26)
- Discord embed field limit (25) requires pre-validation before send() (confidence: 26)
- Flat-layout multi-package projects need explicit setuptools config (confidence: 26)
- Chunk overflow data into continuation messages with consistent naming (confidence: 25)
- Periodic polling with configurable intervals for sports data (confidence: 25)
- Threshold-based detection for significant value movements (confidence: 22)
- Update constant values and docstrings together (confidence: 21)
- Temporal workflows require explicit start commands per sport (confidence: 20)
- Database snapshots for historical tracking and analysis (confidence: 20)
