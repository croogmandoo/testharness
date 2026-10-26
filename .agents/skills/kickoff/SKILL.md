---
name: kickoff
description: Resumes work in a fresh session by ingesting docs/HANDOFF.md, docs/OVERVIEW.md, and docs/STANDARDS.md, verifying server/repo state, and presenting immediate next actions.
---

# Session Kickoff Skill for Web Testing Harness (`testharness`)

Use this skill at the start of a fresh session by typing `/kickoff` or asking the agent to resume work.

## Automatic Kickoff Workflow

When `/kickoff` is invoked:

1. **Ingest Architecture & State:**
   - Read `docs/HANDOFF.md` to get the latest state snapshot, passing tests count, and prioritized roadmap.
   - Read `docs/OVERVIEW.md` and `docs/STANDARDS.md` to load design conventions, layer boundaries, and testing discipline.

2. **Verify Environment & Test Suite:**
   - Run `git status` locally.
   - Run `.venv/bin/pytest -q` to confirm the test suite remains 100% green (797 tests).

3. **Present Status & Next Actions:**
   - Output a concise summary of the current live state.
   - Present the top prioritized roadmap items from `docs/HANDOFF.md`.
   - Ask the user which task to execute first.
