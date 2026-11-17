---
name: chase
description: Chase threads on the map, either by switching this session to one or by starting a new background session per thread (it shows on claude.ai) with condensed context. Use for "/chase", "/chase <id> +", "chase this" (often on quoted/selected text), "chase these items: 1) … 2) …", "let's go down that path", "switch to the X question", "pick up the X thread", "open a session for X".
argument-hint: "<id or title> [+]  |  <id> <id> ... +  |  (empty = list candidates)"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/threads/tm.py *), ArtifactData, Read, Write, Edit, Glob, Grep
---

# Chase threads

`TM` = `python3 ${CLAUDE_PLUGIN_ROOT}/skills/threads/tm.py`, run from the project folder.
Full reference (sync steps, statuses, sessions): `${CLAUDE_PLUGIN_ROOT}/skills/threads/SKILL.md`. Read its
"Sync the mindmap" section if you have not this session.

Arguments: `$ARGUMENTS`

Each session owns at most one thread. `TM get .` is the thread this session owns.

## Pick the mode

- **No argument and no request in words:** run `TM show`, print the tree in a code block, list the parked
  and open nodes as `/chase <id>` lines, and ask which one. Stop there.
- **New session (do not switch):** the arguments end with `+`, OR the user says "chase this" / "chase
  these" about text they quoted or selected, OR gives a list of new questions to chase ("Chase these items:
  1) … 2) …"), OR names several threads, OR asks to open a session for it. Go to **Mode N**.
- **Switch this session:** one existing thread, no `+`. If the conversation holds discussion (anything
  other than a `/clear`) use **Mode A**; if it's fresh, **Mode B**.

Resolve each target with `TM get <ref>` (id, prefix or part of the title). A question that isn't on the map
yet gets `TM add "<short question, ≤8 words>" --why "<1–2 sentences: what raised it, key facts>"` first
(parent: this session's thread, or a better fit with `--parent`).

## Mode N: new background session per thread

1. Add any new questions to the map (above). Quoted/selected text is the source: phrase the question from
   it, and keep the quoted passage's key facts for the brief.
2. **Write or merge each target's brief** `threads/briefs/<id>.md` (template below), condensed from this
   conversation: only what matters for that question. The new session sees nothing but this brief, its
   parent's brief and the files, so put the needed facts, numbers and paths in it. If the brief exists,
   merge instead of replacing it.
3. If this session's own thread has new findings, update its brief and `TM summary . "<state of play>"`.
4. `TM spawn <id> [<id> ...]` (one call for all). It starts one background Remote Control session per
   thread, named after the question, opening with `/chase <id>`. It prints each one's claude.ai link.
   Threads already running in a session are not started again; their link is returned.
5. Sync the mindmap. **Do not switch**: this session keeps its own thread.
6. Reply with one line per thread: `▶ <title> — <link>` (or "starting, link on the mindmap" if no link
   came back, or the failure), then `Still here on: <this session's thread>`. Continue with any other
   unanswered request.

## Mode A: switch, with discussion in this conversation (consolidation)

1. **Checkpoint this session's thread** (`TM get .`, unless it is the target): write or update its brief
   using the template, and `TM summary <id> "<1–2 sentence state of play>"`.
2. **Write the target's brief** (filtered context: only what matters to the target; merge if it exists).
3. `TM focus <target-id>`. If it is refused because another live session owns the thread, give the user that
   session's link and stop (offer `--force` to take it over). Then sync the mindmap.
4. Tell the user, in at most four lines: the brief path, and
   `Run /clear, then /chase <target-id> to continue with only this thread's context.`
   Mention they can keep going here for the full history, or use `/chase <target-id> +` next time to get a
   separate session instead.

## Mode B: fresh conversation (just cleared, newly started, or a spawned session)

1. Read `threads/briefs/<target-id>.md`. If it doesn't exist, build it from `TM get` (title, why, path) and
   the parent's brief, then write it.
2. Skim the parent's brief only for "Decisions so far". Open business files only when the brief points to
   them and the next step needs them.
3. `TM focus <target-id>` (claims it for this session; if refused because another live session owns it,
   give that link and stop) and sync.
4. Reply: a 3–5 line recap (question, key facts, what's ruled out), then propose the first concrete step,
   or answer the question directly if the brief already contains enough. In a spawned session, go ahead
   and do that first step unless it needs a decision from the user.

## Brief template

Briefs must stand alone: a session with no memory of this chat must be able to continue from it.
Keep it under ~600 words. Use real numbers, names and file paths; never "as discussed above".

```markdown
# <question>

**Thread:** <id> · **Parent:** <parent title> (<parent-id>) · **Updated:** <date>

## Question
<The question in one or two sentences, and what a good answer would let us decide.>

## Why it came up
<What in the parent discussion raised it. Include the quoted/selected passage if there was one.>

## Context carried over
- <Facts, figures, assumptions relevant to THIS question only, with sources/file paths>

## Decisions so far
- <Decisions already made that constrain this thread>

## Ruled out
- <Options or explanations already rejected, and why>

## Where to start
- <Files to open, numbers to check, people/data needed>

## Open sub-questions
- <Unresolved pieces. Park them with /park if they turn into their own thread>
```
