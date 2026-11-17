---
name: park
description: Park side questions on the thread map for later, each with a short self-contained brief, without chasing them; then return to the current topic. Use for "/park", "park that", "save those for later", "add these to the map", "questions we'll discuss later: 1) … 2) …".
argument-hint: "[question; another question; ...]  (empty = park the tangents from the recent exchange)"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/threads/tm.py *), ArtifactData, Read, Write
---

# Park side questions

`TM` = `python3 ${CLAUDE_PLUGIN_ROOT}/skills/threads/tm.py`, run from the project folder.
Full reference (sync steps, statuses): `${CLAUDE_PLUGIN_ROOT}/skills/threads/SKILL.md`. Read its
"Sync the mindmap" section if you have not this session.

Arguments: `$ARGUMENTS`

If the user wants these questions *started now* in their own sessions ("chase these"), use the chase skill
(Mode N) instead. Parking only saves them.

1. **Make sure a map exists.** If `TM show` reports no map, run `TM init "<the main question being
   discussed>"`. Phrase it as a short question, then continue.
2. **Pick what to park.**
   - If arguments or a list were given, split them on `;`, new lines or numbering. Each one is a question.
   - If not, collect the side questions raised in the recent exchange (in your answers or the user's
     messages) that are *not* the thing currently being chased. Skip ones already on the map
     (check `TM show`). Usually 1–6 items.
3. **Park each one:** `TM add "<title>" --why "<context>"`
   - Title: a short question, at most ~8 words, readable on its own ("Does freight kill tier-3 margin?").
   - `--why`: one or two sentences: what raised it and the specific facts needed to start on it later
     (numbers, names, file paths). Someone with no memory of this chat must understand it.
   - Parent defaults to this session's thread. Use `--parent <id>` if it clearly belongs under another node.
4. **Write a short brief for each** `threads/briefs/<id>.md`, using the template in the chase skill
   (`${CLAUDE_PLUGIN_ROOT}/skills/chase/SKILL.md`) but short: Question, Why it came up, Context carried
   over, Where to start. 80–250 words, only facts from this conversation that matter to that question. This
   is what a session started for it later will see, since it can't see this chat. Skip it only when the
   conversation holds nothing beyond the `--why`.
5. **Do not start exploring them.** No analysis of parked items.
6. Sync the mindmap (see threads/SKILL.md).
7. Reply briefly: the parked items as `○ <title> [id]`, a hint line
   `/chase <id> + starts one in its own session (several ids at once is fine)`, then `Back to: <this
   session's thread>`. Then continue with the current topic if there was an unanswered request.
