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

SEVERITIES = ["nit", "minor", "major", "blocker"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
TRIAGE_VERDICTS = ["accept", "reject", "defer", "ask", "duplicate"]
CONFIG_FILE = ".review-loop.json"
DEFAULT_SIZE_LIMITS = {"commits": 20, "files": 100, "lines": 5000}
GUIDE_NAMES = ("REVIEW.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md")
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
def load_config(root):
    p = os.path.join(root, CONFIG_FILE)
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p))
    except Exception as e:
        sys.exit(f"{CONFIG_FILE} is not valid JSON: {e}")


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
        "round": 0,
        "rounds": {},
        "findings": [],
        "guard_blocks": 0,
        "notes": [],
    }
    save(root, st)
    print(json.dumps({"repo": root, "base": base, "checks": checks, "size": size, "over_limits": over,
                      "reviewer_model": st["reviewer_model"], "gate_model": st["gate_model"],
                      "config": CONFIG_FILE if cfg else None, "state": state_path(root)}, indent=2))
    if over:
        print(f"SIZE GATE: change exceeds {', '.join(f'{k}>{limits[k]}' for k in over)}. "
              "Ask the user before starting the loop.")
    if not checks:
        print("hint: no checks configured; run `rl.py detect-checks` and `rl.py set-checks`.")


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
    for i, c in enumerate(st["checks"]):
        log = os.path.join(d, f"{i:02d}.log")
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
    with open(os.path.join(d, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    if not a.probe:
        st["rounds"][str(n)]["checks_ok"] = ok if st["checks"] else None
        st["rounds"][str(n)]["checks_broken"] = broken
        save(root, st)
    for r in results:
        tag = "ENV-ERROR" if r["env_error"] else ("PASS" if r["exit_code"] == 0 else "FAIL")
        print(f"[{tag}] exit={r['exit_code']} warn~{r['warning_mentions']}  {r['command']}")
    if broken:
        print("Some checks cannot run in this environment (missing tool/module). Replace them with "
              "`rl.py set-checks` and re-run; they would otherwise block convergence forever.")
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
    found = []
    for d in sorted(dirs, key=lambda x: (x.count("/"), x)):
        for g in GUIDE_NAMES:
            rel = os.path.join(d, g) if d else g
            if os.path.isfile(os.path.join(root, rel)):
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
    pit = os.path.join(state_dir(root), "pitfalls.md")
    if os.path.exists(pit):
        rep["LENS"] += ("\n\n### Known pitfalls in this codebase (team history; verify, do not assume)\n\n"
                        + open(pit).read().strip())
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
    seq = len(findings_in(st, n))
    added = []
    for f in doc["findings"]:
        seq += 1
        f = dict(f)
        f.update({"id": f"R{n}-{seq}", "round": n, "reviewer": name, "status": "untriaged",
                  "fingerprint": fingerprint(f), "triage_note": "", "duplicate_of": "",
                  "verifier": "", "recurrences": 0, "options": [], "recommendation": ""})
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
    if v == "ask" and not a.option:
        sys.exit("an `ask` needs at least two --option values (and ideally --recommend)")
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
    elif v == "ask":
        f["status"] = "question"
        f["options"] = a.option
        f["recommendation"] = a.recommend or ""
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
    save(root, st)
    print(f"marked fixed: {' '.join(a.ids)}")


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
    return "CONVERGED", tip


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
    L.append("## Decisions needed from you\n")
    if not qs:
        L.append("None.\n")
    for i, f in enumerate(qs, 1):
        L.append(f"### Q{i}. {f['title']}  ({f['id']}, {f['severity']})\n")
        L.append(f"`{f['file']}:{f['line']}`. {f['claim']}\n")
        if f.get("triage_note"):
            L.append(f"Why this is a judgement call: {f['triage_note']}\n")
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
    minors = [f for f in by("accepted") if not is_blocking(st, f)]
    L.append("## Optional fixes (accepted, below threshold, not applied)\n")
    L.append("\n".join(f"- {f['id']} `{f['file']}:{f['line']}` {f['title']}" for f in minors) or "None.")
    L.append("\n## Rounds\n")
    L.append("| Round | Kind | Reviewers | Checks | Found | Accepted+fixed | Rejected | Questions |")
    L.append("|---|---|---|---|---|---|---|---|")
    for k in sorted(st["rounds"], key=int):
        r = st["rounds"][k]
        rf = findings_in(st, int(k))
        ck = {True: "pass", False: "FAIL", None: "n/a"}[r.get("checks_ok")]
        L.append(f"| {k} | {r['kind']} | {', '.join(r['reviewers'])} | {ck} | {len(rf)} | "
                 f"{sum(f['status'] in ('fixed','accepted') for f in rf)} | "
                 f"{sum(f['status']=='rejected' for f in rf)} | {sum(f['status']=='question' for f in rf)} |")
    L.append("\n## Fixed\n")
    L.append("\n".join(f"- {f['id']} [{f['severity']}] `{f['file']}:{f['line']}` {f['title']}"
                       + (f" - {f['fix_note']}" if f.get('fix_note') else "") for f in by("fixed")) or "None.")
    L.append("\n## Rejected (with reason)\n")
    L.append("\n".join(f"- {f['id']} [{f['severity']}] {f['title']}: {f['triage_note']}"
                       + (f" (verifier: {f['verifier']})" if f['verifier'] else "") for f in by("rejected")) or "None.")
    L.append("\n## Deferred (pre-existing or out of scope)\n")
    L.append("\n".join(f"- {f['id']} `{f['file']}:{f['line']}` {f['title']}: {f['triage_note']}" for f in by("deferred")) or "None.")
    if st.get("notes"):
        L.append("\n## Process notes\n")
        L.append("\n".join(f"- {n}" for n in st["notes"]))
    L.append(f"\n## How to inspect\n\n```\ngit diff {st['base'][:12]} refs/review-loop/round-{st['round']}   # whole change\n"
             f"python3 {os.path.abspath(__file__)} show <ID>                 # any finding in full\n```\n")
    out = os.path.join(state_dir(root), f"report-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.md")
    with open(out, "w") as f:
        f.write("\n".join(L))
    print(out)


def cmd_export_learnings(a):
    """Deterministic, compact summary of what this loop taught us, for publishing to a
    team memory (e.g. the Meko extension). Contains no code and no conversation text."""
    root = repo_root()
    st = load(root)
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root, capture_output=True, text=True).stdout.strip()
    slug = re.sub(r"^.*[:/]([^/]+/[^/]+?)(\.git)?$", r"\1", remote) if remote else os.path.basename(root)
    final = st["rounds"].get(str(st["round"]), {}).get("snapshot", "")
    F = st["findings"]
    cats = {}
    for f in F:
        if f["status"] == "fixed":
            cats[f["category"]] = cats.get(f["category"], 0) + 1
    out = {
        "marker": f"review-loop:{slug}:{st['base'][:12]}:{final[:12]}",
        "repo": slug, "target": st["target"], "base": st["base"], "final_snapshot": final,
        "rounds": st["round"], "outcome": st["status"],
        "defect_classes_fixed": cats,
        "fixed": [{"severity": f["severity"], "category": f["category"], "file": f["file"], "title": f["title"]}
                  for f in F if f["status"] == "fixed"],
        "verified_false_positives": [{"title": f["title"], "file": f["file"], "why_not_real": f["triage_note"]}
                                     for f in F if f["status"] == "rejected" and f.get("verifier") == "not-real"],
        "user_decisions": [{"title": f["title"], "decision": f["answer"]["text"], "note": f["answer"].get("note", "")}
                           for f in F if f["status"] == "answered"],
        "open_questions": [f["title"] for f in F if f["status"] == "question"],
    }
    p = os.path.join(state_dir(root), "learnings.json")
    with open(p, "w") as fh:
        json.dump(out, fh, indent=2)
    print(p)


def main():
    p = argparse.ArgumentParser(prog="rl.py")
    s = p.add_subparsers(dest="cmd", required=True)
    x = s.add_parser("init"); x.add_argument("--target"); x.add_argument("--base"); x.add_argument("--intent")
    x.add_argument("--intent-file"); x.add_argument("--session"); x.add_argument("--max-rounds", type=int, default=0)
    x.add_argument("--reviewer-model")
    x.add_argument("--strict", action="store_true"); x.add_argument("--no-gate", action="store_true")
    x.add_argument("--gate-model"); x.add_argument("--checks", help="commands separated by ';;'")
    x.add_argument("--force", action="store_true"); x.set_defaults(fn=cmd_init)
    s.add_parser("detect-checks").set_defaults(fn=cmd_detect_checks)
    x = s.add_parser("set-intent"); x.add_argument("--file", required=True); x.set_defaults(fn=cmd_set_intent)
    x = s.add_parser("set-checks"); x.add_argument("checks"); x.set_defaults(fn=cmd_set_checks)
    x = s.add_parser("snapshot"); x.add_argument("--kind", choices=["review", "gate"], default="review"); x.set_defaults(fn=cmd_snapshot)
    x = s.add_parser("run-checks"); x.add_argument("--timeout", type=int, default=900)
    x.add_argument("--probe", action="store_true", help="validate checks before round 1"); x.set_defaults(fn=cmd_run_checks)
    x = s.add_parser("brief"); x.add_argument("--mode", choices=["full", "delta", "gate"], required=True)
    x.add_argument("--name", required=True); x.add_argument("--lens"); x.set_defaults(fn=cmd_brief)
    x = s.add_parser("ingest"); x.add_argument("file"); x.add_argument("--reviewer"); x.set_defaults(fn=cmd_ingest)
    x = s.add_parser("triage"); x.add_argument("id"); x.add_argument("verdict", choices=TRIAGE_VERDICTS)
    x.add_argument("--note"); x.add_argument("--duplicate-of"); x.add_argument("--verifier", choices=["real", "not-real", "uncertain"])
    x.add_argument("--option", action="append"); x.add_argument("--recommend"); x.set_defaults(fn=cmd_triage)
    x = s.add_parser("fixed"); x.add_argument("ids", nargs="+"); x.add_argument("--note"); x.set_defaults(fn=cmd_fixed)
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
