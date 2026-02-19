---
name: done
description: Summarize session work and generate Obsidian documentation
disable-model-invocation: true
---

# Session Summary + Obsidian Documentation

Wrap up the current session by summarizing work, writing Obsidian docs, and syncing memory.

## Dynamic Context

Today's date: !`date +%Y-%m-%d`

Recent commits (this session):
!`git log --oneline -20`

Uncommitted changes:
!`git diff --stat`

Current tasks/todo.md:
!`cat tasks/todo.md 2>/dev/null || echo "No todo.md found"`

## Steps

### Step 1: Gather session context

Review the full conversation to identify:
- **What was worked on** — features added, bugs fixed, refactors done
- **Files changed** — use the git context above plus your knowledge of the conversation
- **Decisions made** — architectural choices, trade-offs, approach selections
- **Lessons learned** — mistakes corrected, patterns discovered, vendor quirks found

Synthesize this into a clear mental model before writing anything.

### Step 2: Write session log to Obsidian

Create a date-stamped session note. Derive a short kebab-case slug from the work done (e.g., `agent-healthcheck-fix`, `cjunos-support`, `vlan-repair-loop`).

**File**: `~/obsidian/Archetype/Sessions/YYYY-MM-DD-<slug>.md`

If multiple sessions happened on the same day, append a number: `YYYY-MM-DD-<slug>-2.md`

Use this format:

```markdown
# YYYY-MM-DD — <Short Title>

## Summary
<2-3 sentence overview of what was accomplished>

## Changes
- `path/to/file.py` — Description of change
- ...

## Decisions
- <Key architectural or implementation decisions and rationale>

## Lessons Learned
- <Anything notable for future reference>

## Related
- [[Agent]] or [[API]] or [[Networking]] etc. (link to relevant Knowledge docs)

## Tags
#archetype #<area> #<type>
```

Where `<area>` is one of: `agent`, `api`, `frontend`, `networking`, `devops`, `vendors`
And `<type>` is one of: `bugfix`, `feature`, `refactor`, `investigation`, `docs`

### Step 3: Update living Knowledge docs in Obsidian

Directory: `~/obsidian/Archetype/Knowledge/`

Topic files (create or update as needed based on what was touched this session):
- `Agent.md` — Agent architecture, providers, health checks, networking
- `API.md` — Backend API, jobs, state management, auth
- `Frontend.md` — React app, canvas, WS state, components
- `Networking.md` — OVS, VXLAN, overlay, carrier, VLANs
- `DevOps.md` — Deployment, Docker, CI, backups, agent updates
- `Vendors.md` — Device-specific configs, quirks, boot detection

**Only update topics that were actually touched in this session.** Don't update files for areas that weren't worked on.

For each relevant topic file:
- If the file **doesn't exist**, create it with this structure:
  ```markdown
  # <Topic>

  ## Overview
  <Brief description of this area>

  ## Key Files
  - `path/to/key/file.py` — purpose

  ## Architecture
  <How this area works>

  ## <Section per subtopic>
  > Updated YYYY-MM-DD

  <Content>

  ## See Also
  - [[Related Topic]]
  ```

- If the file **exists**, read it first, then append or update the relevant section:
  - Add new sections for new topics
  - Update existing sections with `> Updated YYYY-MM-DD` marker
  - Don't duplicate information already present
  - Use `[[wikilinks]]` for cross-references

### Step 4: Update _Index.md

Update `~/obsidian/Archetype/_Index.md` with a table of contents. Read the existing file first if it exists.

Format:
```markdown
# Archetype Knowledge Base

## Sessions
- [[YYYY-MM-DD-slug]] — Short description
- ...

## Knowledge
- [[Agent]] — Agent architecture and providers
- [[API]] — Backend API and state management
- [[Frontend]] — React app and canvas
- [[Networking]] — OVS, VXLAN, overlay networking
- [[DevOps]] — Deployment and operations
- [[Vendors]] — Device configs and quirks
```

Only list files that actually exist. Add new session entries at the top of the Sessions list.

### Step 5: Update MEMORY.md

Review the current memory file at `~/.claude/projects/-home-azayaka-archetype-iac/memory/MEMORY.md`.

- **Add** any new stable patterns, key file paths, or architectural decisions discovered this session
- **Update** any entries that are now outdated or wrong based on this session's work
- **Remove** entries that are no longer accurate
- **Keep it under 200 lines** — move detailed content to separate memory files if needed
- Don't add session-specific or temporary state

### Step 6: Update tasks/lessons.md

Read the current `tasks/lessons.md` file first.

Append any new lessons learned this session using this format:
```markdown
## YYYY-MM-DD: <Short description>

**Bug/Issue**: <What went wrong or what was discovered>

**Impact**: <Why it matters>

**Fix**: <How it was resolved>

**Rule**: <Prevention rule for the future>
```

Only add lessons if there are genuine new insights. Don't add trivial or already-documented lessons.

### Step 7: Present summary to user

Display a concise summary:

```
Session complete. Documentation updated:

**Session Log**: ~/obsidian/Archetype/Sessions/YYYY-MM-DD-slug.md
**Knowledge Updated**: <list of topic files touched>
**Memory**: <what was added/changed, or "No changes needed">
**Lessons**: <what was added, or "No new lessons">
```
