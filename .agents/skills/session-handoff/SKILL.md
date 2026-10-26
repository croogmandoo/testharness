---
name: session-handoff
description: Generates a complete, structured session handoff document and kickoff prompt for a fresh AI agent session, consolidating completed work, current state, architectural decisions, and prioritized roadmap items.
---

# Session Handoff Skill for Web Testing Harness (`testharness`)

Use this skill when concluding a session or preparing to hand off work to a fresh AI agent session.

## Handoff Procedure

When invoked (via `/handoff` or user request):

1. **Inspect Current Project State:**
   - Run `git status` and `git log -n 5 --oneline`.
   - Run `.venv/bin/pytest` to verify that all tests pass without regressions.
   - Review architectural decisions, security patches, or changes made during the session.

2. **Update `docs/HANDOFF.md`:**
   Write/update the structured markdown handoff document containing:
   - **System Snapshot & Live State**: Port, DB files, Python environment, test count.
   - **Work Completed This Session**: Specific accomplishments, modified files, test additions.
   - **Architectural Invariants & Gotchas**: Key constraints and security rules.
   - **Prioritized Next Actions (Top 5)**: Ranked next steps for the incoming agent.
   - **Kickoff Prompt for Next Session**: Single copy-pasteable prompt block.

3. **Provide Fresh Session Kickoff Prompt:**
   Output the copy-pasteable prompt block so the user or next agent can immediately resume with zero context loss.
