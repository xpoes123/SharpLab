Re-orient after a break or long session. Run this at the start of every working session.

Workflow:
1. Read `memory/status.md` if it exists — this has the last session's summary
2. Run `git log --oneline -10` to see recent commits
3. Run `git status` to check for uncommitted changes
4. Check `pyproject.toml` for current dependencies — what's actually installed?
5. Scan `temporal/activities.py` for TODO/stub comments to find what's not yet wired
6. Check if there's a `.env` file and which API keys are present (don't print values, just names)
7. Summarize clearly:
   - What's working (real, not stubs)
   - What's stubbed / not yet wired
   - What's in progress or incomplete
   - What the obvious next step is
8. Ask: "What do you want to work on?"
