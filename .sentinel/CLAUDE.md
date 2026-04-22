# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-04-22 18:30 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Temporal workflows should guard against replay of partial side effects (confidence: 35)
- Task loops swallow exceptions silently—unhandled errors kill loops permanently (confidence: 32)
- Validate line data completeness before processing (confidence: 31)
- Live data source exclusions may filter out entire dataset (confidence: 27)
- Discord API calls should be idempotent or preceded by state checks (confidence: 26)
- Don't ignore setuptools flat-layout error—it prevents wrong installs (confidence: 26)
- Test with realistic data volumes; unit tests may miss batch edge cases (confidence: 25)
- Rate-limit API calls to avoid odds provider blocking (confidence: 24)
- Handle timezone differences in game timestamps carefully (confidence: 24)
- ValueError from max() on empty generators goes unhandled (confidence: 24)

## Conventions & Preferences

- Use try-except with fallback when filtering empty sequences (confidence: 29)
- Document which dirs are packages vs data/docs in discovery config (confidence: 29)
- Document intentional trade-offs when reordering state updates and side effects (confidence: 29)
- Prioritize silent failures over duplicate notifications in bot integrations (confidence: 27)
- Document why fallback behavior is acceptable in code comments (confidence: 27)
- Extract repeated filter patterns into reusable utility functions (confidence: 25)
- Use Discord embeds for rich, scannable alert formatting (confidence: 24)
- Provide user feedback when data is incomplete or pending (confidence: 24)
- Consider transaction-like patterns for multi-step operations (confidence: 22)
- Check orchestration/startup files early in debugging (confidence: 22)

## Learned Patterns

- Copy-pasted error handling in similar code paths (confidence: 38)
- Async crash windows between message send and state persistence (confidence: 32)
- Empty sequence check before max()/min() on generators (confidence: 32)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 28)
- Flat-layout multi-package projects need explicit setuptools config (confidence: 26)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 25)
- Sport-agnostic code needs all sport configs in startup (confidence: 24)
- Stage data into collections before mutating embeds to enable slicing (confidence: 24)
- Discord embed field limit (25) requires pre-validation before send() (confidence: 24)
- Periodic polling with configurable intervals for sports data (confidence: 24)
- Chunk overflow data into continuation messages with consistent naming (confidence: 23)
- Threshold-based detection for significant value movements (confidence: 20)
- Temporal workflows require explicit start commands per sport (confidence: 20)
- Update constant values and docstrings together (confidence: 19)
- Use meaningful constant names instead of magic numbers (confidence: 18)
