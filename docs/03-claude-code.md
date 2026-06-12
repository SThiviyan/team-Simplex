# 03 — Using Claude Code well during the hackathon

This is opinionated, not exhaustive. Verify current flag/feature names at https://docs.claude.com if anything below looks off.

## First 5 minutes in your repo

```bash
cd team-<yourname>
claude
```

Then in Claude Code:

```
/init
```

Lets Claude read the codebase and build a `CLAUDE.md` it can use as context. Worth doing once — it pays back across the whole event.

## Plan before you ship

When you're about to make a non-trivial change ("add a vector search provider", "swap Tailwind for shadcn"), use plan mode:

```
Plan: add a Tavily search provider behind the SearchProvider interface, wired into the orchestrator.
```

Claude will outline files and changes; you approve or redirect before any edits land. Catches misunderstandings cheaply.

## Scope = speed

Smaller PRs deploy faster (cache-hits more layers) and roll back cleaner. A good rhythm:

- One feature per push.
- Hit `git commit` + `git push` between meaningful steps, not at end-of-day.
- If something breaks production, ping `#hackathon-help` — the team rolls back in <30s.

## Sub-agents — when

The Agent tool is good for:
- **Parallel research** ("find every place we hit Anthropic in the codebase")
- **Independent tasks** ("write tests for `orchestrator.py` while I work on the frontend")

Don't use sub-agents for trivial work — they add overhead. Rule of thumb: if you'd ask a colleague to context-switch onto it, sub-agent. If it's a 30-second tweak, just do it.

## Don't fight the deploy contract

Claude will sometimes propose changes that break the Cloud Run contract:
- Renaming the port from 8080 to something else
- Splitting into two services (each one needs its own Cloud Run service — out of scope for the default template)
- Hardcoding env vars that should come from `secrets.yaml`

When in doubt, ask: "does this still build with `docker build -t x . && docker run -p 8080:8080 x` and respond?"

## When you're stuck

Type `/help` in Claude Code or ask in `#hackathon-help`.
