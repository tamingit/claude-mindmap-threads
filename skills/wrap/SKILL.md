---
name: wrap
description: Close out the thread being chased. Records its conclusion, reports the finding back to the parent thread, parks any loose ends, and returns focus to the parent. Use for "/wrap", "that settles it", "done with this one", "pause this thread".
argument-hint: "[conclusion in your words | pause | drop]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/threads/tm.py *), ArtifactData, Read, Write, Edit
---

# Wrap up the current thread

`TM` = `python3 ${CLAUDE_PLUGIN_ROOT}/skills/threads/tm.py`, run from the project folder.
Full reference (sync steps, statuses): `${CLAUDE_PLUGIN_ROOT}/skills/threads/SKILL.md`. Read its
"Sync the mindmap" section if you have not this session.

Arguments: `$ARGUMENTS`

Run `TM get .` to get the active node, its parent and brief path. The outcome depends on the argument:
- **pause**: the thread is unfinished. Final status `open`.
- **drop**: not worth pursuing. Final status `dropped`. Record why.
- **anything else or empty**: concluded. Final status `done`. Use the user's words as the conclusion if
  given, or else draw it from the discussion. If the discussion did not reach a conclusion, say so and
  ask whether to pause instead.

Steps:
1. **Write the outcome into the node's brief** (`threads/briefs/<id>.md`; create it with the /chase
   template if missing). Add or replace a section at the top, under the title:
   ```markdown
   ## Outcome (<done|paused|dropped>, <date>)
   <Conclusion in 2–5 sentences: the answer, the key numbers behind it, and decisions made.>
   - Follow-ups: <concrete next actions, if any>
   ```
   For a pause, the outcome says where things stand and what the next step would be.
2. `TM summary <id> "<one-sentence conclusion>"`.
3. **Report back to the parent.** In `threads/briefs/<parent-id>.md` (create a minimal one if missing),
   add a bullet under `## Findings from branches` (create the section if needed):
   `- **<title>** (<status>): <one-sentence conclusion>. See briefs/<id>.md`
4. **Loose ends**: unresolved sub-questions that came up in this thread and aren't on the map get
   `TM add "<title>" --parent <id> --why "..."` (parked), each with a short brief as in the park skill.
5. `TM status <id> <done|open|dropped>`. For done/dropped this also stops the thread's background session.
   If that is *this* session (`TM sessions` shows `"this": true` with a session name), it stops itself
   about three minutes later, so finish steps 6–7 promptly.
6. Run `TM sessions`. If this session is a spawned background session for the thread, do not take over the
   parent. Otherwise `TM focus <parent-id>` (skip if this is the root, or if focus is refused because
   another session owns the parent). Then sync the mindmap.
7. Reply in at most six lines: the conclusion, what was parked, and the best next moves among parked
   and open siblings as `/chase <id>` lines (`/chase <id> +` for a separate session). In a background
   session that was stopped, say it closes in a few minutes and its record stays on the map. Otherwise
   suggest `/clear` then `/chase <parent-id>` to continue the parent with fresh context.
