# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-04-22 04:46 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Temporal workflows should guard against replay of partial side effects (confidence: 24)
- ValueError from max() on empty generators goes unhandled (confidence: 21)
- Validate line data completeness before processing (confidence: 19)
- Same bug pattern exists in /odds and /best-line endpoints (confidence: 19)
- Task loops swallow exceptions silently—unhandled errors kill loops permanently (confidence: 17)
- Live data source exclusions may filter out entire dataset (confidence: 17)
- Discord API calls should be idempotent or preceded by state checks (confidence: 17)
- Don't ignore setuptools flat-layout error—it prevents wrong installs (confidence: 15)
- Rate-limit API calls to avoid odds provider blocking (confidence: 15)
- Handle timezone differences in game timestamps carefully (confidence: 15)

## Conventions & Preferences

- Document intentional trade-offs when reordering state updates and side effects (confidence: 20)
- Document which dirs are packages vs data/docs in discovery config (confidence: 19)
- Use try-except with fallback when filtering empty sequences (confidence: 19)
- Consider transaction-like patterns for multi-step operations (confidence: 18)
- Document why fallback behavior is acceptable in code comments (confidence: 17)
- Commit DB state before sending Discord messages when possible (confidence: 17)
- Extract repeated filter patterns into reusable utility functions (confidence: 16)
- Test edge cases where filters exclude all data sources (confidence: 16)
- Prefer list comprehension over generator when fallback needed (confidence: 16)
- Prioritize silent failures over duplicate notifications in bot integrations (confidence: 15)

## Learned Patterns

- Copy-pasted error handling in similar code paths (confidence: 25)
- Empty sequence check before max()/min() on generators (confidence: 23)
- Async crash windows between message send and state persistence (confidence: 21)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 19)
- Flat-layout multi-package projects need explicit setuptools config (confidence: 18)
- Discord embed field limit (25) requires pre-validation before send() (confidence: 15)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 15)
- Periodic polling with configurable intervals for sports data (confidence: 15)
- Chunk overflow data into continuation messages with consistent naming (confidence: 14)
- Stage data into collections before mutating embeds to enable slicing (confidence: 14)
- Threshold-based detection for significant value movements (confidence: 14)
- Discord webhook integration for real-time notifications (confidence: 14)
- Database snapshots for historical tracking and analysis (confidence: 14)
- Modular configuration system for dynamic thresholds (confidence: 14)
- Use [tool.setuptools.packages.find] with include/exclude filters (confidence: 14)
