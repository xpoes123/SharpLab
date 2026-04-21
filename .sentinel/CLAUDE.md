# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-04-21 18:53 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Temporal workflows should guard against replay of partial side effects (confidence: 15)
- ValueError from max() on empty generators goes unhandled (confidence: 14)
- Same bug pattern exists in /odds and /best-line endpoints (confidence: 12)
- Live data source exclusions may filter out entire dataset (confidence: 10)
- Discord API calls should be idempotent or preceded by state checks (confidence: 10)
- Don't ignore setuptools flat-layout error—it prevents wrong installs (confidence: 10)
- Wildcard include may need tuning if subpackages shouldn't be included (confidence: 10)
- Remember to test 'pip install -e .' after config changes, not just setup (confidence: 10)
- Avoid src-layout migration if it blocks other cross-project imports (confidence: 10)
- Validate line data completeness before processing (confidence: 9)

## Conventions & Preferences

- Use try-except with fallback when filtering empty sequences (confidence: 12)
- Document which dirs are packages vs data/docs in discovery config (confidence: 11)
- Consider transaction-like patterns for multi-step operations (confidence: 11)
- Document why fallback behavior is acceptable in code comments (confidence: 10)
- Document intentional trade-offs when reordering state updates and side effects (confidence: 10)
- Commit DB state before sending Discord messages when possible (confidence: 10)
- Prefer explicit package inclusion over src-layout when refactoring costly (confidence: 10)
- Use pyproject.toml [tool.setuptools] over legacy setup.py for modern projects (confidence: 10)
- Exclude non-package dirs (data/, docs/) from setuptools discovery (confidence: 10)
- Test edge cases where filters exclude all data sources (confidence: 9)

## Learned Patterns

- Empty sequence check before max()/min() on generators (confidence: 15)
- Copy-pasted error handling in similar code paths (confidence: 14)
- Async crash windows between message send and state persistence (confidence: 13)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 12)
- Flat-layout multi-package projects need explicit setuptools config (confidence: 11)
- Use [tool.setuptools.packages.find] with include/exclude filters (confidence: 10)
- Wildcard patterns (package*) match package and subpackages (confidence: 10)
- Test with 'pip install -e .' to verify package discovery works (confidence: 10)
- Multiple top-level dirs = setuptools ambiguity guard triggers (confidence: 10)
- Test with boundary cases (24, 25, 26, 50 items) before deployment (confidence: 7)
- Periodic polling with configurable intervals for sports data (confidence: 7)
- Threshold-based detection for significant value movements (confidence: 7)
- Discord webhook integration for real-time notifications (confidence: 7)
- Database snapshots for historical tracking and analysis (confidence: 7)
- Modular configuration system for dynamic thresholds (confidence: 7)
