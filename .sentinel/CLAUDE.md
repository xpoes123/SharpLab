# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-05-02 20:11 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Task loops swallow exceptions silently—unhandled errors kill loops permanently (confidence: 143)
- Temporal workflows should guard against replay of partial side effects (confidence: 108)
- Validate line data completeness before processing (confidence: 80)
- Test with realistic data volumes; unit tests may miss batch edge cases (confidence: 76)
- Don't mutate embeds mid-loop without prior bounds checking (confidence: 59)
- Discord API calls should be idempotent or preceded by state checks (confidence: 58)
- Race condition: bot restart between send() and DB mark causes dupes (confidence: 56)
- Generic code requires explicit per-instance startup calls (confidence: 54)
- Avoid assuming code is broken if infrastructure exists (confidence: 53)
- Same bug pattern exists in /odds and /best-line endpoints (confidence: 52)

## Conventions & Preferences

- Document intentional trade-offs when reordering state updates and side effects (confidence: 89)
- Consider transaction-like patterns for multi-step operations (confidence: 67)
- Test edge cases where filters exclude all data sources (confidence: 64)
- Extract repeated filter patterns into reusable utility functions (confidence: 63)
- Scan for duplicated code blocks during review (confidence: 61)
- Use Discord embeds for rich, scannable alert formatting (confidence: 60)
- Document why fallback behavior is acceptable in code comments (confidence: 59)
- Use try-except with fallback when filtering empty sequences (confidence: 51)
- Document which dirs are packages vs data/docs in discovery config (confidence: 48)
- Provide user feedback when data is incomplete or pending (confidence: 47)

## Learned Patterns

- Async crash windows between message send and state persistence (confidence: 115)
- Copy-pasted error handling in similar code paths (confidence: 108)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 69)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 63)
- Chunk overflow data into continuation messages with consistent naming (confidence: 54)
- Stage data into collections before mutating embeds to enable slicing (confidence: 52)
- Discord embed field limit (25) requires pre-validation before send() (confidence: 49)
- Database snapshots for historical tracking and analysis (confidence: 46)
- Update constant values and docstrings together (confidence: 39)
- Empty sequence check before max()/min() on generators (confidence: 41)
- Sport-agnostic code needs all sport configs in startup (confidence: 40)
- Periodic polling with configurable intervals for sports data (confidence: 35)
- Call coin-awarding logic immediately after defer() for consistency (confidence: 35)
- Change user-facing messages when altering reward intervals (confidence: 33)
- Distinguish self vs other users with is_self check for conditional logic (confidence: 32)
