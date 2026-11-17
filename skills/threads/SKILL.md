---
name: threads
description: Show or manage the conversation thread map (mindmap of branching topics) kept in ./threads/, and export it to Obsidian. Use for "/threads", "show the map", "where are we", "export to Obsidian", starting a new map, dropping/moving/renaming a thread, or setting up the mindmap page. Companion commands are /park, /chase and /wrap.
argument-hint: "[start \"topic\" | drop ID | move ID PARENT | order ID up|down|before X | rename ID \"title\" | obsidian [path|auto|off] | setup | private]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/threads/tm.py *), ArtifactData, Read, Write, Edit
---

# Thread map

A map of the lines of inquiry in this project's discussions. One node per question.
The current node is the one being chased. Side questions get **parked** instead of chased.
Each node can have a self-contained brief at `threads/briefs/<id>.md`.

`TM` below means `python3 ${CLAUDE_PLUGIN_ROOT}/skills/threads/tm.py` (run from the project folder).
It manages `threads/map.json`. Never edit map.json by hand.

Status marks: ○ parked · ▶ active (being chased) · ◐ open (explored, paused) · ✓ done · ✕ dropped

## Commands

Arguments: `$ARGUMENTS`

- **(none)**: run `TM show` and print the tree in a code block, then the mindmap link it prints. Below it, list
  at most 5 good next moves (parked or open nodes) as `/chase <id>` lines. Don't add commentary.
- **start "topic"**: `TM init "topic"`. Then write `threads/briefs/<root-id>.md` from the conversation so far if
  there is any (same template as /chase), then sync.
- **drop ID**: `TM status ID dropped` (stops its background session, if any), sync.
- **move ID PARENT**: `TM move ID PARENT`, sync.
- **order ID up|down|first|last|before OTHER|after OTHER**: `TM order ...` reorders a thread among its
  siblings (top to bottom on the mindmap, Index and canvas), sync. Also for plain requests like "put X above Y".
- **rename ID "title"**: `TM title ID "title"`, sync.
- **obsidian [PATH] | obsidian auto | obsidian off**: see "Obsidian export" below.
- **private**: `TM config sync_briefs=false`, sync. The mindmap then shows titles and summaries but not brief text.
- **setup**: see "Mindmap setup" below.

Other useful calls: `TM get ID` (node + path + children), `TM summary ID "..."`, `TM why ID "..."`.

## Sessions

Each Claude Code session owns at most one thread; several threads can be ▶ active at once, one per session.
`TM get .` is this session's thread. `TM spawn ID [ID ...]` starts a background Remote Control session per
thread (visible on claude.ai and the phone, named after the question, opening with `/chase ID`) and records
its link; the mindmap then shows an "Open session" button. Marking a thread done or dropped stops its
background session; pausing (open) keeps it running. `TM sessions` lists threads with sessions and whether
each is still running; `TM release ID [--stop]` detaches one. `claude agents` / `claude stop <id>` manage
the sessions directly.
IDs accept a unique prefix or a unique piece of the title.

## Obsidian export

On request ("export to Obsidian", `/threads obsidian`): `TM obsidian [PATH] [--no-sessions] [--all]`.
PATH defaults to the saved vault folder, else `~/thread-vault`; the first PATH used is remembered.
It writes `<vault>/<project>/`:
- `Threads/<question>.md`: one note per thread (properties: status, parent link, summary, session link;
  body: summary, why, branches, links to its sessions, the brief).
- `Sessions/<date> <title>.md`: a readable transcript of every Claude Code session that worked on a thread
  in this map (found from the map's session records and from `focus`/`/chase` steps in the session files
  under `~/.claude/projects/`). Split into sections per thread; tool calls collapsed, tool output left out.
  `--no-sessions` skips them.
- `Index.md` (the tree plus the session list) and `Map.canvas` (the mindmap as an Obsidian canvas).
`--all` exports every map this user has synced. Everything is read-only (chmod 444) and rebuilt each time.
Reply with the folder, the counts it prints, and that they open the vault folder in Obsidian.

`TM obsidian auto [PATH]` also refreshes the vault after every map change; `TM obsidian off` stops that.

## Sync the mindmap (always the last step after any change)

1. Run `TM export`. It prints JSON: `url`, `collection`, `doc_id`, `file_path`, `if_version`.
2. If `url` is null, skip syncing and mention once that `/threads setup` turns on the mindmap.
3. Call the `ArtifactData` tool: `action: "set"`, with `url`, `collection`, `doc_id`, `file_path`, and
   `if_version` (leave it out when it is null). If the write is refused because the document exists or its
   version changed, the refusal names the current version: retry once with that as `if_version`.
4. Run `TM synced <version shown in the successful result>`.

Keep sync quiet: no narration beyond "Map synced." at most.

## Mindmap setup

The mindmap page is one claude.ai artifact shared by all projects (each project is one document in its
`maps` collection). Check `TM config` first: if `global.artifact_url` is set, it already exists and only the
sync is needed. Otherwise publish `${CLAUDE_PLUGIN_ROOT}/skills/threads/mindmap.html` with the Artifact
tool (icon "map", capabilities `{"db": {"rules": [{"path": "maps", "read": "view", "write": "owner"}]}}`),
then `TM config url=<artifact url>` (saved per user in `~/.claude/mindmap-threads/config.json`), then sync.
