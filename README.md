# mindmap-threads

A Claude Code plugin that keeps long discussions from sprawling. One answer often raises five new
questions. Instead of losing them, or dragging the whole conversation along while you chase one,
mindmap-threads keeps them as a **mindmap of questions**:

- **Park** side questions for later. Each one gets a short, self-contained brief.
- **Chase** a question in the session you're in, or in its **own background session** that already
  holds only the context that matters for it. You can open that session from claude.ai or the Claude app.
- **Wrap** a thread when it's settled. Its conclusion is reported back to the question it came from.
- See it all on a **live mindmap page** on claude.ai, and optionally in an **Obsidian vault**.

```
◐ How do we price the new service tier?            [pricing-new-tier]
├─ ✓ What do competitors charge?                    [competitors-charge]
├─ ▶ Does freight kill tier-3 margin?               [freight-tier3-margin]  ·session running
│  └─ ○ Can we renegotiate the carrier contract?    [renegotiate-carrier]
└─ ○ Who signs off on a price change?               [signs-off-price]
○ parked · ▶ being chased · ◐ paused · ✓ done · ✕ dropped
```

## Install

In Claude Code:

```
/plugin marketplace add tamingit/claude-mindmap-threads
/plugin install mindmap-threads@mindmap-threads
```

Or try it from a local copy without installing: `claude --plugin-dir /path/to/claude-mindmap`.

### Requirements

- Claude Code with `python3` on the PATH (standard library only, no packages to install).
- For the mindmap page: a claude.ai account that can publish artifacts.
- For sessions per thread (`/chase <id> +`): Claude Code's background sessions and **Remote Control**.
  On Team and Enterprise plans an admin may have to turn on Remote Control first.

## Commands

| Command | What it does |
| --- | --- |
| `/threads start "main question"` | Start a map in this folder. |
| `/park` | Save the side questions from the recent exchange, each with a brief. Or list them: `/park first question; second question` |
| `/chase <id>` | Switch this session to a thread: write its brief, then `/clear` and `/chase <id>` to continue with only that context. |
| `/chase <id> +` | Start the thread in its **own background session** and stay where you are. Several ids at once work too. |
| "chase this" | Select text on claude.ai, add it to your message, and say *chase this*: it becomes a thread with its own session. |
| "Chase these: 1) … 2) …" | One new thread and session per item. |
| `/wrap` | Close the thread with a conclusion and report it to its parent. `/wrap pause` keeps it open; `/wrap drop` abandons it. |
| `/threads` | Show the map. Also `drop`, `move ID PARENT`, `order ID up\|down\|first\|last\|before X\|after X`, `rename`, `private`, `setup`. |

Plain words work too ("park that", "put the freight question first", "that settles it").
Ids accept a unique prefix or a piece of the title. If another plugin uses the same command names, type
the full name, for example `/mindmap-threads:chase`.

## How it works

Everything lives in a `threads/` folder inside the project folder where you run Claude Code:

| File | Purpose |
| --- | --- |
| `threads/map.json` | The map: one node per question, with status, parent, summary and its session. |
| `threads/briefs/<id>.md` | One brief per question: the question, why it came up, facts carried over, decisions, what's ruled out, where to start. Briefs stand alone, so a fresh session can pick up from one. |
| `threads/.sync/` | What was last sent to the mindmap page. |

Each project folder has its own map. A map in a parent folder is found from subfolders too.

### One session per thread

`/chase <id> +` writes the thread's brief from the current conversation, then runs
`claude --bg --remote-control "<question>" "/mindmap-threads:chase <id>"`. The new session starts
fresh, reads only that brief, and gets going. It shows in your session list on claude.ai and in the Claude
app, named after the question, and the mindmap page gets an **Open session** button for it.

- Each session owns one thread, so several threads can be chased at the same time.
- `/wrap` (done) or `/threads drop` stops the thread's background session. `/wrap pause` leaves it running.
- Every running session uses your Claude usage while it works. `claude agents` lists them, and
  `claude stop <id>` stops one.

### Mindmap page (optional, once per person)

Run `/threads setup`. It publishes a private mindmap page to your claude.ai account and saves its link in
`~/.claude/mindmap-threads/config.json`. Every project folder syncs to that one page as a separate map.
Click a question to read its brief, open its session, or copy the command that starts one.

Only you can write to your page, so each person runs setup for themselves. Brief text is sent to the page
unless you run `/threads private` in that folder (titles and summaries only).

### Obsidian export (optional)

Ask for it when you want it: `/threads obsidian` or *"export the map to Obsidian"*. Add a folder to choose
where it goes (default `~/thread-vault`, and the first folder you use is remembered). It writes
`<vault>/<project>/`:

- `Threads/<question>.md`: one note per thread, with properties for status, parent, summary and session
  link, followed by links to its sessions and the brief. Notes link to their parent and branches, so the
  graph view shows the tree.
- `Sessions/<date> <title>.md`: a readable transcript of every Claude Code session that worked on these
  threads, split into a section per thread. Tool calls are collapsed and tool output is left out. Add
  `--no-sessions` to skip transcripts.
- `Index.md`: the whole tree and the session list.
- `Map.canvas`: the mindmap as an Obsidian canvas, colored by status.

`/threads obsidian --all` exports every map you have. `/threads obsidian auto` keeps the vault current
after every change, and `/threads obsidian off` stops that.

The export is **read-only**. Files are write-protected and rebuilt on every export, so make changes
through Claude. Open the folder as a vault in Obsidian, for example over an SSHFS or SMB mount if Claude
Code runs on a server. Transcripts can contain anything said in those sessions, so treat the vault like
the conversations themselves.

## Tips

- Add `threads/.sync/` and `threads/.lock` to your project's `.gitignore`. Commit `map.json` and
  `briefs/` if the map should travel with the project.
- `tm.py` prints its own help: `python3 <plugin>/skills/threads/tm.py`.
- Moving from a hand-installed copy in `~/.claude/skills/{threads,park,chase,wrap}`: delete those folders
  after installing the plugin. A personal skill with the same name takes the bare `/chase` name, and two
  copies drift apart. Your mindmap link carries over automatically.

## License

MIT. See [LICENSE](LICENSE).
