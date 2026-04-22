# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-04-22 04:46 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Temporal workflows should guard against replay of partial side effects (confidence: 23)
- ValueError from max() on empty generators goes unhandled (confidence: 20)
- Validate line data completeness before processing (confidence: 18)
- Same bug pattern exists in /odds and /best-line endpoints (confidence: 18)
- Task loops swallow exceptions silently—unhandled errors kill loops permanently (confidence: 16)
- Live data source exclusions may filter out entire dataset (confidence: 16)
- Discord API calls should be idempotent or preceded by state checks (confidence: 16)
- Rate-limit API calls to avoid odds provider blocking (confidence: 14)
- Handle timezone differences in game timestamps carefully (confidence: 14)
- Don't ignore setuptools flat-layout error—it prevents wrong installs (confidence: 14)

## Conventions & Preferences

- Document which dirs are packages vs data/docs in discovery config (confidence: 18)
- Document intentional trade-offs when reordering state updates and side effects (confidence: 18)
- Use try-except with fallback when filtering empty sequences (confidence: 18)
- Consider transaction-like patterns for multi-step operations (confidence: 17)
- Document why fallback behavior is acceptable in code comments (confidence: 16)
- Commit DB state before sending Discord messages when possible (confidence: 16)
- Extract repeated filter patterns into reusable utility functions (confidence: 15)
- Test edge cases where filters exclude all data sources (confidence: 15)
- Prefer list comprehension over generator when fallback needed (confidence: 15)
- Prioritize silent failures over duplicate notifications in bot integrations (confidence: 14)

## Learned Patterns

- Copy-pasted error handling in similar code paths (confidence: 24)
- Empty sequence check before max()/min() on generators (confidence: 22)
- Async crash windows between message send and state persistence (confidence: 20)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 18)
- Flat-layout multi-package projects need explicit setuptools config (confidence: 17)
- Discord embed field limit (25) requires pre-validation before send() (confidence: 14)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 14)
- Periodic polling with configurable intervals for sports data (confidence: 14)
- Use [tool.setuptools.packages.find] with include/exclude filters (confidence: 14)
- Wildcard patterns (package*) match package and subpackages (confidence: 14)
- Test with 'pip install -e .' to verify package discovery works (confidence: 14)
- Multiple top-level dirs = setuptools ambiguity guard triggers (confidence: 14)
- Chunk overflow data into continuation messages with consistent naming (confidence: 13)
- Stage data into collections before mutating embeds to enable slicing (confidence: 13)
- Threshold-based detection for significant value movements (confidence: 13)
