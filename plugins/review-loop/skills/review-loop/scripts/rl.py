#!/usr/bin/env python3
"""review-loop state engine.

Owns everything that should be deterministic rather than left to model memory:
snapshots of the tree under review, check execution, the findings ledger,
duplicate/oscillation detection, the convergence decision, the Stop-hook
guard, and the final report.

State lives in <repo>/.review-loop/ (excluded via .git/info/exclude).
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(SKILL_DIR, "reference", "findings.schema.json")
BRIEF_TEMPLATE = os.path.join(SKILL_DIR, "reference", "reviewer-brief.md")

SEVERITIES = ["question", "nit", "minor", "major", "blocker"]  # question: needs author input, never blocks
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
TRIAGE_VERDICTS = ["accept", "reject", "doubtful", "defer", "ask", "duplicate"]  # ask = doubtful with options
CONFIG_FILE = ".review-loop.json"
RL_HOME = os.environ.get("REVIEW_LOOP_HOME") or os.path.join(os.path.expanduser("~"), ".claude", "review-loop")
LEARNINGS_CONTEXT_CAP = 8000
# Why did the author session miss a valid finding? Drives how the author guide is edited.
MISS_CAUSES = {
    "not-covered": "no guidance existed in the author guide or the repo rules",
    "covered-unclear": "guidance existed but was vague, buried, or ambiguous",
    "covered-ignored": "guidance was clear but was not applied while writing",
    "context-gap": "needed knowledge of the code or system that was not looked up",
    "spec-gap": "the requirement itself was unclear (not an instructions problem)",
    "one-off": "a slip with no reusable lesson",
}
DEFAULT_DOUBT_OPTIONS = ["Keep the current design (no change)", "Change it as the reviewer suggests"]
DEFAULT_SIZE_LIMITS = {"commits": 20, "files": 100, "lines": 5000}
GUIDE_NAMES = ("REVIEW.md", "AGENTS.md", "CLAUDE.md", "BUGBOT.md", ".cursor/BUGBOT.md", "CONTRIBUTING.md")
OPEN_STATES = {"untriaged", "accepted"}
GUARD_MAX_BLOCKS = 3
ARTIFACT_GLOBS = ["*__pycache__*", "*.pyc", "*.pyo", ".pytest_cache*", "*.egg-info*", "node_modules*",
                  "dist/*", "build/*", "target/*", "out/*", "coverage*", ".coverage*", "*.o", "*.class",
                  ".mypy_cache*", ".ruff_cache*", ".next/*", ".gradle/*", "*.log"]


# ---------------------------------------------------------------- helpers
def sh(args, cwd=None, env=None, check=True, timeout=None):
    r = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        sys.exit(f"command failed: {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout.strip()


def repo_root(start=None):
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start or os.getcwd(),
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def state_dir(root):
    return os.path.join(root, ".review-loop")


def state_path(root):
    return os.path.join(state_dir(root), "state.json")


def load(root):
    if not root:
        sys.exit("not inside a git repository; cd into the repository under review")
    p = state_path(root)
    if not os.path.exists(p):
        sys.exit("no review-loop state here; run `rl.py init` first")
    with open(p) as f:
        return json.load(f)


def save(root, st):
    p = state_path(root)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, p)


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def round_dir(root, n):
    d = os.path.join(state_dir(root), "rounds", str(n))
    os.makedirs(os.path.join(d, "scratch"), exist_ok=True)
    os.makedirs(os.path.join(d, "checks"), exist_ok=True)
    return d


def fingerprint(f):
    words = sorted(set(re.findall(r"[a-z0-9_]{3,}", (f.get("title", "") + " " + f.get("claim", "")).lower())))[:12]
    key = f"{f.get('file','')}|{f.get('category','')}|{' '.join(words)}"
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def threshold(st):
    return "minor" if st.get("strict") else "major"


def is_blocking(st, f):
    return SEV_RANK[f["severity"]] >= SEV_RANK[threshold(st)]


def findings_in(st, rnd):
    return [f for f in st["findings"] if f["round"] == rnd]


# --------------------------------------------------------------- snapshot
def make_snapshot(root, label):
    """Commit object of the full working tree (tracked + untracked, minus ignored),
    created through a temporary index so the user's index/HEAD are untouched."""
    head = sh(["git", "rev-parse", "HEAD"], cwd=root)
    fd, idx = tempfile.mkstemp(prefix="rl-index-")
    os.close(fd)
    os.unlink(idx)
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    try:
        sh(["git", "read-tree", head], cwd=root, env=env)
        # .review-loop/ is in .git/info/exclude, so `add -A` skips it (and all ignored files)
        sh(["git", "add", "-A"], cwd=root, env=env)
        tree = sh(["git", "write-tree"], cwd=root, env=env)
    finally:
        if os.path.exists(idx):
            os.unlink(idx)
    commit = sh(["git", "commit-tree", tree, "-p", head, "-m", label], cwd=root,
                env=dict(os.environ, GIT_AUTHOR_NAME="review-loop", GIT_AUTHOR_EMAIL="review-loop@local",
                         GIT_COMMITTER_NAME="review-loop", GIT_COMMITTER_EMAIL="review-loop@local"))
    return commit, tree


# --------------------------------------------------------------- commands
def repo_slug(root):
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root, capture_output=True,
                            text=True).stdout.strip()
    return re.sub(r"^.*[:/]([^/]+/[^/]+?)(\.git)?$", r"\1", remote) if remote else os.path.basename(root)


def config_path(root):
    """Repo-level .review-loop.json wins; otherwise a private per-repo file under RL_HOME,
    so a team repository needs no extra file."""
    p = os.path.join(root, CONFIG_FILE)
    if os.path.exists(p):
        return p
    for name in (repo_slug(root).replace("/", "__"), os.path.basename(root)):
        q = os.path.join(RL_HOME, "repos", name + ".json")
        if os.path.exists(q):
            return q
    return None


def load_config(root):
    p = config_path(root)
    if not p:
        return {}
    try:
        return json.load(open(p))
    except Exception as e:
        sys.exit(f"{p} is not valid JSON: {e}")


def review_skill_path(root, name):
    for base in (os.path.join(root, ".claude", "skills"), os.path.join(os.path.expanduser("~"), ".claude", "skills")):
        p = os.path.join(base, name, "SKILL.md")
        if os.path.exists(p):
            return p
    return ""


def change_size(root, base):
    commits = int(sh(["git", "rev-list", "--count", f"{base}..HEAD"], cwd=root) or 0)
    _, tree = make_snapshot(root, "size probe")
    files, lines = 0, 0
    for row in sh(["git", "diff", "--numstat", base, tree], cwd=root).splitlines():
        parts = row.split("\t")
        if len(parts) >= 3:
            files += 1
            lines += sum(int(x) for x in parts[:2] if x.isdigit())
    return {"commits": commits, "files": files, "lines": lines}


def cmd_init(a):
    root = repo_root()
    if not root:
        sys.exit("not inside a git repository")
    cfg = load_config(root)
    if os.path.exists(state_path(root)) and not a.force:
        st = load(root)
        if st["status"] in ("running", "awaiting-user"):
            sys.exit(f"a review-loop is already {st['status']} here (round {st['round']}). "
                     "Use `rl.py status`, or `rl.py init --force` to start over.")
        if any(f.get("lesson") or f.get("miss") for f in st["findings"]) and not st.get("learnings_exported"):
            sys.exit("the previous loop has lessons that were never saved. Run the exit skill "
                     "(`rl.py export-learnings`), or `rl.py init --force` to discard them.")
    os.makedirs(state_dir(root), exist_ok=True)
    excl = sh(["git", "rev-parse", "--git-path", "info/exclude"], cwd=root)
    excl = excl if os.path.isabs(excl) else os.path.join(root, excl)
    os.makedirs(os.path.dirname(excl), exist_ok=True)
    existing = open(excl).read() if os.path.exists(excl) else ""
    if ".review-loop/" not in existing:
        with open(excl, "a") as f:
            f.write("\n.review-loop/\n")

    base = a.base
    if not base:
        cand = None
        for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
            if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], cwd=root,
                              capture_output=True).returncode == 0:
                cand = ref
                break
        base = sh(["git", "merge-base", "HEAD", cand], cwd=root) if cand else "HEAD"
    base = sh(["git", "rev-parse", base], cwd=root)

    intent = ""
    if a.intent_file:
        intent = open(a.intent_file).read().strip()
    elif a.intent:
        intent = a.intent.strip()

    checks = [c.strip() for c in (a.checks or "").split(";;") if c.strip()] or list(cfg.get("checks", []))
    limits = dict(DEFAULT_SIZE_LIMITS, **cfg.get("size_limits", {}))
    size = change_size(root, base)
    over = [k for k in ("commits", "files", "lines") if size[k] > limits[k]]
    st = {
        "version": 1,
        "engine": os.path.abspath(__file__),
        "created": now(),
        "session_id": a.session or "",
        "status": "running",
        "target": a.target or "working tree",
        "base": base,
        "intent": intent or "(No intent supplied. Infer it from commit messages and the diff, and say so.)",
        "max_rounds": a.max_rounds or int(cfg.get("max_rounds", 5)),
        "strict": a.strict or bool(cfg.get("strict", False)),
        "gate": (not a.no_gate) and bool(cfg.get("gate", True)),
        "gate_model": a.gate_model or cfg.get("gate_model", ""),
        "reviewer_model": a.reviewer_model or cfg.get("reviewer_model", ""),
        "size": size,
        "size_limits": limits,
        "checks": checks,
        "review_skill": a.review_skill or cfg.get("review_skill", ""),
        "exit_instructions": cfg.get("exit_instructions", ""),
        "pr": None,
        "round": 0,
        "rounds": {},
        "findings": [],
        "guard_blocks": 0,
        "notes": [],
    }
    save(root, st)
    print(json.dumps({"repo": root, "base": base, "checks": checks, "size": size, "over_limits": over,
                      "reviewer_model": st["reviewer_model"], "gate_model": st["gate_model"],
                      "review_skill": st["review_skill"],
                      "config": config_path(root), "state": state_path(root)}, indent=2))
    if st["review_skill"] and not review_skill_path(root, st["review_skill"]):
        print(f"warning: review skill '{st['review_skill']}' not found under .claude/skills; "
              "reviewers will fall back to the built-in lenses.")
    if over:
        print(f"SIZE GATE: change exceeds {', '.join(f'{k}>{limits[k]}' for k in over)}. "
              "Ask the user before starting the loop.")
    if not checks:
        print("hint: no checks configured; run `rl.py detect-checks` and `rl.py set-checks`.")


CONFIG_KEYS = {"_comment", "checks", "max_rounds", "strict", "gate", "gate_model", "reviewer_model",
               "size_limits", "review_skill", "exit_instructions", "reply_prefix",
               "go_module", "go_tags", "go_reverse_depth"}


def cmd_config(a):
    """Show the settings a loop in this repository would use, and what is wrong with them.
    Needs no loop state, so the setup skill can verify a new config before the first loop."""
    root = repo_root()
    if not root:
        sys.exit("not inside a git repository")
    p, cfg = config_path(root), load_config(root)
    problems = [f"unknown key '{k}'" for k in sorted(set(cfg) - CONFIG_KEYS)]
    if not isinstance(cfg.get("checks", []), list):
        problems.append("'checks' must be a list of shell commands")
    name = cfg.get("review_skill")
    if name and not review_skill_path(root, name):
        problems.append(f"review skill '{name}' not found in .claude/skills/ or ~/.claude/skills/")
    unknown_limits = set(cfg.get("size_limits", {})) - set(DEFAULT_SIZE_LIMITS)
    if unknown_limits:
        problems.append(f"unknown size_limits key(s): {', '.join(sorted(unknown_limits))}")
    out = {"repo": root, "slug": repo_slug(root), "config": p,
           "private_config": os.path.join(RL_HOME, "repos", repo_slug(root).replace("/", "__") + ".json"),
           "settings": cfg, "review_skill_path": review_skill_path(root, name) if name else "",
           "guide_store": guide_store(), "guide_file": guide_file(root), "problems": problems}
    if a.probe:
        out["probe"] = []
        checks = cfg.get("checks", []) if isinstance(cfg.get("checks", []), list) else []
        if any(GO_PLACEHOLDER in c for c in checks):
            # Scoped checks expand per round from loop state; here, prove `go list` works for them.
            mod = os.path.join(root, cfg.get("go_module", ""))
            tags = cfg.get("go_tags", "")
            try:
                r = subprocess.run(["go", "list"] + (["-tags", tags] if tags else []) + ["./..."], cwd=mod,
                                   capture_output=True, text=True, timeout=a.timeout)
                ok, tail = r.returncode == 0, (r.stderr or "").strip()[-300:]
            except (OSError, subprocess.TimeoutExpired) as e:
                ok, tail = False, str(e)[-300:]
            out["probe"].append({"command": f"go list ./... in {os.path.relpath(mod, root)} (for {GO_PLACEHOLDER})",
                                 "result": "PASS" if ok else "ENV-ERROR", "exit_code": 0 if ok else 1, "tail": tail})
            if not ok:
                problems.append(f"{GO_PLACEHOLDER} cannot be computed: go list failed; check go_module / go_tags")
        for c in checks:
            if GO_PLACEHOLDER in c:
                out["probe"].append({"command": c, "result": "SCOPED", "exit_code": None,
                                     "tail": "runs on the changed packages during a loop"})
                continue
            try:
                r = subprocess.run(c, shell=True, cwd=root, capture_output=True, text=True, timeout=a.timeout)
                code, outp = r.returncode, r.stdout + r.stderr
            except subprocess.TimeoutExpired:
                code, outp = 124, ""
            env_err = code != 0 and (code == 127 or bool(ENV_ERROR_RE.search(outp)))
            tag = "ENV-ERROR" if env_err else "TIMEOUT" if code == 124 else "PASS" if code == 0 else "FAIL"
            out["probe"].append({"command": c, "result": tag, "exit_code": code, "tail": outp.strip()[-300:]})
            if env_err:
                problems.append(f"check cannot run here (missing tool or module): {c}")
    print(json.dumps(out, indent=2))
    if not p:
        print("no settings found: write .review-loop.json at the repository root, or "
              f"{out['private_config']}", file=sys.stderr)
    return 1 if problems else 0


def cmd_set_intent(a):
    root = repo_root()
    st = load(root)
    if st["round"] != 0:
        sys.exit("intent can only change before round 1, so every reviewer sees the same requirements")
    st["intent"] = open(a.file).read().strip()
    save(root, st)
    print(f"intent updated ({len(st['intent'])} chars)")


def cmd_detect_checks(a):
    root = repo_root()
    out = []
    pj = os.path.join(root, "package.json")
    if os.path.exists(pj):
        try:
            scripts = json.load(open(pj)).get("scripts", {})
        except Exception:
            scripts = {}
        runner = "pnpm" if os.path.exists(os.path.join(root, "pnpm-lock.yaml")) else \
                 "yarn" if os.path.exists(os.path.join(root, "yarn.lock")) else "npm run"
        for k in ("typecheck", "lint", "test", "build"):
            if k in scripts:
                out.append(f"{runner} {k}")
    if os.path.exists(os.path.join(root, "pyproject.toml")) or os.path.exists(os.path.join(root, "setup.py")):
        for tool, cmd in (("ruff", "ruff check ."), ("mypy", "mypy ."), ("pytest", "pytest -q")):
            if subprocess.run(["which", tool], capture_output=True).returncode == 0:
                out.append(cmd)
    if os.path.exists(os.path.join(root, "go.mod")):
        out += ["go vet ./...", "go test ./..."]
    if os.path.exists(os.path.join(root, "Cargo.toml")):
        out += ["cargo clippy --all-targets -- -D warnings", "cargo test"]
    for build in ("pom.xml",):
        if os.path.exists(os.path.join(root, build)):
            out.append("mvn -q -o verify")
    if os.path.exists(os.path.join(root, "build.gradle")) or os.path.exists(os.path.join(root, "build.gradle.kts")):
        out.append("./gradlew check")
    mk = os.path.join(root, "Makefile")
    if os.path.exists(mk):
        targets = re.findall(r"^(lint|test|check|typecheck):", open(mk).read(), re.M)
        out += [f"make {t}" for t in targets]
    print(json.dumps(out, indent=2))
    print("note: large repos (e.g. C++ monorepos) need targeted commands; prefer those over full suites.",
          file=sys.stderr)


def cmd_set_checks(a):
    root = repo_root()
    st = load(root)
    st["checks"] = [c.strip() for c in a.checks.split(";;") if c.strip()]
    save(root, st)
    print(json.dumps(st["checks"], indent=2))


def cmd_snapshot(a):
    root = repo_root()
    st = load(root)
    if st["round"] > 0:
        cur = st["rounds"][str(st["round"])]
        missing = [x for x in cur["reviewers"] if x not in cur.get("verdicts", {}) and x not in cur.get("dropped", {})]
        if missing:
            sys.exit(f"round {st['round']} reviewer output not ingested: {', '.join(missing)}. Ingest it, or "
                     "`rl.py drop-reviewer NAME --reason ...` to record why it is excluded.")
        pending = [f["id"] for f in findings_in(st, st["round"]) if f["status"] == "untriaged"]
        if pending:
            sys.exit(f"round {st['round']} still has untriaged findings: {', '.join(pending)}")
    n = st["round"] + 1
    commit, tree = make_snapshot(root, f"review-loop round {n}")
    prev = st["rounds"].get(str(n - 1), {}).get("snapshot")
    if prev and st["rounds"][str(n - 1)].get("tree") == tree:
        print(f"warning: tree unchanged since round {n-1}.", file=sys.stderr)
    sh(["git", "update-ref", f"refs/review-loop/round-{n}", commit], cwd=root)
    st["round"] = n
    st["guard_blocks"] = 0
    st["rounds"][str(n)] = {"snapshot": commit, "tree": tree, "started": now(),
                            "kind": a.kind, "reviewers": [], "checks_ok": None}
    save(root, st)
    round_dir(root, n)
    print(json.dumps({"round": n, "snapshot": commit, "previous": prev, "kind": a.kind}, indent=2))


def need_round(st):
    if st["round"] == 0:
        sys.exit("no round started; run `rl.py snapshot` first")
    return st["round"]


# ------------------------------------------------------------ go test scope
GO_PLACEHOLDER = "{go_packages}"


def go_module_dir(root, cfg, changed):
    if cfg.get("go_module"):
        return os.path.join(root, cfg["go_module"])
    counts = {}
    for f in changed:
        if not f.endswith(".go"):
            continue
        d = os.path.dirname(os.path.join(root, f))
        while d.startswith(root):
            if os.path.exists(os.path.join(d, "go.mod")):
                counts[d] = counts.get(d, 0) + 1
                break
            d = os.path.dirname(d)
    return max(counts, key=counts.get) if counts else None


def go_scope(root, st):
    """The packages scoped checks run on: changed packages, their importers (to `go_reverse_depth`),
    and packages the author declared. The author can widen the scope, never narrow it."""
    cfg = load_config(root)
    if st["round"]:
        changed = sh(["git", "diff", "--name-only", st["base"], st["rounds"][str(st["round"])]["snapshot"]],
                     cwd=root).splitlines()
    else:  # before round 1: the working tree, untracked files included
        changed = sh(["git", "diff", "--name-only", st["base"]], cwd=root).splitlines() + \
            sh(["git", "ls-files", "--others", "--exclude-standard"], cwd=root).splitlines()
    mod = go_module_dir(root, cfg, changed)
    if not mod:
        return {"module": None, "packages": [], "why": {}, "error": ""}
    tags = cfg.get("go_tags", "")
    r = subprocess.run(["go", "list"] + (["-tags", tags] if tags else []) + ["-e", "-f",
                       "{{.ImportPath}}|{{.Dir}}|{{join .Imports \" \"}} {{join .TestImports \" \"}} "
                       "{{join .XTestImports \" \"}}", "./..."], cwd=mod, capture_output=True, text=True)
    if r.returncode != 0:
        return {"module": os.path.relpath(mod, root), "packages": [], "why": {}, "error": r.stderr.strip()[:500]}
    pkgs, imports = {}, {}
    for line in r.stdout.splitlines():
        ip, d, imps = (line.split("|") + ["", ""])[:3]
        pkgs[ip] = d
        imports[ip] = set(imps.split())
    by_dir = {d: ip for ip, d in pkgs.items()}
    why = {}
    for f in changed:
        d = os.path.dirname(os.path.join(root, f))
        if f.endswith(".go") and d in by_dir:
            why.setdefault(by_dir[d], "changed")
    frontier = set(why)
    for _ in range(int(cfg.get("go_reverse_depth", 1))):
        nxt = {ip for ip, imps in imports.items() if imps & frontier and ip not in why}
        for ip in nxt:
            why[ip] = "imports changed code"
        frontier = nxt
    rel_to_ip = {"./" + os.path.relpath(d, mod) if d != mod else ".": ip for ip, d in pkgs.items()}
    for p in st.get("scope_declared", []):
        ip = p if p in pkgs else rel_to_ip.get(norm_pkg(p))
        if ip:
            why.setdefault(ip, "declared by author")
    rel = lambda ip: "./" + os.path.relpath(pkgs[ip], mod) if pkgs[ip] != mod else "."
    ordered = sorted(why, key=lambda ip: (["changed", "imports changed code", "declared by author"].index(why[ip]), ip))
    return {"module": os.path.relpath(mod, root), "packages": [rel(ip) for ip in ordered],
            "why": {rel(ip): why[ip] for ip in ordered}, "error": "", "known": sorted(rel_to_ip)}


def is_import_path(p):
    return "." in p.split("/")[0] and not p.startswith(".")


def norm_pkg(p):
    p = p.rstrip("/")
    if is_import_path(p) or p == "." or p.startswith("./"):
        return p
    return "./" + p


def cmd_scope(a):
    """Author-declared test scope: packages affected in ways the import graph cannot see
    (interfaces, registries, reflection, shared config or SQL, tests elsewhere)."""
    root = repo_root()
    st = load(root)
    if a.add:
        cur = go_scope(root, st)
        known = set(cur.get("known", []))
        norm = [norm_pkg(p) for p in a.add]
        bad = [p for p in norm if p not in known and not is_import_path(p)]
        if bad:
            sys.exit(f"not a package in {cur['module']}: {', '.join(bad)}")
        st.setdefault("scope_declared", [])
        st["scope_declared"] += [p for p in norm if p not in st["scope_declared"]]
        if a.note:
            st.setdefault("scope_notes", []).append(a.note)
        save(root, st)
    sc = go_scope(root, st)
    if sc["error"]:
        print(f"go list failed in {sc['module']}: {sc['error']}")
    print(f"module: {sc['module'] or '(no Go change)'}  packages in scope: {len(sc['packages'])}")
    for p in sc["packages"]:
        print(f"  {p:<40} {sc['why'][p]}")


ENV_ERROR_RE = re.compile(r"No module named|command not found|not recognized as an internal|"
                          r"Cannot find module|ENOENT|No such file or directory: '|executable file not found|"
                          r"Unknown command|is not installed", re.I)


def cmd_run_checks(a):
    """Run configured checks. With --probe (allowed before round 1) it only validates
    that each check can run in this environment and records nothing in the round state."""
    root = repo_root()
    st = load(root)
    n = st["round"] if a.probe else need_round(st)
    d = os.path.join(round_dir(root, n), "checks" if not a.probe else "probe")
    os.makedirs(d, exist_ok=True)
    results = []
    scope = go_scope(root, st) if any(GO_PLACEHOLDER in c for c in st["checks"]) else None
    for i, c in enumerate(st["checks"]):
        log = os.path.join(d, f"{i:02d}.log")
        if scope is not None and GO_PLACEHOLDER in c:
            if scope["error"]:
                with open(log, "w") as f:
                    f.write(f"$ {c}\n(go list failed)\n\n{scope['error']}")
                results.append({"command": c, "exit_code": 127, "log": log, "warning_mentions": 0, "env_error": True})
                continue
            if not scope["packages"]:
                with open(log, "w") as f:
                    f.write(f"$ {c}\n(skipped: no Go package in scope)\n")
                results.append({"command": c, "exit_code": 0, "log": log, "warning_mentions": 0,
                                "env_error": False, "skipped": True})
                continue
            c = c.replace(GO_PLACEHOLDER, " ".join(scope["packages"]))
        try:
            r = subprocess.run(c, shell=True, cwd=root, capture_output=True, text=True, timeout=a.timeout)
            code, outp = r.returncode, r.stdout + "\n--- stderr ---\n" + r.stderr
        except subprocess.TimeoutExpired as e:
            code, outp = 124, f"TIMEOUT after {a.timeout}s\n{e.stdout or ''}"
        with open(log, "w") as f:
            f.write(f"$ {c}\n(exit {code})\n\n{outp}")
        warn = len(re.findall(r"\bwarn(ing)?\b", outp, re.I))
        env_err = code != 0 and (code == 127 or bool(ENV_ERROR_RE.search(outp)))
        results.append({"command": c, "exit_code": code, "log": log, "warning_mentions": warn,
                        "env_error": env_err})
    ok = all(r["exit_code"] == 0 for r in results)
    broken = [r["command"] for r in results if r["env_error"]]
    summary = {"round": n, "all_passed": ok, "configured": bool(st["checks"]), "env_errors": broken,
               "results": results}
    if scope is not None:
        summary["scope"] = {k: scope[k] for k in ("module", "packages", "why")}
    with open(os.path.join(d, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    if not a.probe:
        st["rounds"][str(n)]["checks_ok"] = ok if st["checks"] else None
        st["rounds"][str(n)]["checks_broken"] = broken
        if scope is not None:
            st["rounds"][str(n)]["scope"] = summary["scope"]
        save(root, st)
    for r in results:
        tag = "ENV-ERROR" if r["env_error"] else ("SKIP" if r.get("skipped") else "PASS" if r["exit_code"] == 0 else "FAIL")
        print(f"[{tag}] exit={r['exit_code']} warn~{r['warning_mentions']}  {r['command']}")
    if broken:
        print("Some checks cannot run in this environment (missing tool/module). Replace them with "
              "`rl.py set-checks` and re-run; they would otherwise block convergence forever.")
    if scope is not None and scope["packages"]:
        print(f"scope ({scope['module']}): " + ", ".join(f"{p} [{scope['why'][p]}]" for p in scope["packages"]))
    if not st["checks"]:
        print("no checks configured; reviewers will discover and run checks themselves.")
    print(f"summary: {os.path.join(d, 'summary.json')}")


EXAMPLE_OUTPUT = """{
  "reviewer": "NAME", "round": ROUND, "verdict": "issues_found",
  "summary": "Two or three sentences.",
  "checks_run": [{"command": "pytest -q", "exit_code": 1, "outcome": "1 failure in test_x"}],
  "intent_gaps": [],
  "findings": [{
    "severity": "major", "confidence": "high", "category": "correctness",
    "file": "src/a.py", "line": 42, "title": "Off-by-one drops the last item",
    "claim": "range(len(xs) - 1) skips the final element.",
    "evidence": "a.py:42 `for i in range(len(xs) - 1):`; repro printed [1, 2] for [1, 2, 3]",
    "failure_scenario": "process([1, 2, 3]) returns 2 items instead of 3",
    "suggested_fix": "Iterate over range(len(xs)).", "reproduced": true
  }]
}"""

MODE_TEXT = {
    "full": ("Review the **entire change** from base to snapshot as if seeing it for the first time.", ""),
    "delta": ("Review **only what changed since the previous round** (the fix delta), and judge whether "
              "those fixes are correct, complete, and free of regressions. Read the full diff only for context.",
              "git diff {prev} {snap}   # the fix delta since the previous round"),
    "gate": ("This is the **final gate review** in a brand-new session. Review the entire change from base "
             "to snapshot with maximum rigour. Several earlier reviews claim it is clean; do not trust that.", ""),
}

DEFAULT_LENS = {
    "full": "General correctness first: logic, edge cases, error handling, contracts, tests that truly assert behaviour.",
    "delta": "Regressions and incomplete fixes introduced by the most recent edits.",
    "gate": "Everything. Prioritise defects that would cause wrong results, crashes, data loss, or security exposure.",
}


def review_guides(root, base, snapshot):
    """Repository review guides that govern the touched files: each guide name found in
    the repo root or in any ancestor directory of a changed file."""
    changed = sh(["git", "diff", "--name-only", base, snapshot], cwd=root).splitlines()
    dirs = {""}
    for path in changed:
        d = os.path.dirname(path)
        while d:
            dirs.add(d)
            d = os.path.dirname(d)
    found, seen = [], set()
    for d in sorted(dirs, key=lambda x: (x.count("/"), x)):
        for g in GUIDE_NAMES:
            rel = os.path.join(d, g) if d else g
            full = os.path.join(root, rel)
            if os.path.isfile(full) and os.path.realpath(full) not in seen:  # symlinked copies count once
                seen.add(os.path.realpath(full))
                found.append(rel)
    return found


def cmd_brief(a):
    root = repo_root()
    st = load(root)
    n = need_round(st)
    r = st["rounds"][str(n)]
    prev = st["rounds"].get(str(n - 1), {}).get("snapshot", st["base"])
    d = round_dir(root, n)
    out_json = os.path.join(d, f"{a.name}.json")
    mode_txt, delta_cmd = MODE_TEXT[a.mode]
    if a.mode == "gate":
        output_section = ("Return your result as the structured output (the session enforces the JSON schema "
                          f"in `{SCHEMA_PATH}`). Set `\"reviewer\": \"{a.name}\"` and `\"round\": {n}`.")
    else:
        output_section = (f"Write your result as JSON matching `{SCHEMA_PATH}` to:\n\n`{out_json}`\n\n"
                          "Use **exactly** these field names (no others, none missing):\n\n"
                          "```json\n" + EXAMPLE_OUTPUT.replace("NAME", a.name).replace("ROUND", str(n)) + "\n```\n\n"
                          "Use a quoted heredoc so the shell does not mangle the content:\n\n"
                          f"```\ncat > {out_json} <<'JSON_EOF'\n{{ ...your JSON... }}\nJSON_EOF\n```\n\n"
                          f"Set `\"reviewer\": \"{a.name}\"` and `\"round\": {n}`. Then reply with one line: "
                          "the verdict and the count of findings by severity.")
    rep = {
        "ROUND": str(n), "NAME": a.name, "REPO": root, "BASE": st["base"], "SNAPSHOT": r["snapshot"],
        "MODE": a.mode, "MODE_INSTRUCTIONS": mode_txt,
        "DELTA_COMMAND": delta_cmd.format(prev=prev, snap=r["snapshot"]) if delta_cmd else "",
        "INTENT": st["intent"], "LENS": a.lens or DEFAULT_LENS[a.mode],
        "CHECKS_PATH": os.path.join(d, "checks"), "SCRATCH": os.path.join(d, "scratch"),
        "SCHEMA_PATH": SCHEMA_PATH, "OUTPUT_PATH": out_json, "OUTPUT_SECTION": output_section,
    }
    guides = review_guides(root, st["base"], r["snapshot"])
    rep["GUIDES"] = ("\n".join(f"- `{g}`" for g in guides) if guides else
                     "- (none found; apply general engineering judgement)")
    rep["REVIEW_SKILL"] = review_skill_section(root, st, a.mode, prev, r["snapshot"])
    sc = r.get("scope")
    rep["SCOPE"] = ("" if not sc else
        "\n**Scoped tests.** The orchestrator ran the Go checks on these packages of module "
        f"`{sc['module']}` (see `scope` in `summary.json`):\n\n"
        + "\n".join(f"- `{p}` ({w})" for p, w in sc["why"].items()) +
        "\n\nDo not re-run whole-module suites; read the logs instead. Run a targeted test only to "
        "reproduce a suspected defect. If a package affected by this change is missing from the list "
        "(it reaches the changed code through an interface, registry, reflection, generated code, shared "
        "config or SQL, or its tests exercise the changed behaviour), report a `test-gap` finding whose "
        "`claim` names the package; it will be added to the scope.\n")
    text = open(BRIEF_TEMPLATE).read()
    for k, v in rep.items():
        text = text.replace("{{" + k + "}}", v)
    bp = os.path.join(d, f"brief-{a.name}.md")
    with open(bp, "w") as f:
        f.write(text)
    if a.name not in r["reviewers"]:
        r["reviewers"].append(a.name)
    save(root, st)
    print(json.dumps({"brief": bp, "output": out_json}, indent=2))


def review_skill_section(root, st, mode, prev, snap):
    name = st.get("review_skill")
    path = review_skill_path(root, name) if name else ""
    if not path:
        return "(No team review skill is configured; use the lens and the guides above.)"
    rng = f"{prev} {snap}" if mode == "delta" else f"{st['base']} {snap}"
    return (f"This team reviews with the `{name}` skill. Invoke it with the Skill tool (or read `{path}` "
            "if the Skill tool does not list it) and apply its method in full: the standards walk, the "
            "active rule checklist and mechanical checks, and every review lens.\n\n"
            "Adapt it to this loop:\n\n"
            f"- Review the range `git diff {rng}`. Wherever the skill says `main`, `MERGE_BASE`, or `HEAD`, "
            "use those two commits instead. The working tree matches the snapshot, including uncommitted files.\n"
            "- Skip its change-summary step and its output and posting steps. Write the JSON described under "
            "**Output** instead.\n"
            "- Map its classes: CRITICAL -> `blocker`, WARNING -> `major`, SUGGESTION -> `minor`, "
            "QUESTION -> `question`. Cite the rule file in `evidence` when a standard drove the finding.\n"
            "- If a tool it names is not installed, skip that step and say so in `summary`.")


FIELD_ALIASES = {"suggestion": "suggested_fix", "fix": "suggested_fix", "recommendation": "suggested_fix",
                 "description": "claim", "details": "claim", "issue": "claim", "problem": "claim",
                 "scenario": "failure_scenario", "impact": "failure_scenario", "repro": "failure_scenario",
                 "path": "file", "line_number": "line", "summary": "title"}


def normalise(doc):
    """Repair common reviewer slips (aliased or missing narrative fields) and report each repair.
    Structural fields (severity, file, title) are never invented."""
    notes = []
    for i, f in enumerate(doc.get("findings", [])):
        for alias, canon in FIELD_ALIASES.items():
            if alias in f and canon not in f:
                f[canon] = f.pop(alias)
                notes.append(f"finding {i}: '{alias}' -> '{canon}'")
        if "claim" not in f and f.get("title"):
            f["claim"] = f["title"]
            notes.append(f"finding {i}: claim defaulted to title")
        if "failure_scenario" not in f:
            f["failure_scenario"] = "(not stated by reviewer)"
            notes.append(f"finding {i}: failure_scenario missing")
        f.setdefault("evidence", "(not stated by reviewer)")
        f.setdefault("confidence", "medium")
        if isinstance(f.get("severity"), str):
            f["severity"] = f["severity"].lower()
        try:
            f["line"] = int(f.get("line") or 0)
        except (TypeError, ValueError):
            f["line"] = 0
    doc.setdefault("checks_run", [])
    return notes


def validate(doc):
    errs = []
    for k in ("reviewer", "round", "verdict", "findings"):
        if k not in doc:
            errs.append(f"missing top-level '{k}'")
    for i, f in enumerate(doc.get("findings", [])):
        for k in ("severity", "confidence", "category", "file", "line", "title", "claim", "evidence", "failure_scenario"):
            if k not in f:
                errs.append(f"finding {i}: missing '{k}'")
        if f.get("severity") not in SEVERITIES:
            errs.append(f"finding {i}: bad severity {f.get('severity')!r}")
    return errs


def change_map(root, base, snapshot):
    """{path: [(start, end), ...]} of added/changed line ranges in the snapshot (zero-context diff)."""
    ranges, cur = {}, None
    for line in sh(["git", "diff", "-U0", "--no-color", base, snapshot], cwd=root).splitlines():
        if line.startswith("+++ "):
            cur = None if line[4:] == "/dev/null" else line[6:] if line.startswith("+++ b/") else line[4:]
            if cur is not None:
                ranges.setdefault(cur, [])
        elif line.startswith("--- a/"):
            ranges.setdefault(line[6:], [])  # deleted or modified files count as changed
        elif line.startswith("@@") and cur is not None:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                ranges[cur].append((start, start + max(count, 1) - 1))
    return ranges


def locate(f, cmap, slack=5):
    """Changed-hunk validation, as a flag rather than a filter: is the finding in the change?"""
    if f["file"] not in cmap:
        return "outside-change"
    line = int(f.get("line") or 0)
    if line == 0 or any(a - slack <= line <= b + slack for a, b in cmap[f["file"]]):
        return "in-change"
    return "near-change"


def cmd_ingest(a):
    root = repo_root()
    st = load(root)
    n = need_round(st)
    doc = json.load(open(a.file))
    if "structured_output" in doc:  # raw `claude -p --output-format json`
        doc = doc["structured_output"] or {}
    repairs = normalise(doc)
    errs = validate(doc)
    if errs:
        sys.exit("invalid reviewer output:\n  " + "\n  ".join(errs) +
                 "\nSend these errors to the same reviewer (SendMessage) and ask it to rewrite its file, or "
                 "re-run it. Do not skip it silently; if you must, run `rl.py drop-reviewer NAME --reason ...`.")
    if repairs:
        print(f"note: repaired {len(repairs)} reviewer slip(s): " + "; ".join(repairs[:6]) +
              (" ..." if len(repairs) > 6 else ""))
    name = a.reviewer or doc["reviewer"]
    if any(f["round"] == n and f["reviewer"] == name for f in st["findings"]):
        sys.exit(f"findings from {name} in round {n} were already ingested")
    prior = [f for f in st["findings"] if f["round"] < n]
    cmap = change_map(root, st["base"], st["rounds"][str(n)]["snapshot"])
    seq = len(findings_in(st, n))
    added = []
    for f in doc["findings"]:
        seq += 1
        f = dict(f)
        f.update({"id": f"R{n}-{seq}", "round": n, "reviewer": name, "status": "untriaged",
                  "fingerprint": fingerprint(f), "triage_note": "", "duplicate_of": "",
                  "verifier": "", "recurrences": 0, "options": [], "recommendation": ""})
        f["location"] = locate(f, cmap)
        pool = prior + [p for p in st["findings"] if p["round"] == n and p["reviewer"] != name]
        hints = [p["id"] for p in pool if p["fingerprint"] == f["fingerprint"]]
        hints += [p["id"] for p in pool if p["id"] not in hints and p["file"] == f["file"]
                  and abs(int(p.get("line") or 0) - int(f.get("line") or 0)) <= 15
                  and p["category"] == f["category"]]
        f["possible_duplicates"] = hints
        st["findings"].append(f)
        added.append(f)
    st["rounds"][str(n)].setdefault("verdicts", {})[name] = {
        "verdict": doc["verdict"], "summary": doc.get("summary", ""),
        "checks_run": doc.get("checks_run", []), "intent_gaps": doc.get("intent_gaps", [])}
    save(root, st)
    print(f"{name}: verdict={doc['verdict']}, {len(added)} finding(s)")
    for f in sorted(added, key=lambda x: -SEV_RANK[x["severity"]]):
        dup = f"  ~dup? {','.join(f['possible_duplicates'])}" if f["possible_duplicates"] else ""
        dup += "  [outside change]" if f["location"] == "outside-change" else ""
        print(f"  {f['id']:<7} {f['severity']:<7} {f['confidence']:<6} {f['category']:<15} "
              f"{f['file']}:{f['line']}  {f['title']}{dup}")
    for g in doc.get("intent_gaps", []):
        print(f"  intent gap: {g}")


def find(st, fid):
    for f in st["findings"]:
        if f["id"] == fid:
            return f
    sys.exit(f"unknown finding {fid}")


def cmd_triage(a):
    root = repo_root()
    st = load(root)
    f = find(st, a.id)
    v = a.verdict
    needs_verifier = SEV_RANK[f["severity"]] >= SEV_RANK["major"] or \
        (f["severity"] == "minor" and f.get("reproduced") is True)
    if v == "reject" and needs_verifier and a.verifier != "not-real":
        sys.exit(f"{f['id']} is {f['severity']}" + (" and reproduced" if f.get("reproduced") else "") +
                 ": rejecting it requires an independent "
                 "verifier verdict. Run the finding-verifier agent, then pass --verifier not-real "
                 "(or triage as accept/ask per its verdict).")
    if v in ("reject", "doubtful", "ask", "defer") and not a.note:
        sys.exit(f"`{v}` needs --note: it is the reason the final report (and any PR reply) will show")
    if v == "ask" and a.option and len(a.option) < 2:
        sys.exit("give at least two --option values, or none to use the defaults")
    f["triage_note"] = a.note or ""
    if a.verifier:
        f["verifier"] = a.verifier
    if v == "duplicate":
        if not a.duplicate_of:
            sys.exit("--duplicate-of is required")
        orig = find(st, a.duplicate_of)
        seen = set()
        while orig.get("duplicate_of") and orig["id"] not in seen:  # resolve to the root finding
            seen.add(orig["id"])
            orig = find(st, orig["duplicate_of"])
        f["duplicate_of"] = orig["id"]
        if orig["round"] == f["round"]:
            # same-round duplicate (another reviewer saw the same thing): never a regression
            f["status"] = "duplicate"
            save(root, st)
            print(f"{f['id']} duplicates {orig['id']} from the same round -> dropped")
            return
        if orig["status"] == "fixed":
            orig["recurrences"] += 1
            if orig["recurrences"] >= 2:
                f["status"] = "question"
                f["options"] = [f"Keep the current fix for {orig['id']} and ignore this report",
                                f"Revisit {orig['id']}: the fix is not holding (recurred {orig['recurrences']}x)"]
                f["recommendation"] = ""
                save(root, st)
                print(f"OSCILLATION: {orig['id']} has recurred {orig['recurrences']}x after being fixed. "
                      f"{f['id']} converted to a question for the user. Do not fix it again in this loop.")
                return
            # recurrence of a fixed issue = regression: must be fixed again
            f["status"] = "accepted"
            print(f"{f['id']} duplicates FIXED {orig['id']}: treated as a regression -> accepted")
        elif orig["status"] in ("rejected", "deferred", "question"):
            f["status"] = "duplicate"
            print(f"{f['id']} duplicates {orig['status']} {orig['id']} -> dropped")
        else:
            f["status"] = "duplicate"
            print(f"{f['id']} duplicates {orig['id']} ({orig['status']}) -> dropped")
    elif v == "accept":
        f["status"] = "accepted"
    elif v == "reject":
        f["status"] = "rejected"
    elif v == "defer":
        f["status"] = "deferred"
    elif v in ("doubtful", "ask"):
        # possible design difference: the code is kept as is and the user decides from the report
        f["status"] = "question"
        f["doubt"] = True
        f["options"] = a.option or list(DEFAULT_DOUBT_OPTIONS)
        f["recommendation"] = a.recommend or ("A" if not a.option else "")
    save(root, st)
    if v != "duplicate":
        print(f"{f['id']} -> {f['status']}")


def cmd_drop_reviewer(a):
    root = repo_root()
    st = load(root)
    r = st["rounds"][str(need_round(st))]
    if a.name not in r["reviewers"]:
        sys.exit(f"{a.name} is not a reviewer in this round")
    r.setdefault("dropped", {})[a.name] = a.reason
    st["notes"].append(f"round {st['round']}: dropped reviewer {a.name}: {a.reason}")
    save(root, st)
    print(f"dropped {a.name}; this is recorded in the report")


def cmd_answer(a):
    root = repo_root()
    st = load(root)
    f = find(st, a.id)
    if f["status"] != "question":
        sys.exit(f"{a.id} is {f['status']}, not a question")
    idx = ord(a.choice.strip().upper()[:1]) - 65
    if a.choice.strip().upper()[:1].isalpha() and 0 <= idx < len(f["options"]):
        f["answer"] = {"choice": chr(65 + idx), "text": f["options"][idx], "note": a.note or ""}
    else:
        f["answer"] = {"choice": "other", "text": a.choice, "note": a.note or ""}
    f["status"] = "answered"
    save(root, st)
    print(f"{a.id} answered: {f['answer']['choice']} - {f['answer']['text']}")


def cmd_fixed(a):
    root = repo_root()
    st = load(root)
    for fid in a.ids:
        f = find(st, fid)
        if f["status"] != "accepted":
            sys.exit(f"{fid} is {f['status']}, not accepted")
        f["status"] = "fixed"
        f["fixed_in_round"] = st["round"]
        if a.note:
            f["fix_note"] = a.note
        if a.lesson:
            f["lesson"] = a.lesson
        if a.cause:
            f["miss"] = {"cause": a.cause, "why": a.why or "", "guide_ref": a.guide_ref or ""}
        st["learnings_exported"] = False
    save(root, st)
    print(f"marked fixed: {' '.join(a.ids)}" + ("" if a.cause else
          "  (no --cause: record why you missed it with `rl.py miss ID --cause ... --why ...` while you remember)"))


def cmd_miss(a):
    """Record the author session's own diagnosis of why it missed a finding."""
    root = repo_root()
    st = load(root)
    f = find(st, a.id)
    if f["status"] not in ("fixed", "answered"):
        sys.exit(f"{a.id} is {f['status']}; miss analysis applies to fixed findings and answered design questions")
    if a.cause in ("covered-unclear", "covered-ignored") and not a.guide_ref:
        sys.exit(f"--cause {a.cause} needs --guide-ref: quote the section or rule that already covered it")
    f["miss"] = {"cause": a.cause, "why": a.why, "guide_ref": a.guide_ref or ""}
    st["learnings_exported"] = False
    save(root, st)
    print(f"{a.id}: missed because {a.cause}")


def cmd_lesson(a):
    """Attach a reusable lesson (pattern or design choice, not the instance) to a finding."""
    root = repo_root()
    st = load(root)
    f = find(st, a.id)
    if f["status"] not in ("fixed", "answered", "rejected", "question"):
        sys.exit(f"{a.id} is {f['status']}; lessons belong to fixed, answered, rejected, or doubtful findings")
    f["lesson"] = a.text
    st["learnings_exported"] = False
    save(root, st)
    print(f"{a.id}: lesson recorded")


def decide(st):
    n = st["round"]
    if n == 0:
        return "SNAPSHOT", "No round started yet."
    cur = findings_in(st, n)
    r = st["rounds"][str(n)]
    reported = set(r.get("verdicts", {}))
    if not r["reviewers"]:
        return "REVIEW", f"Round {n} has no reviewers yet: generate briefs and spawn them (or run the gate)."
    waiting = [x for x in r["reviewers"] if x not in reported and x not in r.get("dropped", {})]
    if waiting:
        return "REVIEW", "Waiting for reviewer output to be ingested: " + ", ".join(waiting)
    untriaged = [f["id"] for f in cur if f["status"] == "untriaged"]
    if untriaged:
        return "TRIAGE", "Untriaged: " + ", ".join(untriaged)
    unfixed = [f for f in st["findings"] if f["status"] == "accepted"]
    blocking_unfixed = [f["id"] for f in unfixed if is_blocking(st, f)]
    nonblocking_unfixed = [f["id"] for f in unfixed if not is_blocking(st, f)]
    blocking_this_round = [f for f in cur if f["status"] in ("accepted", "fixed") and is_blocking(st, f)]
    checks_failed = r.get("checks_ok") is False
    if r.get("checks_broken"):
        return "FIX_CHECKS", ("These checks cannot run here: " + ", ".join(r["checks_broken"]) +
                              ". Replace them (`rl.py set-checks`), then `rl.py run-checks` again.")
    if r["kind"] == "pr":
        # every accepted PR comment is applied, whatever its severity, and then independently reviewed
        todo = [f["id"] for f in cur if f["status"] == "accepted"]
        if todo:
            return "FIX", "Apply accepted PR comments: " + ", ".join(todo)
        if any(f["status"] == "fixed" for f in cur):
            if n >= st["max_rounds"]:
                return "CAPPED", "PR comment fixes were applied but the round budget is spent; they are unreviewed."
            return "NEXT_ROUND", "PR comment fixes need an independent review round (full + delta), then the gate."
        return "CONVERGED", "No code changed for the PR comments." + pr_tip(st)
    if blocking_unfixed:
        return "FIX", "Fix accepted blocking findings: " + ", ".join(blocking_unfixed)
    clean = not blocking_this_round and not checks_failed
    if not clean:
        why = f"{len(blocking_this_round)} blocking finding(s) accepted this round" + \
              ("; checks failing" if checks_failed else "")
        if n >= st["max_rounds"]:
            return "CAPPED", (f"Max rounds ({st['max_rounds']}) reached and round {n} was not clean: {why}. "
                               "Fixes applied in this final round have NOT been independently reviewed.")
        return "NEXT_ROUND", why + (f". Also fix non-blocking accepted: {', '.join(nonblocking_unfixed)}"
                                   if nonblocking_unfixed else "")
    # clean round
    if nonblocking_unfixed:
        tip = ("Clean round. Do NOT edit code now (it would be unreviewed); the accepted minor "
               f"findings {', '.join(nonblocking_unfixed)} go to the report as optional fixes.")
    else:
        tip = "Clean round."
    if st["gate"] and r["kind"] != "gate":
        if n >= st["max_rounds"] + 1:  # allow one gate beyond max
            return "CAPPED", tip + " Gate budget exhausted."
        return "GATE", tip + " Run the fresh-session gate review on the same tree."
    return "CONVERGED", tip + pr_tip(st)


def pr_tip(st):
    if not st.get("pr"):
        return ""
    return (" Draft the PR replies (`rl.py pr-replies`), let the user push, and post only after they confirm "
            "(`rl.py pr-replies --post`).")


def cmd_status(a):
    root = repo_root()
    st = load(root)
    action, why = decide(st)
    counts = {}
    for f in st["findings"]:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    if a.json:
        print(json.dumps({"round": st["round"], "status": st["status"], "next": action, "why": why,
                          "counts": counts, "threshold": threshold(st)}, indent=2))
        return
    print(f"round {st['round']}/{st['max_rounds']}  status={st['status']}  blocking>={threshold(st)}")
    print(f"ledger: {counts}")
    print(f"NEXT: {action} - {why}")
    if a.verbose:
        for f in st["findings"]:
            if f["round"] == st["round"] or f["status"] in OPEN_STATES or f["status"] == "question":
                print(f"  {f['id']:<7} {f['status']:<10} {f['severity']:<7} {f['file']}:{f['line']}  {f['title']}")


def cmd_show(a):
    root = repo_root()
    st = load(root)
    print(json.dumps(find(st, a.id), indent=2))


def cmd_verify_tree(a):
    """Detect reviewers that modified the tree they were supposed to only read."""
    root = repo_root()
    st = load(root)
    n = need_round(st)
    _, tree = make_snapshot(root, "integrity probe")
    expected = st["rounds"][str(n)]["tree"]
    if tree != expected:
        import fnmatch
        paths = [p for p in sh(["git", "diff", "--name-only", expected, tree], cwd=root).splitlines() if p]
        real = [p for p in paths if not any(fnmatch.fnmatch(p, g) for g in ARTIFACT_GLOBS)]
        if not real:
            print(f"tree intact (only build artifacts changed: {len(paths)} path(s))")
            return
        changed = sh(["git", "diff", "--stat", expected, tree, "--"] + real, cwd=root)
        print(f"TREE CHANGED during review of round {n}:\n{changed}")
        print(f"Restore with: git diff {tree} {expected} | git apply   (review first)")
        sys.exit(3)
    print("tree intact")


def cmd_finish(a):
    root = repo_root()
    st = load(root)
    st["status"] = a.status
    st["finished"] = now()
    save(root, st)
    print(f"status -> {a.status}")


def cmd_guard(a):
    """Stop hook: keep the session from ending mid-loop. Never traps the user:
    only blocks for the owning session, only while running, at most 3 times per round."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    root = repo_root(payload.get("cwd") or os.getcwd())
    if not root or not os.path.exists(state_path(root)):
        return 0
    try:
        st = json.load(open(state_path(root)))
    except Exception:
        return 0
    if st.get("status") != "running":
        return 0
    sid = payload.get("session_id", "")
    if st.get("session_id") and sid and st["session_id"] != sid:
        return 0
    if st.get("guard_blocks", 0) >= GUARD_MAX_BLOCKS:
        return 0
    st["guard_blocks"] = st.get("guard_blocks", 0) + 1
    save(root, st)
    action, why = decide(st)
    print(json.dumps({
        "decision": "block",
        "reason": (f"review-loop is still running (round {st['round']}). Next step: {action} - {why} "
                   "Continue the protocol in the review-loop skill. If you must stop, first run "
                   "`rl.py finish --status cancelled` (or `awaiting-user` before asking the user).")}))
    return 0


def cmd_report(a):
    root = repo_root()
    st = load(root)
    action, why = decide(st)
    F = st["findings"]
    by = lambda s: [f for f in F if f["status"] == s]
    L = []
    title = {"CONVERGED": "Converged", "CAPPED": "Stopped at round cap"}.get(action, f"In progress ({action})")
    L.append(f"# Review loop report: {title}\n")
    L.append(f"- Target: {st['target']}")
    L.append(f"- Base: `{st['base'][:12]}`  Final snapshot: `{st['rounds'].get(str(st['round']), {}).get('snapshot', '')[:12]}`")
    L.append(f"- Rounds: {st['round']} (cap {st['max_rounds']}), blocking threshold: {threshold(st)}, "
             f"gate: {'on' if st['gate'] else 'off'}")
    L.append(f"- Outcome: {why}\n")
    answered = by("answered")
    qs = by("question")
    L.append("## Possible design differences (code kept as is; your call)\n")
    if not qs:
        L.append("None.\n")
    for i, f in enumerate(qs, 1):
        src = f" - PR comment by @{f['source']['author']}" if f.get("source") else ""
        L.append(f"### D{i}. {f['title']}  ({f['id']}, {f['severity']}{src})\n")
        L.append(f"`{f['file']}:{f['line']}`. {f['claim']}\n")
        if f.get("triage_note"):
            L.append(f"Why this may be a design difference: {f['triage_note']}\n")
        for j, o in enumerate(f["options"]):
            o = re.sub(r"\s*\((?:recommended)\)\s*", " ", o, flags=re.I).strip()
            mark = " **(recommended)**" if f.get("recommendation") and f["recommendation"].strip().upper()[:1] == chr(65 + j) else ""
            L.append(f"- **{chr(65+j)}.** {o}{mark}")
        L.append("")
    if answered:
        L.append("## Decisions you made\n")
        L.append("\n".join(f"- {f['id']} {f['title']}: **{f['answer']['choice']}** {f['answer']['text']}"
                           + (f" ({f['answer']['note']})" if f['answer'].get('note') else "") for f in answered))
        L.append("")
    L.append("## Valid: fixed\n")
    L.append("\n".join(f"- {f['id']} [{f['severity']}] `{f['file']}:{f['line']}` {f['title']}"
                       + (f" - {f['fix_note']}" if f.get('fix_note') else "")
                       + (f"\n  - Missed because: {f['miss']['cause']} - {f['miss']['why']}" if f.get('miss') else "")
                       + (f"\n  - Lesson: {f['lesson']}" if f.get('lesson') else "") for f in by("fixed")) or "None.")
    L.append("\n## Not valid: code kept as is (with reason)\n")
    L.append("\n".join(f"- {f['id']} [{f['severity']}] `{f['file']}:{f['line']}` {f['title']}: {f['triage_note']}"
                       + (f" (verifier: {f['verifier']})" if f['verifier'] else "")
                       + (f" - PR comment by @{f['source']['author']}" if f.get('source') else "")
                       for f in by("rejected")) or "None.")
    minors = [f for f in by("accepted") if not is_blocking(st, f)]
    L.append("\n## Optional fixes (valid, below threshold, not applied)\n")
    L.append("\n".join(f"- {f['id']} `{f['file']}:{f['line']}` {f['title']}" for f in minors) or "None.")
    L.append("\n## Deferred (pre-existing or out of scope)\n")
    L.append("\n".join(f"- {f['id']} `{f['file']}:{f['line']}` {f['title']}: {f['triage_note']}" for f in by("deferred")) or "None.")
    L.append("\n## Rounds\n")
    L.append("| Round | Kind | Reviewers | Checks | Found | Fixed | Not valid | Doubtful |")
    L.append("|---|---|---|---|---|---|---|---|")
    for k in sorted(st["rounds"], key=int):
        r = st["rounds"][k]
        rf = findings_in(st, int(k))
        ck = {True: "pass", False: "FAIL", None: "n/a"}[r.get("checks_ok")]
        L.append(f"| {k} | {r['kind']} | {', '.join(r['reviewers'])} | {ck} | {len(rf)} | "
                 f"{sum(f['status'] in ('fixed','accepted') for f in rf)} | "
                 f"{sum(f['status']=='rejected' for f in rf)} | {sum(f['status'] in ('question','answered') for f in rf)} |")
    if st.get("notes"):
        L.append("\n## Process notes\n")
        L.append("\n".join(f"- {n}" for n in st["notes"]))
    L.append(f"\n## How to inspect\n\n```\ngit diff {st['base'][:12]} refs/review-loop/round-{st['round']}   # whole change\n"
             f"python3 {os.path.abspath(__file__)} show <ID>                 # any finding in full\n```\n")
    out = os.path.join(state_dir(root), f"report-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.md")
    with open(out, "w") as f:
        f.write("\n".join(L))
    print(out)


def guide_file(root, suffix=".md"):
    """The author guide: a semantic, topic-organised instructions doc for the author session."""
    os.makedirs(os.path.join(RL_HOME, "guides"), exist_ok=True)
    return os.path.join(RL_HOME, "guides", repo_slug(root).replace("/", "__") + suffix)


def exit_instructions_path(root, st):
    p = st.get("exit_instructions") or load_config(root).get("exit_instructions") or os.path.join(RL_HOME, "EXIT.md")
    return os.path.expanduser(p)


def cmd_export_learnings(a):
    """Deterministic summary of what this loop taught the author session, for the exit skill.
    It holds no code and no conversation text, and it is never shown to reviewers."""
    root = repo_root()
    st = load(root)
    slug = repo_slug(root)
    final = st["rounds"].get(str(st["round"]), {}).get("snapshot", "")
    F = st["findings"]
    cats = {}
    for f in F:
        if f["status"] == "fixed":
            cats[f["category"]] = cats.get(f["category"], 0) + 1
    src = lambda f: ({"pr_comment_by": f["source"]["author"]} if f.get("source") else {})
    out = {
        "marker": f"review-loop:{slug}:{st['base'][:12]}:{final[:12]}",
        "repo": slug, "target": st["target"], "pr": (st.get("pr") or {}).get("number"),
        "base": st["base"], "final_snapshot": final, "rounds": st["round"], "outcome": st["status"],
        "defect_classes_fixed": cats,
        "fixed": [dict({"severity": f["severity"], "category": f["category"], "file": f["file"],
                        "title": f["title"], "claim": f["claim"], "fix": f.get("fix_note", ""),
                        "miss": f.get("miss"), "lesson": f.get("lesson", "")}, **src(f))
                  for f in F if f["status"] == "fixed"],
        "not_valid": [dict({"title": f["title"], "file": f["file"], "why_not_valid": f["triage_note"],
                            "verifier": f.get("verifier", ""), "lesson": f.get("lesson", "")}, **src(f))
                      for f in F if f["status"] == "rejected"],
        "design_differences": [dict({"title": f["title"], "file": f["file"], "note": f["triage_note"],
                                     "decision": (f.get("answer") or {}).get("text", "open"),
                                     "decision_note": (f.get("answer") or {}).get("note", ""),
                                     "miss": f.get("miss"), "lesson": f.get("lesson", "")}, **src(f))
                               for f in F if f["status"] in ("question", "answered")],
        "miss_causes": MISS_CAUSES,
        "missing_miss_analysis": [f["id"] for f in F if f["status"] == "fixed" and not f.get("miss")],
        "guide_file": guide_file(root),
        "guide_store": guide_store(),
        "guide_history": guide_file(root, ".history.md"),
        "global_guide": os.path.join(RL_HOME, "guides", "_global.md"),
        "exit_instructions": exit_instructions_path(root, st),
    }
    p = os.path.join(state_dir(root), "learnings.json")
    with open(p, "w") as fh:
        json.dump(out, fh, indent=2)
    st["learnings_exported"] = True
    save(root, st)
    print(p)


# ------------------------------------------------------------ PR comments
PR_QUERY = """query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){
 number url author{login}
 reviewThreads(first:100){nodes{id isResolved isOutdated path line originalLine
   comments(first:30){nodes{databaseId author{login} body url}}}}
 reviews(first:100){nodes{databaseId author{login} body state url}}
 comments(first:100){nodes{databaseId author{login} body url}}}}}"""
SEVERITY_WORDS = {"critical": "blocker", "blocker": "blocker", "warning": "major", "major": "major",
                  "high": "major", "suggestion": "minor", "minor": "minor", "medium": "minor",
                  "nit": "nit", "low": "nit", "question": "question"}
LEAD_TAG = re.compile(r"^\s*(?:\[AI\]\s*)?(?:\[(\w+)\]|\*\*(\w+)\*\*\s*[-\u2014:])", re.I)


def comment_severity(body, inline):
    """Severity from the comment's own tag: `[AI][Warning] ...`, `[CRITICAL]`, `**minor** - ...`,
    or "High Severity". Untagged: a question stays a question; an inline comment is a change request."""
    m = LEAD_TAG.match(body)
    word = (m.group(1) or m.group(2)).lower() if m else ""
    if word in SEVERITY_WORDS:
        return SEVERITY_WORDS[word]
    m = re.search(r"\b(critical|high|medium|low) severity\b", body, re.I)
    if m:
        return SEVERITY_WORDS[m.group(1).lower()]
    if re.search(r"\?\s*$", body.strip().splitlines()[0] if body.strip() else ""):
        return "question"
    return "major" if inline else "question"


def first_line(body):
    for ln in body.splitlines():
        ln = re.sub(r"\[\w+\]\s*", "", ln)                                   # [AI][Warning]
        ln = re.sub(r"^\s*\*\*\w+\*\*\s*[-\u2014:]\s*", "", ln)              # **minor** -
        ln = re.sub(r"^\s*`[^`]*`\s*[-\u2014]\s*", "", ln)                     # `file:line` -
        ln = re.sub(r"^[#>\s-]+", "", ln).replace("**", "").strip()
        if ln:
            return ln[:80]
    return "(empty comment)"


def cmd_reopen(a):
    """After the user pushed: start answering PR comments in the same orchestrator thread."""
    root = repo_root()
    st = load(root)
    if st["status"] == "running":
        sys.exit("the loop is still running; finish it first")
    st["status"] = "running"
    st["pr"] = dict(st.get("pr") or {}, number=a.pr, seen=(st.get("pr") or {}).get("seen", []),
                    posted=(st.get("pr") or {}).get("posted", []))
    st["max_rounds"] = st["round"] + 1 + a.extra_rounds
    st["notes"].append(f"reopened for PR #{a.pr} comments at round {st['round']}")
    save(root, st)
    print(f"reopened for PR #{a.pr}; next: `rl.py snapshot --kind pr`, then `rl.py pr-ingest`")


def cmd_pr_ingest(a):
    root = repo_root()
    st = load(root)
    n = need_round(st)
    r = st["rounds"][str(n)]
    if r["kind"] != "pr" or not st.get("pr"):
        sys.exit("run `rl.py reopen --pr N` and `rl.py snapshot --kind pr` first")
    if a.from_file:
        data = json.load(open(a.from_file))
    else:
        owner, _, name = repo_slug(root).partition("/")
        out = sh(["gh", "api", "graphql", "-f", f"query={PR_QUERY}", "-F", f"o={owner}", "-F", f"r={name}",
                  "-F", f"n={st['pr']['number']}"], cwd=root)
        data = json.loads(out)
    pr = data["data"]["repository"]["pullRequest"]
    me = (pr.get("author") or {}).get("login", "")
    seen = set(st["pr"]["seen"])
    cmap = change_map(root, st["base"], r["snapshot"])
    seq = len(findings_in(st, n))
    added = []

    def add(kind, cid, author, body, url, path="", line=0, thread=None, thread_id=""):
        nonlocal seq
        seq += 1
        f = {"severity": comment_severity(body, kind == "inline"), "confidence": "high",
             "category": "review-comment", "file": path or "", "line": int(line or 0),
             "title": first_line(body), "claim": body.strip(), "evidence": url,
             "failure_scenario": "(PR review comment)", "reproduced": False}
        f.update({"id": f"R{n}-{seq}", "round": n, "reviewer": "github", "status": "untriaged",
                  "fingerprint": fingerprint(f), "triage_note": "", "duplicate_of": "", "verifier": "",
                  "recurrences": 0, "options": [], "recommendation": "", "possible_duplicates": [],
                  "source": {"kind": kind, "comment_id": cid, "author": author, "url": url,
                             "thread": thread or [], "thread_id": thread_id}})
        f["location"] = locate(f, cmap) if path else "outside-change"
        st["findings"].append(f)
        seen.add(cid)
        added.append(f)

    for t in pr["reviewThreads"]["nodes"]:
        cs = t["comments"]["nodes"]
        if t["isResolved"] or not cs:
            continue
        head = cs[0]
        who = (head.get("author") or {}).get("login", "")
        if head["databaseId"] in seen or who == me:
            continue
        rest = [f"@{(c.get('author') or {}).get('login','')}: {c['body']}" for c in cs[1:]]
        add("inline", head["databaseId"], who, head["body"], head["url"], t["path"],
            t.get("line") or t.get("originalLine") or 0, rest, t["id"])
    for rv in pr["reviews"]["nodes"]:
        who = (rv.get("author") or {}).get("login", "")
        if rv["body"].strip() and rv["databaseId"] not in seen and who != me:
            add("review", rv["databaseId"], who, rv["body"], rv["url"])
    for c in pr["comments"]["nodes"]:
        who = (c.get("author") or {}).get("login", "")
        if c["body"].strip() and c["databaseId"] not in seen and who != me:
            add("comment", c["databaseId"], who, c["body"], c["url"])
    st["pr"]["seen"] = sorted(seen)
    if "github" not in r["reviewers"]:
        r["reviewers"].append("github")
    r.setdefault("verdicts", {})["github"] = {"verdict": "issues_found" if added else "clean",
                                             "summary": f"{len(added)} open PR comment(s)", "checks_run": []}
    save(root, st)
    print(f"PR #{st['pr']['number']}: {len(added)} open comment(s) ingested (resolved threads and your own skipped)")
    for f in added:
        print(f"  {f['id']:<7} {f['severity']:<8} @{f['source']['author']:<16} {f['file'] or '(PR-level)'}:{f['line']}  {f['title']}")


def reply_text(st, f, prefix):
    s = f["status"]
    if s == "fixed":
        body = f"Fixed: {f.get('fix_note') or 'applied as suggested'}."
    elif s == "rejected":
        body = f"Keeping this as is: {f['triage_note']}"
    elif s == "question":
        opts = " / ".join(f"({chr(65+i)}) {o}" for i, o in enumerate(f["options"]))
        body = (f"Possible design difference: {f['triage_note']} Options: {opts}. "
                "Keeping the current design until we agree.")
    elif s == "answered":
        body = f"Decided: {f['answer']['text']}." + (f" {f['answer']['note']}" if f["answer"].get("note") else "")
    elif s == "deferred":
        body = f"Out of scope for this PR: {f['triage_note']}"
    else:
        return ""
    return prefix + body


def cmd_pr_replies(a):
    root = repo_root()
    st = load(root)
    if not st.get("pr"):
        sys.exit("no PR attached; run `rl.py reopen --pr N` first")
    prefix = load_config(root).get("reply_prefix", "[AI] ")
    posted = set(st["pr"].get("posted", []))
    todo = []
    for f in st["findings"]:
        if f.get("source") and f["source"]["comment_id"] not in posted:
            txt = reply_text(st, f, prefix)
            if txt:
                todo.append((f, txt))
    md = [f"# Draft replies for PR #{st['pr']['number']}\n"]
    for f, txt in todo:
        md.append(f"## {f['id']} @{f['source']['author']} ({f['status']})\n\n{f['source']['url']}\n\n> {f['title']}\n\n{txt}\n")
    out = os.path.join(state_dir(root), "pr-replies.md")
    with open(out, "w") as fh:
        fh.write("\n".join(md))
    if not a.post:
        print(f"{len(todo)} draft repl(ies): {out}\nNothing was posted. Post only after the user pushed and confirmed.")
        return
    dirty = [l for l in sh(["git", "status", "--porcelain"], cwd=root).splitlines() if ".review-loop" not in l]
    ahead = subprocess.run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=root, capture_output=True, text=True)
    if dirty or ahead.returncode != 0 or ahead.stdout.strip() != "0":
        sys.exit("refusing to post: the fixes are not pushed yet (uncommitted changes, or HEAD ahead of / "
                 "without an upstream). Ask the user to commit and push first.")
    slug = repo_slug(root)
    for f, txt in todo:
        src = f["source"]
        if src["kind"] == "inline":
            sh(["gh", "api", "-X", "POST", f"repos/{slug}/pulls/{st['pr']['number']}/comments/{src['comment_id']}/replies",
                "-f", f"body={txt}"], cwd=root)
        else:
            sh(["gh", "pr", "comment", str(st["pr"]["number"]), "--body", f"> {f['title']}\n\n{txt}"], cwd=root)
        posted.add(src["comment_id"])
        st["pr"]["posted"] = sorted(posted)
        save(root, st)
        print(f"posted reply to {f['id']} (@{src['author']})")


# ------------------------------------------------------- author guide store
# The guide always has a local copy in RL_HOME/guides/. Optionally an external store (a versioned
# notes service, a synced folder, a private git repo, a memory server) holds the canonical copy,
# reached through two user-supplied shell commands in RL_HOME/settings.json:
#   {"guide_store": "auto|command|file", "guide_pull_cmd": "...", "guide_push_cmd": "..."}
# Both run with RL_REPO_SLUG set. Pull prints the newest guide on stdout (empty = none yet; non-zero
# exit = store unavailable). Push reads RL_GUIDE_FILE, RL_HISTORY_FILE (may be empty) and RL_MARKER,
# and may print a version id. "auto" (the default) uses the commands when set and falls back to the
# local copy; "command" makes a failed save an error; "file" ignores the commands.
STORE_TIMEOUT = 20


class StoreError(Exception):
    pass


def rl_settings():
    p = os.path.join(RL_HOME, "settings.json")
    try:
        return json.load(open(p)) if os.path.exists(p) else {}
    except Exception:
        return {}


def guide_store():
    s = os.environ.get("REVIEW_LOOP_GUIDE_STORE") or rl_settings().get("guide_store", "auto")
    if s == "auto" and not (rl_settings().get("guide_pull_cmd") and rl_settings().get("guide_push_cmd")):
        return "file"
    return s


def store_run(key, slug, extra=None):
    cmd = rl_settings().get(key)
    if not cmd:
        raise StoreError(f"{key} is not set in {os.path.join(RL_HOME, 'settings.json')}")
    env = dict(os.environ, RL_REPO_SLUG=slug, **(extra or {}))
    try:
        r = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True, timeout=STORE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise StoreError(f"{key} timed out after {STORE_TIMEOUT}s")
    if r.returncode != 0:
        raise StoreError(f"{key} exited {r.returncode}: {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def load_guide(root):
    """(text, source, note): the external store first (refreshing the local copy), else the local file."""
    slug = repo_slug(root)
    local = guide_file(root)
    note = ""
    if guide_store() != "file":
        try:
            text = store_run("guide_pull_cmd", slug)
            if text.strip():
                if not os.path.exists(local) or open(local).read() != text:
                    with open(local, "w") as fh:
                        fh.write(text)
                return text, "store", ""
            note = "the guide store has no guide for this repository yet"
        except StoreError as e:
            note = f"guide store unavailable ({e}); using the local copy"
    if os.path.exists(local):
        return open(local).read(), "file", note
    return None, "none", note


def cmd_guide_pull(a):
    root = repo_root()
    if not root:
        sys.exit("not inside a git repository")
    text, source, note = load_guide(root)
    print(json.dumps({"source": source, "path": guide_file(root), "store": guide_store(), "note": note,
                      "chars": len(text or "")}, indent=2))


def cmd_guide_push(a):
    """Save an approved guide revision: the external store (if configured), then the local copy."""
    root = repo_root()
    if not root:
        sys.exit("not inside a git repository")
    slug = repo_slug(root)
    text = open(a.file).read().strip() + "\n"
    hist_path = guide_file(root, ".history.md")
    history = open(a.history_file).read().strip() if a.history_file else ""
    seen = a.marker and os.path.exists(hist_path) and a.marker in open(hist_path).read()
    stamped = f"<!-- author guide for {slug}; saved {now()}{'; ' + a.marker if a.marker else ''} -->\n" + \
        re.sub(r"^<!-- author guide for .*? -->\n", "", text)
    store, where = guide_store(), "file"
    if store != "file":
        with tempfile.TemporaryDirectory() as tmp:
            gp, hp = os.path.join(tmp, "guide.md"), os.path.join(tmp, "history.md") if history and not seen else ""
            open(gp, "w").write(stamped)
            if hp:
                open(hp, "w").write(history + "\n")
            try:
                out = store_run("guide_push_cmd", slug, {"RL_GUIDE_FILE": gp, "RL_HISTORY_FILE": hp,
                                                         "RL_MARKER": a.marker or ""})
                version = (out.strip().splitlines() or [""])[-1][:80]
                where = f"store:{version}" if version else "store"
            except StoreError as e:
                if store == "command":
                    sys.exit(f"guide not saved: {e}. Fix the store or set guide_store to file.")
                print(f"warning: {e}; saved locally only", file=sys.stderr)
    with open(guide_file(root), "w") as fh:
        fh.write(stamped)
    if history and not seen:
        lead = "\n" if os.path.exists(hist_path) and os.path.getsize(hist_path) else ""
        with open(hist_path, "a") as fh:
            fh.write(lead + f"## {now()} {a.marker or ''}\n\n{history}\n")
    print(json.dumps({"saved": where, "local": guide_file(root), "history": "skipped (marker already recorded)"
                      if seen else (hist_path if history else "none")}, indent=2))


def cmd_learnings_context(a):
    """SessionStart hook: give the *author* session the lessons of earlier loops in this repo.
    Subagents do not run SessionStart, and the gate process runs with hooks disabled, so reviewers
    never receive this text. Never fails the session."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    try:
        root = repo_root(payload.get("cwd") or os.getcwd())
        if not root:
            return 0
        parts = []
        g = os.path.join(RL_HOME, "guides", "_global.md")
        if os.path.exists(g) and open(g).read().strip():
            parts.append(f"<!-- {g} -->\n" + open(g).read().strip())
        text, source, note = load_guide(root)
        if text and text.strip():
            parts.append(f"<!-- author guide for {repo_slug(root)} (from {source}) -->\n" + text.strip())
        if note and parts:
            parts.append(f"(note: {note})")
        if not parts:
            return 0
        text = "\n\n".join(parts)
        if len(text) > LEARNINGS_CONTEXT_CAP:
            text = text[:LEARNINGS_CONTEXT_CAP] + "\n\n[truncated; read the files named above for the rest]"
        ctx = ("Author guide for this repository, distilled from earlier review loops (why past changes had "
               "bugs, and the design choices that were confirmed). Follow it while writing code. Never paste "
               "it into a review-loop reviewer prompt.\n\n" + text)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
    except Exception:
        return 0
    return 0


def main():
    p = argparse.ArgumentParser(prog="rl.py")
    s = p.add_subparsers(dest="cmd", required=True)
    x = s.add_parser("init"); x.add_argument("--target"); x.add_argument("--base"); x.add_argument("--intent")
    x.add_argument("--intent-file"); x.add_argument("--session"); x.add_argument("--max-rounds", type=int, default=0)
    x.add_argument("--reviewer-model")
    x.add_argument("--strict", action="store_true"); x.add_argument("--no-gate", action="store_true")
    x.add_argument("--gate-model"); x.add_argument("--checks", help="commands separated by ';;'")
    x.add_argument("--review-skill", help="team review skill reviewers must apply, e.g. branch-review")
    x.add_argument("--force", action="store_true"); x.set_defaults(fn=cmd_init)
    s.add_parser("detect-checks").set_defaults(fn=cmd_detect_checks)
    x = s.add_parser("config"); x.add_argument("--probe", action="store_true"); x.add_argument("--timeout", type=int, default=300)
    x.set_defaults(fn=cmd_config)
    x = s.add_parser("set-intent"); x.add_argument("--file", required=True); x.set_defaults(fn=cmd_set_intent)
    x = s.add_parser("set-checks"); x.add_argument("checks"); x.set_defaults(fn=cmd_set_checks)
    x = s.add_parser("scope"); x.add_argument("--add", nargs="+"); x.add_argument("--note"); x.set_defaults(fn=cmd_scope)
    x = s.add_parser("snapshot"); x.add_argument("--kind", choices=["review", "gate", "pr"], default="review"); x.set_defaults(fn=cmd_snapshot)
    x = s.add_parser("run-checks"); x.add_argument("--timeout", type=int, default=900)
    x.add_argument("--probe", action="store_true", help="validate checks before round 1"); x.set_defaults(fn=cmd_run_checks)
    x = s.add_parser("brief"); x.add_argument("--mode", choices=["full", "delta", "gate"], required=True)
    x.add_argument("--name", required=True); x.add_argument("--lens"); x.set_defaults(fn=cmd_brief)
    x = s.add_parser("ingest"); x.add_argument("file"); x.add_argument("--reviewer"); x.set_defaults(fn=cmd_ingest)
    x = s.add_parser("triage"); x.add_argument("id"); x.add_argument("verdict", choices=TRIAGE_VERDICTS)
    x.add_argument("--note"); x.add_argument("--duplicate-of"); x.add_argument("--verifier", choices=["real", "not-real", "uncertain"])
    x.add_argument("--option", action="append"); x.add_argument("--recommend"); x.set_defaults(fn=cmd_triage)
    x = s.add_parser("fixed"); x.add_argument("ids", nargs="+"); x.add_argument("--note")
    x.add_argument("--lesson", help="the reusable pattern this fix teaches")
    x.add_argument("--cause", choices=sorted(MISS_CAUSES)); x.add_argument("--why"); x.add_argument("--guide-ref")
    x.set_defaults(fn=cmd_fixed)
    x = s.add_parser("miss"); x.add_argument("id"); x.add_argument("--cause", choices=sorted(MISS_CAUSES), required=True)
    x.add_argument("--why", required=True); x.add_argument("--guide-ref"); x.set_defaults(fn=cmd_miss)
    x = s.add_parser("lesson"); x.add_argument("id"); x.add_argument("text"); x.set_defaults(fn=cmd_lesson)
    x = s.add_parser("reopen"); x.add_argument("--pr", type=int, required=True)
    x.add_argument("--extra-rounds", type=int, default=3); x.set_defaults(fn=cmd_reopen)
    x = s.add_parser("pr-ingest"); x.add_argument("--from-file"); x.set_defaults(fn=cmd_pr_ingest)
    x = s.add_parser("pr-replies"); x.add_argument("--post", action="store_true"); x.set_defaults(fn=cmd_pr_replies)
    s.add_parser("learnings-context").set_defaults(fn=cmd_learnings_context)
    s.add_parser("guide-pull").set_defaults(fn=cmd_guide_pull)
    x = s.add_parser("guide-push"); x.add_argument("--file", required=True); x.add_argument("--history-file")
    x.add_argument("--marker"); x.set_defaults(fn=cmd_guide_push)
    x = s.add_parser("status"); x.add_argument("--json", action="store_true"); x.add_argument("-v", "--verbose", action="store_true"); x.set_defaults(fn=cmd_status)
    x = s.add_parser("show"); x.add_argument("id"); x.set_defaults(fn=cmd_show)
    s.add_parser("verify-tree").set_defaults(fn=cmd_verify_tree)
    x = s.add_parser("finish"); x.add_argument("--status", required=True,
        choices=["converged", "capped", "cancelled", "awaiting-user", "done"]); x.set_defaults(fn=cmd_finish)
    s.add_parser("guard").set_defaults(fn=cmd_guard)
    s.add_parser("report").set_defaults(fn=cmd_report)
    x = s.add_parser("answer"); x.add_argument("id"); x.add_argument("choice", help="option letter, or free text")
    x.add_argument("--note"); x.set_defaults(fn=cmd_answer)
    s.add_parser("export-learnings").set_defaults(fn=cmd_export_learnings)
    x = s.add_parser("drop-reviewer"); x.add_argument("name"); x.add_argument("--reason", required=True)
    x.set_defaults(fn=cmd_drop_reviewer)
    a = p.parse_args()
    rc = a.fn(a)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
