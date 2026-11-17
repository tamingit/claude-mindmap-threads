#!/usr/bin/env python3
"""tm.py - thread map for branching conversations.

The map lives in ./threads/ of the folder Claude Code runs in:
  threads/map.json        source of truth (nodes, active node)
  threads/briefs/<id>.md  self-contained brief per node (written by Claude)
  threads/.sync/          export for the claude.ai mindmap page

Statuses: parked (captured, not explored), active (being chased by a session; one per
session, several at once), open (explored, paused), done (concluded), dropped (not pursued).

Sessions: each Claude Code session owns at most one thread (node["session"]). "." means
the thread this session owns. `spawn` starts a background Remote Control session per thread;
marking a thread done or dropped stops its background session.
"""
import json, os, re, sys, datetime, pathlib, subprocess, time, fcntl, io, contextlib

KIT = pathlib.Path(__file__).resolve().parent
# Per-user settings (mindmap URL, vault path) live outside the plugin, so an update never wipes
# them and a published copy never carries anyone's URL. TM_CONFIG overrides the location.
GLOBAL_CFG = pathlib.Path(os.environ.get("TM_CONFIG") or
                          pathlib.Path.home() / ".claude" / "mindmap-threads" / "config.json")
LEGACY_CFGS = [pathlib.Path.home() / ".claude" / "thread-map" / "config.json",   # earlier package name
               KIT / "config.json"]                                               # pre-plugin kit
def find_root():
    """THREADS_DIR wins; else the nearest ./threads/map.json in this folder or a parent; else ./threads."""
    if os.environ.get("THREADS_DIR"):
        return pathlib.Path(os.environ["THREADS_DIR"]).resolve()
    here = pathlib.Path.cwd().resolve()
    for d in [here, *here.parents]:
        if (d / "threads" / "map.json").exists():
            return d / "threads"
    return here / "threads"


ROOT = find_root()
MAP = ROOT / "map.json"
BRIEFS = ROOT / "briefs"
SYNC = ROOT / ".sync"
STATUSES = ["parked", "active", "open", "done", "dropped"]
MARK = {"parked": "○", "active": "▶", "open": "◐", "done": "✓", "dropped": "✕"}
BRIEF_CAP = 6000          # chars of each brief sent to the mindmap page
DOC_CAP = 230_000         # db documents max out at 256 KiB


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def slugify(text, n=40):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    s = s[:n].rstrip("-")
    return s or "node"


def load():
    if not MAP.exists():
        die(f"no map in {ROOT}. Start one with: tm.py init \"<topic>\"", 2)
    return json.loads(MAP.read_text())


def save(m):
    m["updated"] = now()
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = MAP.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(MAP)


def gcfg():
    for f in (GLOBAL_CFG, *([] if os.environ.get("TM_CONFIG") else LEGACY_CFGS)):
        if f.exists():
            return json.loads(f.read_text())
    return {}


def save_gcfg(c):
    GLOBAL_CFG.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_CFG.write_text(json.dumps(c, indent=2))


def resolve(m, ref):
    nodes = m["nodes"]
    if ref in (".", "", None):
        return mine(m) or m["active"]
    if ref in nodes:
        return ref
    pre = [i for i in nodes if i.startswith(ref)]
    if len(pre) == 1:
        return pre[0]
    sub = [i for i, n in nodes.items() if ref.lower() in n["title"].lower()]
    if len(sub) == 1:
        return sub[0]
    cands = pre or sub
    if cands:
        die(f"'{ref}' matches several nodes: {', '.join(cands)}")
    die(f"no node matches '{ref}'. Run: tm.py show")


STOP = set("""a an the of to in on at by for from with and or but is are was were be been do does did
should could would can will we our us i my you your it its this that these those what which who whom how
why when where whether there their they them actually really just about up into vs versus any some
much many more most per""".split())


def short_id(title, words=4, n=32):
    toks = re.findall(r"[a-z0-9]+", title.lower())
    keep = [t for t in toks if t not in STOP] or toks
    out = ""
    for t in keep[:words]:
        nxt = f"{out}-{t}" if out else t
        if len(nxt) > n:
            break
        out = nxt
    return out or slugify(title, n)


def new_id(m, title):
    base = short_id(title)
    i, k = base, 2
    while i in m["nodes"]:
        i, k = f"{base}-{k}", k + 1
    return i


def make_node(m, title, parent, status, why=""):
    nid = new_id(m, title)
    t = now()
    m["nodes"][nid] = {"id": nid, "title": title, "parent": parent, "status": status,
                       "why": why, "summary": "", "created": t, "updated": t,
                       "order": len(m["nodes"])}
    return nid


def children(m, nid):
    return sorted((n for n in m["nodes"].values() if n["parent"] == nid), key=lambda n: n["order"])


def brief_path(nid):
    return BRIEFS / f"{nid}.md"


# ---------------------------------------------------------------- sessions

def me():
    """This Claude Code session: its id and, with Remote Control on, its claude.ai link."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    bridge = os.environ.get("CLAUDE_CODE_BRIDGE_SESSION_ID")
    return {"sid": sid, "url": f"https://claude.ai/code/{bridge}" if bridge else None}


def mine(m):
    sid = me()["sid"]
    if not sid:
        return None
    for i, n in m["nodes"].items():
        if (n.get("session") or {}).get("sid") == sid and n["status"] == "active":
            return i
    return None


_AGENTS = None
def agents():
    """Running Claude Code sessions, keyed by sessionId (cached per run)."""
    global _AGENTS
    if _AGENTS is None:
        try:
            out = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=20).stdout
            _AGENTS = {a["sessionId"]: a for a in json.loads(out or "[]") if a.get("sessionId")}
        except Exception:
            _AGENTS = {}
    return _AGENTS


def is_live(s):
    if not s or not s.get("sid"):
        return False
    a = agents().get(s["sid"])
    return bool(a) and a.get("state") not in ("stopped", "failed", "exited")


def owner_elsewhere(m, nid):
    """The session dict if another live session owns this thread, else None."""
    s = m["nodes"][nid].get("session")
    if s and s.get("sid") != me()["sid"] and is_live(s):
        return s
    return None


def set_active(m, nid):
    """This session takes thread nid. Its previous thread pauses (open)."""
    who = me()
    prev = None                                   # this session's record from its previous thread
    for i, n in m["nodes"].items():
        if i == nid:
            continue
        s = n.get("session")
        mine_ = bool(s and who["sid"] and s.get("sid") == who["sid"])
        legacy = not s and i == m.get("active") and n["status"] == "active"
        if mine_:
            prev = n.pop("session")
        if (mine_ or legacy) and n["status"] == "active":
            n["status"], n["updated"] = "open", now()
    m["active"] = nid
    n = m["nodes"][nid]
    n["status"], n["updated"] = "active", now()
    if who["sid"]:
        hist = n.setdefault("sessions", [])
        if who["sid"] not in hist:
            hist.append(who["sid"])
        old = n.get("session") or {}
        if old.get("sid") != who["sid"] and prev:
            old = prev
        same = old.get("sid") == who["sid"]
        n["session"] = {"sid": who["sid"], "url": who["url"] or (old.get("url") if same else None),
                        "bg": old.get("bg") if same else None, "name": old.get("name") if same else None,
                        "at": old.get("at") if same else now()}


def stop_session(n):
    """Stop the thread's background session (delayed if it is this one, so it can finish replying)."""
    s = n.pop("session", None)
    if not s or not s.get("bg"):
        return None
    if s.get("sid") == me()["sid"]:
        subprocess.Popen(["sh", "-c", f"sleep 180; claude stop {s['bg']}"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"this session ({s['bg']}) stops in about 3 minutes"
    subprocess.run(["claude", "stop", s["bg"]], capture_output=True, text=True, timeout=30)
    return f"stopped session {s['bg']}"


# ---------------------------------------------------------------- commands

def cmd_init(args):
    if not args:
        die('usage: tm.py init "<topic>" [--project slug]')
    if MAP.exists():
        die(f"a map already exists at {MAP}")
    import hashlib
    cwd = pathlib.Path.cwd().resolve()
    project = f"{slugify(cwd.name, 50)}-{hashlib.sha1(str(cwd).encode()).hexdigest()[:4]}"
    if "--project" in args:
        i = args.index("--project"); project = slugify(args[i + 1], 60); del args[i:i + 2]
    m = {"project": project, "title": args[0], "active": None, "nodes": {}, "created": now()}
    root = make_node(m, args[0], None, "active")
    m["active"] = root
    BRIEFS.mkdir(parents=True, exist_ok=True)
    save(m)
    print(json.dumps({"root": root, "project": project, "map": str(MAP)}))


def opt(args, flag, default=None):
    if flag in args:
        i = args.index(flag); v = args[i + 1]; del args[i:i + 2]; return v
    return default


def cmd_add(args):
    m = load()
    parent = resolve(m, opt(args, "--parent", "."))
    why = opt(args, "--why", "")
    if not args:
        die('usage: tm.py add "<title>" [--parent ID] [--why "..."]')
    nid = make_node(m, args[0], parent, "parked", why)
    save(m)
    print(nid)


def cmd_focus(args):
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    m = load()
    nid = resolve(m, args[0] if args else None)
    other = owner_elsewhere(m, nid)
    if other and not force:
        print(json.dumps({"refused": "owned by another live session", "id": nid,
                          "session": other.get("name") or other.get("bg"), "url": other.get("url"),
                          "hint": "open that session instead, or pass --force to take it over"}))
        sys.exit(3)
    set_active(m, nid)
    save(m)
    n = m["nodes"][nid]
    bp = brief_path(nid)
    print(json.dumps({"active": nid, "title": n["title"],
                      "brief": str(bp) if bp.exists() else None, "session": n.get("session")}))


def cmd_status(args):
    if len(args) < 2 or args[1] not in STATUSES:
        die(f"usage: tm.py status ID {'|'.join(STATUSES)}")
    m = load()
    nid = resolve(m, args[0])
    note = None
    if args[1] == "active":
        set_active(m, nid)
    else:
        n = m["nodes"][nid]
        n["status"], n["updated"] = args[1], now()
        if args[1] in ("done", "dropped"):
            note = stop_session(n)
        elif args[1] in ("open", "parked"):
            s = n.get("session") or {}
            if s.get("sid") == me()["sid"] and not s.get("bg"):
                n.pop("session", None)      # a background session stays attached until done
    save(m)
    print(f"{nid}: {m['nodes'][nid]['status']}" + (f" ({note})" if note else ""))


def chase_cmd():
    """/chase, or /<plugin>:chase when running from a plugin (always resolves, even if names clash)."""
    man = KIT.parent.parent / ".claude-plugin" / "plugin.json"
    try:
        return f"/{json.loads(man.read_text())['name']}:chase"
    except Exception:
        return "/chase"


def spawn_name(m, n):
    t = n["title"].rstrip("?")
    return (t[:48].rstrip() + "…") if len(t) > 49 else t


def cmd_spawn(args):
    """spawn ID [ID ...] : one background Remote Control session per thread, started with /chase ID."""
    if not args:
        die("usage: tm.py spawn ID [ID ...]")
    with lock():
        m = load()
        ids = [resolve(m, a) for a in args]
    cwd = str(ROOT.parent)
    results = []
    for nid in ids:
        with lock():
            m = load()
            n = m["nodes"][nid]
            s = n.get("session")
            if is_live(s):
                results.append({"id": nid, "status": "already running", "url": s.get("url"),
                                "session": s.get("name") or s.get("bg")})
                continue
            if n["status"] in ("done", "dropped"):
                n["status"] = "open"
            name = spawn_name(m, n)
        r = subprocess.run(["claude", "--bg", "--remote-control", name, "-n", name, f"{chase_cmd()} {nid}"],
                           cwd=cwd, capture_output=True, text=True, timeout=90)
        mt = re.search(r"backgrounded\s*·\s*([0-9a-f]{6,})", r.stdout + r.stderr)
        if not mt:
            results.append({"id": nid, "status": "failed", "output": (r.stdout + r.stderr)[-600:]})
            continue
        bg = mt.group(1)
        global _AGENTS
        sid, url = None, None
        for _ in range(30):                       # up to ~30s for the id and the claude.ai link
            _AGENTS = None
            a = next((a for a in agents().values() if a.get("id") == bg), None)
            sid = sid or (a or {}).get("sessionId")
            log = subprocess.run(["claude", "logs", bg], capture_output=True, text=True, timeout=20).stdout
            u = re.search(r"https://claude\.ai/code/session_[A-Za-z0-9]+", log)
            url = u.group(0) if u else None
            if sid and url:
                break
            time.sleep(1)
        with lock():
            m = load()
            n = m["nodes"][nid]
            cur = n.get("session") or {}
            n["session"] = {"sid": sid, "bg": bg, "name": name, "url": url or cur.get("url"),
                            "at": now()}
            if sid and sid not in n.setdefault("sessions", []):
                n["sessions"].append(sid)
            if n["status"] != "active":
                n["status"], n["updated"] = "active", now()
            save(m)
        results.append({"id": nid, "status": "started", "bg": bg, "session": name, "url": url})
    print(json.dumps(results, indent=2, ensure_ascii=False))


def cmd_sessions(args):
    """List threads with a session attached and whether it is still running."""
    m = load()
    rows = []
    for n in sorted(m["nodes"].values(), key=lambda n: n["order"]):
        s = n.get("session")
        if s:
            rows.append({"id": n["id"], "title": n["title"], "status": n["status"], "live": is_live(s),
                         "session": s.get("name") or s.get("bg"), "url": s.get("url"),
                         "this": s.get("sid") == me()["sid"]})
    print(json.dumps(rows, indent=2, ensure_ascii=False))


def cmd_release(args):
    """release ID [--stop] : detach a thread from its session (and stop a background one with --stop)."""
    stop = "--stop" in args
    args = [a for a in args if a != "--stop"]
    m = load()
    nid = resolve(m, args[0] if args else None)
    n = m["nodes"][nid]
    note = stop_session(n) if stop else (n.pop("session", None) and "released")
    if n["status"] == "active":
        n["status"] = "open"
    n["updated"] = now()
    save(m)
    print(f"{nid}: {n['status']}" + (f" ({note})" if note else ""))


def cmd_field(field):
    def run(args):
        if len(args) < 2:
            die(f'usage: tm.py {field} ID "<text>"')
        m = load()
        nid = resolve(m, args[0])
        m["nodes"][nid][field] = args[1]
        m["nodes"][nid]["updated"] = now()
        save(m)
        print(f"{nid}: {field} updated")
    return run


def cmd_move(args):
    m = load()
    nid, parent = resolve(m, args[0]), resolve(m, args[1])
    p = parent
    while p:
        if p == nid:
            die("cannot move a node under itself")
        p = m["nodes"][p]["parent"]
    m["nodes"][nid]["parent"] = parent
    save(m)
    print(f"{nid} -> under {parent}")


def cmd_order(args):
    """order ID up|down|first|last|before OTHER|after OTHER : reorder a thread among its siblings."""
    if len(args) < 2 or args[1] not in ("up", "down", "first", "last", "before", "after"):
        die("usage: tm.py order ID up|down|first|last|before OTHER|after OTHER")
    m = load()
    nid = resolve(m, args[0])
    sib = [n["id"] for n in children(m, m["nodes"][nid]["parent"])] if m["nodes"][nid]["parent"] else \
          [n["id"] for n in sorted(m["nodes"].values(), key=lambda n: n["order"]) if n["parent"] is None]
    slots = sorted(m["nodes"][i]["order"] for i in sib)      # reuse the same order numbers
    i = sib.index(nid)
    sib.pop(i)
    how = args[1]
    if how == "up":
        sib.insert(max(i - 1, 0), nid)
    elif how == "down":
        sib.insert(min(i + 1, len(sib)), nid)
    elif how == "first":
        sib.insert(0, nid)
    elif how == "last":
        sib.append(nid)
    else:
        if len(args) < 3:
            die(f"usage: tm.py order ID {how} OTHER")
        other = resolve(m, args[2])
        if other not in sib:
            die(f"{other} is not a sibling of {nid} (move it first: tm.py move {nid} <parent>)")
        j = sib.index(other)
        sib.insert(j if how == "before" else j + 1, nid)
    for k, slot in zip(sib, slots):
        m["nodes"][k]["order"] = slot
    save(m)
    print(" > ".join(sib))


def cmd_get(args):
    m = load()
    nid = resolve(m, args[0] if args else None)
    n = dict(m["nodes"][nid])
    bp = brief_path(nid)
    n["brief"] = str(bp)
    n["brief_exists"] = bp.exists()
    n["path"] = []
    p = n["parent"]
    while p:
        n["path"].insert(0, f"{p}: {m['nodes'][p]['title']}")
        p = m["nodes"][p]["parent"]
    n["children"] = [f"{c['id']} [{c['status']}]" for c in children(m, nid)]
    print(json.dumps(n, indent=2, ensure_ascii=False))


def cmd_show(args):
    m = load()
    roots = [n for n in m["nodes"].values() if n["parent"] is None]
    roots.sort(key=lambda n: n["order"])
    out = []

    def walk(n, prefix, last, top):
        conn = "" if top else ("└─ " if last else "├─ ")
        b = " ·brief" if brief_path(n["id"]).exists() else ""
        s = n.get("session")
        if s and s.get("sid") == me()["sid"]:
            b += " ·this session"
        elif is_live(s):
            b += " ·session running"
        out.append(f"{prefix}{conn}{MARK[n['status']]} {n['title']}  [{n['id']}]{b}")
        kids = children(m, n["id"])
        ext = "" if top else ("   " if last else "│  ")
        for i, c in enumerate(kids):
            walk(c, prefix + ext, i == len(kids) - 1, False)

    for r in roots:
        walk(r, "", True, True)
    counts = {s: sum(1 for n in m["nodes"].values() if n["status"] == s) for s in STATUSES}
    print("\n".join(out))
    print("\n" + "  ".join(f"{MARK[s]} {s} {c}" for s, c in counts.items() if c))
    a = mine(m)
    if a:
        print(f"this session: {m['nodes'][a]['title']}  [{a}]")
    elif m.get("active") and me()["sid"] is None:
        print(f"chasing: {m['nodes'][m['active']]['title']}  [{m['active']}]")
    url = gcfg().get("artifact_url")
    if url:
        print(f"mindmap: {url}#{m['project']}")


def cmd_export(args):
    m = load()
    cfg = json.loads((ROOT / "config.json").read_text()) if (ROOT / "config.json").exists() else {}
    with_briefs = cfg.get("sync_briefs", True)
    nodes = []
    for n in sorted(m["nodes"].values(), key=lambda n: n["order"]):
        d = {k: n[k] for k in ("id", "title", "parent", "status", "why", "summary", "created", "updated")}
        bp = brief_path(n["id"])
        d["has_brief"] = bp.exists()
        s = n.get("session")
        if s:
            d["session"] = {"name": s.get("name") or s.get("bg"), "url": s.get("url"),
                            "live": is_live(s), "background": bool(s.get("bg"))}
        if with_briefs and bp.exists():
            d["brief"] = bp.read_text()[:BRIEF_CAP]
        nodes.append(d)
    doc = {"project": m["project"], "title": m["title"], "active": m.get("active"),
           "updated": m.get("updated", now()), "nodes": nodes}
    cap = BRIEF_CAP
    while len(json.dumps(doc, ensure_ascii=False).encode()) > DOC_CAP and cap > 200:
        cap //= 2
        for d in nodes:
            if "brief" in d:
                d["brief"] = d["brief"][:cap] + "\n\n…(trimmed)"
    SYNC.mkdir(parents=True, exist_ok=True)
    f = SYNC / "doc.json"
    f.write_text(json.dumps(doc, ensure_ascii=False))
    st = json.loads((SYNC / "state.json").read_text()) if (SYNC / "state.json").exists() else {}
    url = gcfg().get("artifact_url")
    remember_map(m)
    vault_note = None
    if gcfg().get("vault_auto") and "--no-vault" not in args:
        try:
            vault_note = write_vault(m, doc, vault_root(), with_sessions=True)
        except Exception as e:                    # the vault must never block a sync
            vault_note = f"vault export failed: {e}"
    print(json.dumps({"url": url, "collection": "maps", "doc_id": m["project"],
                      "file_path": str(f), "if_version": st.get("version"),
                      "note": None if url else "no mindmap artifact configured; skip sync",
                      "vault": vault_note}))


# ---------------------------------------------------------------- obsidian vault (read-only export)

VAULT_MARK = "generated_by: tm.py"
CANVAS_COLOR = {"active": "2", "open": "5", "done": "4", "dropped": "1"}   # parked: default grey


def note_name(title, nid, taken):
    """A readable file name that is safe on Windows shares."""
    base = re.sub(r'[\\/:*?"<>|#^\[\]]', "", title).strip().rstrip(".") or nid
    base = re.sub(r"\s+", " ", base)[:90]
    name = base if base.lower() not in taken else f"{base} ({nid})"
    taken.add(name.lower())
    return name


def yq(s):
    return json.dumps(s if s is not None else "", ensure_ascii=False)


def vault_root(override=None):
    return pathlib.Path(os.path.expanduser(override or gcfg().get("vault") or "~/thread-vault"))


def remember_map(m):
    c = gcfg()
    maps = c.setdefault("maps", {})
    if maps.get(m["project"]) != str(ROOT):
        maps[m["project"]] = str(ROOT)
        try:
            save_gcfg(c)
        except Exception:
            pass


def write_vault(m, doc, vroot, with_sessions=True):
    proj = slugify(m["project"], 60)
    pdir = vroot / proj
    tdir = pdir / "Threads"
    tdir.mkdir(parents=True, exist_ok=True)
    nodes = {d["id"]: d for d in doc["nodes"]}
    kids = {i: [] for i in nodes}
    for d in doc["nodes"]:
        if d["parent"] in kids:
            kids[d["parent"]].append(d["id"])
    taken, names = set(), {}
    for d in doc["nodes"]:
        names[d["id"]] = note_name(d["title"], d["id"], taken)
    rel = lambda i: f"{proj}/Threads/{names[i]}"               # vault-relative link target
    link = lambda i: f"[[{rel(i)}|{nodes[i]['title']}]]"
    label = {"parked": "Parked", "active": "Chasing now", "open": "Paused", "done": "Done", "dropped": "Dropped"}
    stamp = now()
    written = set()

    def put(path, text):
        if path.exists():
            os.chmod(path, 0o644)
        path.write_text(text)
        os.chmod(path, 0o444)                     # read-only: edits belong in Claude, not here
        written.add(path.resolve())

    # Sessions that worked on these threads, as readable transcripts in Sessions/
    sess, by_thread = ([], {}) if not with_sessions else related_sessions(m)
    sdir = pdir / "Sessions"
    snames = {}
    if sess:
        sdir.mkdir(parents=True, exist_ok=True)
        staken = set()
        for t in sess:
            snames[t["sid"]] = note_name(f"{(t['start'] or '')[:10]} {t['title']}", t["sid"][:8], staken)
        for t in sess:
            put(sdir / f"{snames[t['sid']]}.md", render_transcript(t, lambda i: link(i) if i in nodes else f"`{i}`"))
    slink = lambda sid: f"[[{proj}/Sessions/{snames[sid]}|{snames[sid]}]]"

    for i, d in nodes.items():
        s = d.get("session") or {}
        bp = brief_path(i)
        brief = bp.read_text() if bp.exists() else ""
        brief = re.sub(r"^#\s+.*\n+", "", brief, count=1)    # the note title already shows it
        fm = ["---", VAULT_MARK, f"thread_id: {yq(i)}", f"status: {d['status']}",
              f"parent: {yq(link(d['parent']) if d['parent'] in nodes else '')}",
              f"summary: {yq(d.get('summary'))}", f"session: {yq(s.get('url') if s.get('live') else '')}",
              f"created: {d['created'][:10]}", f"updated: {d['updated'][:10]}",
              f"tags: [thread/{d['status']}]", "---"]
        body = [f"> [!info] Generated from the thread map ({stamp[:16].replace('T', ' ')}). Read-only: "
                f"change it in Claude with `/chase {i}`, `/park`, `/wrap`.", ""]
        body.append(f"**{MARK[d['status']]} {label[d['status']]}**" +
                    (f" · [Open session ↗]({s['url']})" if s.get("live") and s.get("url") else
                     f" · start it: `/chase {i} +`" if d["status"] not in ("done", "dropped") else ""))
        if d["parent"] in nodes:
            body.append(f"Parent: {link(d['parent'])}")
        body.append("")
        if d.get("summary"):
            body += ["## Where it stands", d["summary"], ""]
        if d.get("why"):
            body += ["## Why it came up", d["why"], ""]
        if kids[i]:
            body += ["## Branches"] + [f"- {MARK[nodes[k]['status']]} {link(k)}" for k in kids[i]] + [""]
        if by_thread.get(i):
            body += ["## Sessions"] + [f"- {slink(sid)}" for sid in by_thread[i] if sid in snames] + [""]
        if brief.strip():
            body += ["## Brief", re.sub(r"^## ", "### ", brief.strip(), flags=re.M), ""]
        put(tdir / f"{names[i]}.md", "\n".join(fm + [""] + body))

    # Index: the tree as a nested list
    roots = [d["id"] for d in doc["nodes"] if d["parent"] not in nodes]
    lines = ["---", VAULT_MARK, "---", f"# {m['title']}", "",
             f"> [!info] Thread map `{m['project']}` from `{ROOT.parent}`, generated {stamp[:16].replace('T', ' ')}. "
             "Read-only. Open **Map.canvas** for the picture.", ""]
    def walk(i, depth):
        d = nodes[i]
        s = d.get("session") or {}
        extra = f" · [session ↗]({s['url']})" if s.get("live") and s.get("url") else ""
        lines.append(f"{'  ' * depth}- {MARK[d['status']]} {link(i)}{extra}")
        for k in kids[i]:
            walk(k, depth + 1)
    for r in roots:
        walk(r, 0)
    if snames:
        lines += ["", "## Sessions"] + [f"- {slink(t['sid'])}" for t in sess]
    lines += ["", "Marks: ○ parked · ▶ chasing · ◐ paused · ✓ done · ✕ dropped"]
    put(pdir / "Index.md", "\n".join(lines) + "\n")

    # Canvas: left-to-right tree like the mindmap page
    COLW, ROWH, W, H = 340, 110, 280, 80
    pos, row = {}, [0]
    def place(i, depth):
        for k in kids[i]:
            place(k, depth + 1)
        ks = kids[i]
        y = (pos[ks[0]][1] + pos[ks[-1]][1]) / 2 if ks else row[0] * ROWH
        if not ks:
            row[0] += 1
        pos[i] = (depth * COLW, y)
    for r in roots:
        place(r, 0)
    cnodes, edges = [], []
    for i, (x, y) in pos.items():
        d = nodes[i]
        cn = {"id": i, "type": "text", "x": int(x), "y": int(y), "width": W, "height": H,
              "text": f"{MARK[d['status']]} **{link(i)}**"}
        if d["status"] in CANVAS_COLOR:
            cn["color"] = CANVAS_COLOR[d["status"]]
        cnodes.append(cn)
        if d["parent"] in nodes:
            edges.append({"id": f"e-{i}", "fromNode": d["parent"], "fromSide": "right",
                          "toNode": i, "toSide": "left"})
    put(pdir / "Map.canvas", json.dumps({"nodes": cnodes, "edges": edges}, indent=1, ensure_ascii=False))

    # Remove notes this script made earlier for threads that no longer exist (renamed or deleted)
    removed = 0
    for f in [*tdir.glob("*.md"), *(sdir.glob("*.md") if sdir.exists() else [])]:
        if f.resolve() in written:
            continue
        try:
            head = f.read_text()[:200]
        except Exception:
            continue
        if VAULT_MARK in head:
            os.chmod(f, 0o644); f.unlink(); removed += 1
    return {"vault": str(vroot), "folder": str(pdir), "threads": len(nodes), "sessions": len(snames),
            "removed": removed}


# ---------------------------------------------------------------- session transcripts

def projects_dir():
    return pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude") / "projects"


def soft_resolve(m, ref):
    ref = ref.strip("\"'`")
    if ref in m["nodes"]:
        return ref
    pre = [i for i in m["nodes"] if i.startswith(ref)]
    return pre[0] if len(pre) == 1 else None


FOCUS_RE = re.compile(r"(?:tm\.py|\$TM|\bTM)\s+(?:focus|status)\s+([A-Za-z0-9][\w.-]*)(\s+active)?")
MIDTURN_RE = re.compile(r"The user sent a new message while you were working:\s*\n(.*?)\n\s*\nThis is how", re.S)
STRIP_RE = re.compile(r"<(system-reminder|local-command-caveat|local-command-stdout|command-message)>.*?</\1>", re.S)


def clean_user_text(t):
    cmd = re.search(r"<command-name>(.*?)</command-name>", t, re.S)
    if cmd:
        a = re.search(r"<command-args>(.*?)</command-args>", t, re.S)
        return f"`{cmd.group(1).strip()} {(a.group(1).strip() if a else '')}`".replace(" `", "`")
    t = STRIP_RE.sub("", t)
    t = re.sub(r"</?pasted_content[^>]*>", "", t)
    return t.strip()


def tool_line(b):
    inp = b.get("input") or {}
    name = b.get("name", "tool")
    desc = inp.get("description") or inp.get("file_path") or inp.get("url") or inp.get("action") or ""
    detail = inp.get("command") or inp.get("prompt") or ""
    detail = detail.strip().splitlines()[0][:160] if detail else ""
    return f"**{name}** {desc}".strip() + (f" · `{detail.replace('`', '')}`" if detail else "")


def read_transcript(path, m):
    """Parse a Claude Code session file into messages, noting which thread each part belongs to."""
    t = {"sid": path.stem, "path": str(path), "title": None, "name": None, "start": None, "end": None,
         "cwd": None, "items": [], "threads": []}
    cur = None
    def mark(nid):
        nonlocal cur
        if nid and nid != cur:
            cur = nid
            t["items"].append(("thread", None, nid))
            if nid not in t["threads"]:
                t["threads"].append(nid)
    for line in open(path, errors="replace"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        typ = d.get("type")
        if typ == "ai-title":
            t["title"] = d.get("aiTitle") or t["title"]
        elif typ in ("custom-title", "session-name"):
            t["name"] = d.get("customTitle") or d.get("name") or t["name"]
        if typ == "attachment" and not d.get("isSidechain"):
            a = d.get("attachment") or {}
            if a.get("type") == "queued_command" and (a.get("origin") or {}).get("kind") == "human" \
                    and a.get("prompt"):              # a message sent while Claude was busy
                txt = clean_user_text(str(a["prompt"]))
                if txt and not any(k == "user" and x == txt for k, _, x in t["items"][-3:]):
                    t["items"].append(("user", d.get("timestamp") or a.get("timestamp"), txt))
            continue
        if typ not in ("user", "assistant") or d.get("isSidechain"):
            continue
        ts = d.get("timestamp")
        t["start"] = t["start"] or ts
        t["end"] = ts or t["end"]
        t["cwd"] = t["cwd"] or d.get("cwd")
        content = (d.get("message") or {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        for b in blocks:
            bt = b.get("type")
            if typ == "user":
                if bt == "text" and not d.get("isMeta"):
                    txt = clean_user_text(b.get("text", ""))
                    mc = re.match(r"`/(?:[\w-]+:)?chase ([\w.-]+)`$", txt or "")
                    if mc:
                        mark(soft_resolve(m, mc.group(1)))
                    if txt and not any(k == "user" and x == txt for k, _, x in t["items"][-3:]):
                        t["items"].append(("user", ts, txt))
                elif bt == "tool_result":
                    raw = b.get("content")
                    raw = raw if isinstance(raw, str) else " ".join(x.get("text", "") for x in (raw or [])
                                                                     if isinstance(x, dict))
                    for mm in MIDTURN_RE.finditer(raw or ""):
                        t["items"].append(("user", ts, mm.group(1).strip()))
            else:
                if bt == "text" and b.get("text", "").strip():
                    t["items"].append(("assistant", ts, b["text"].strip()))
                elif bt == "tool_use":
                    cmdline = (b.get("input") or {}).get("command") or ""
                    for f in FOCUS_RE.finditer(cmdline):
                        if "status" in f.group(0) and not f.group(2):
                            continue
                        mark(soft_resolve(m, f.group(1)))
                    t["items"].append(("tool", ts, tool_line(b)))
    t["title"] = t["name"] or t["title"] or t["sid"][:8]
    return t


def related_sessions(m):
    """Transcripts of the sessions that worked on this map's threads, and thread -> [session ids]."""
    pdir = projects_dir()
    folder = pdir / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT.parent))
    files = {f.stem: f for f in folder.glob("*.jsonl")} if folder.exists() else {}
    known = {}                                     # sessions the map itself recorded
    for i, n in m["nodes"].items():
        for sid in [*(n.get("sessions") or []), (n.get("session") or {}).get("sid")]:
            if sid:
                known.setdefault(sid, set()).add(i)
                if sid not in files:
                    hit = next(iter(pdir.glob(f"*/{sid}.jsonl")), None)
                    if hit:
                        files[sid] = hit
    out, by_thread = [], {}
    for sid, f in files.items():
        try:
            t = read_transcript(f, m)
        except Exception:
            continue
        for i in known.get(sid, ()):
            if i not in t["threads"]:
                t["threads"].append(i)
        if not t["threads"] or not any(k[0] in ("user", "assistant") for k in t["items"]):
            continue
        out.append(t)
        for i in t["threads"]:
            by_thread.setdefault(i, []).append(sid)
    out.sort(key=lambda t: t["start"] or "")
    for i in by_thread:
        by_thread[i].sort(key=lambda sid: next((t["start"] or "" for t in out if t["sid"] == sid), ""))
    return out, by_thread


def render_transcript(t, link):
    def hm(ts):
        return (ts or "")[11:16]
    fm = ["---", VAULT_MARK, f"session_id: {yq(t['sid'])}", f"title: {yq(t['title'])}",
          f"started: {(t['start'] or '')[:16].replace('T', ' ')}", f"ended: {(t['end'] or '')[:16].replace('T', ' ')}",
          f"folder: {yq(t['cwd'])}", "threads:"] + [f"  - {yq(link(i))}" for i in t["threads"]] + \
         ["tags: [thread-session]", "---", ""]
    body = [f"> [!info] Transcript of Claude Code session `{t['sid']}`. Tool calls are shortened and tool "
            "output is left out. Times are UTC. Read-only export.", "",
            "Threads: " + ", ".join(link(i) for i in t["threads"]), ""]
    tools = []
    def flush():
        if tools:
            body.append(f"> [!example]- {len(tools)} tool call{'s' if len(tools) > 1 else ''}")
            body.extend(f"> - {x}" for x in tools)
            body.append("")
            tools.clear()
    last = None
    for kind, ts, text in t["items"]:
        if kind == "tool":
            tools.append(text)
            continue
        flush()
        if kind == "thread":
            body += [f"## ▶ {link(text)}", ""]
            last = None
            continue
        who = "You" if kind == "user" else "Claude"
        if who != last:
            body.append(f"**{who}** · {hm(ts)}")
            last = who
        body += [text, ""]
    flush()
    return "\n".join(fm + body)


def export_one(vroot, with_sessions):
    m = load()
    remember_map(m)
    cmd_export_quiet = io.StringIO()
    with contextlib.redirect_stdout(cmd_export_quiet):
        cmd_export(["--no-vault"])                 # refresh doc.json (session liveness, briefs)
    doc = json.loads((SYNC / "doc.json").read_text())
    return write_vault(m, doc, vroot, with_sessions)


def cmd_obsidian(args):
    """obsidian [PATH] [--no-sessions] [--all] : export the full map (and its session transcripts) as an
    Obsidian vault.   obsidian auto [PATH] : also refresh it after every change.   obsidian off : stop that."""
    c = gcfg()
    if args and args[0] in ("auto", "off"):
        if args[0] == "auto":
            c["vault_auto"] = True
            if len(args) > 1:
                c["vault"] = args[1]
            c.setdefault("vault", "~/thread-vault")
        else:
            c["vault_auto"] = False
        save_gcfg(c)
        print(json.dumps({"vault": c.get("vault"), "auto": c["vault_auto"]}))
        if args[0] == "off":
            return
        args = []
    with_sessions = "--no-sessions" not in args
    every = "--all" in args
    rest = [a for a in args if not a.startswith("--")]
    vroot = vault_root(rest[0] if rest else None)
    if rest and not c.get("vault"):
        c["vault"] = rest[0]; save_gcfg(c)       # remember the first place used
    results = []
    if every:
        for proj, root in sorted((gcfg().get("maps") or {}).items()):
            if not (pathlib.Path(root) / "map.json").exists():
                continue
            env = dict(os.environ, THREADS_DIR=root)
            r = subprocess.run([sys.executable, __file__, "obsidian", str(vroot)] +
                               ([] if with_sessions else ["--no-sessions"]),
                               env=env, capture_output=True, text=True, timeout=600)
            try:
                results += json.loads(r.stdout)
            except Exception:
                results.append({"project": proj, "error": (r.stderr or r.stdout)[-300:]})
    else:
        with lock():
            res = export_one(vroot, with_sessions)
            res["project"] = load()["project"]
        results.append(res)
    print(json.dumps(results, indent=2))


def cmd_vault(args):
    """vault : same as obsidian (kept for older instructions)."""
    cmd_obsidian(args)


def cmd_synced(args):
    SYNC.mkdir(parents=True, exist_ok=True)
    (SYNC / "state.json").write_text(json.dumps({"version": int(args[0]), "at": now()}))
    print("ok")


def cmd_config(args):
    """config url=<artifact url>   (global)   |   config sync_briefs=false (this project)"""
    for a in args:
        k, _, v = a.partition("=")
        if k == "url":
            c = gcfg(); c["artifact_url"] = v
            save_gcfg(c)
        elif k == "vault":
            c = gcfg()
            if v:
                c["vault"] = v
            else:
                c.pop("vault", None); c["vault_auto"] = False
            save_gcfg(c)
        elif k == "vault_auto":
            c = gcfg(); c["vault_auto"] = v.lower() in ("1", "true", "yes", "on")
            save_gcfg(c)
        elif k == "sync_briefs":
            p = ROOT / "config.json"
            c = json.loads(p.read_text()) if p.exists() else {}
            c["sync_briefs"] = v.lower() in ("1", "true", "yes")
            ROOT.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(c, indent=2))
        else:
            die(f"unknown config key {k}")
    print(json.dumps({"global": gcfg(), "map_dir": str(ROOT)}))


CMDS = {"init": cmd_init, "add": cmd_add, "focus": cmd_focus, "status": cmd_status,
        "summary": cmd_field("summary"), "why": cmd_field("why"), "title": cmd_field("title"),
        "move": cmd_move, "get": cmd_get, "show": cmd_show, "export": cmd_export,
        "synced": cmd_synced, "config": cmd_config, "spawn": cmd_spawn,
        "sessions": cmd_sessions, "release": cmd_release, "vault": cmd_vault, "obsidian": cmd_obsidian,
        "order": cmd_order}

import contextlib
@contextlib.contextmanager
def lock():
    """Several sessions share one map: serialize read-modify-write."""
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        print("commands:", ", ".join(CMDS))
        sys.exit(0 if len(sys.argv) < 2 else 1)
    if sys.argv[1] in ("spawn", "obsidian", "vault"):
        CMDS[sys.argv[1]](sys.argv[2:])   # these take the lock themselves, around each step
    else:
        with lock():
            CMDS[sys.argv[1]](sys.argv[2:])
