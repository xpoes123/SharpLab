# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-04-21 19:48 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Temporal workflows should guard against replay of partial side effects (confidence: 16)
- ValueError from max() on empty generators goes unhandled (confidence: 15)
- Same bug pattern exists in /odds and /best-line endpoints (confidence: 13)
- Live data source exclusions may filter out entire dataset (confidence: 11)
- Discord API calls should be idempotent or preceded by state checks (confidence: 11)
- Don't ignore setuptools flat-layout error—it prevents wrong installs (confidence: 11)
- Wildcard include may need tuning if subpackages shouldn't be included (confidence: 11)
- Remember to test 'pip install -e .' after config changes, not just setup (confidence: 11)
- Avoid src-layout migration if it blocks other cross-project imports (confidence: 11)
- Validate line data completeness before processing (confidence: 10)

## Conventions & Preferences

- Use try-except with fallback when filtering empty sequences (confidence: 13)
- Document which dirs are packages vs data/docs in discovery config (confidence: 12)
- Consider transaction-like patterns for multi-step operations (confidence: 12)
- Document why fallback behavior is acceptable in code comments (confidence: 11)
- Document intentional trade-offs when reordering state updates and side effects (confidence: 11)
- Commit DB state before sending Discord messages when possible (confidence: 11)
- Prefer explicit package inclusion over src-layout when refactoring costly (confidence: 11)
- Use pyproject.toml [tool.setuptools] over legacy setup.py for modern projects (confidence: 11)
- Exclude non-package dirs (data/, docs/) from setuptools discovery (confidence: 11)
- Test edge cases where filters exclude all data sources (confidence: 10)

## Learned Patterns

- Empty sequence check before max()/min() on generators (confidence: 16)
- Async crash windows between message send and state persistence (confidence: 15)
- Copy-pasted error handling in similar code paths (confidence: 15)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 13)
- Flat-layout multi-package projects need explicit setuptools config (confidence: 12)
- Use [tool.setuptools.packages.find] with include/exclude filters (confidence: 11)
- Wildcard patterns (package*) match package and subpackages (confidence: 11)
- Test with 'pip install -e .' to verify package discovery works (confidence: 11)
- Multiple top-level dirs = setuptools ambiguity guard triggers (confidence: 11)
- Periodic polling with configurable intervals for sports data (confidence: 9)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 8)
- Threshold-based detection for significant value movements (confidence: 8)
- Discord webhook integration for real-time notifications (confidence: 8)
- Database snapshots for historical tracking and analysis (confidence: 8)
- Modular configuration system for dynamic thresholds (confidence: 8)
