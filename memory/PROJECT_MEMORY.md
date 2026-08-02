# Project Memory Across Sessions

Each project gets its own memory file in `memory/projects/`. These files persist across sessions and let me pick up exactly where you left off.

## How to Use It

### At the Start of a Session
I'll read your project memory to understand:
- What you were last working on
- What blockers exist
- Recent decisions and their rationale
- Current goals and priorities

### During a Session
Tell me about:
- Work you completed
- New blockers or decisions
- Changes in priorities or approach
- Architecture or design decisions

I'll update the memory file automatically so it's fresh for next time.

### What to Track

**Current Status** — One sentence on what's in progress right now
- "Implementing user authentication module"
- "Investigating performance regression in API"
- "Code review on PR #234"

**Blockers** — What's holding progress back
- Technical dependencies (waiting on library release)
- Decisions pending (need input on feature design)
- External (waiting for design team)

**Recent Work** — Last 2-3 things you completed
- Helps provide context for ongoing work
- Makes it easy to reference what you did last session

**Tech/Architecture Notes** — Key decisions and why
- Why you chose specific libraries
- Trade-offs you accepted
- Known limitations

**Next Steps** — What's lined up
- Highest priority task first
- Dependency chain if relevant

## Example

```markdown
## Current Status
Wrapping up refactor of authentication module. Testing in progress.

## Blockers
- Waiting on OAuth provider docs for scope clarification
- Decision needed: should we cache tokens in localStorage or sessionStorage?

## Recent Work
- [x] Migrated auth to context API (completed)
- [x] Updated login form UI (completed)
- [ ] Integration tests for auth (in progress)

## Tech Decisions
- Using context API over Redux for state — simpler for this project
- JWT tokens in httpOnly cookies — more secure than localStorage

## Next Steps
1. Finish integration tests
2. Set up refresh token rotation
3. Update API docs for auth flows

## Notes
OAuth flow: client → backend proxy → provider (avoid CORS issues)
```

## Across Multiple Sessions

**Session 1:** "Built the auth module and hit a blocker on OAuth scopes"  
→ Memory file saved with blocker noted

**Session 2:** I read the memory, see the blocker, and can immediately ask "Did you hear back on those OAuth scopes?"

**Session 3:** If you mention "we decided to use sessionStorage," I update the decision in memory so it's there for session 4.

---

**Bottom line:** Project memory = no context-switch tax. Each session knows what happened before.
