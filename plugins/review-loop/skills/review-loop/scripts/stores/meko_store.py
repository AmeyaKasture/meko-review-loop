#!/usr/bin/env python3
"""Author-guide store backed by a local (offline) Meko: https://mekodata.ai

Each repository gets a datapack `review-loop guide <owner>/<repo>`. Every approved guide revision is
saved as a new version of the artifact `author-guide.md`, and each loop's miss history as a memory.

Plugs into rl.py's command store (~/.review-loop/settings.json):
  {"guide_store": "auto",
   "guide_pull_cmd": "python3 <this file> pull",
   "guide_push_cmd": "python3 <this file> push"}

usage: meko_store.py check | pull | push
  check  exit 0 if Meko is installed, running, and accepts the token; print the reason otherwise
  pull   print the newest guide for $RL_REPO_SLUG (nothing if there is none yet)
  push   save $RL_GUIDE_FILE (and $RL_HISTORY_FILE, if set) for $RL_REPO_SLUG; print the version
Exits non-zero when Meko is unreachable, so rl.py falls back to its local copy.
Env: MEKO_URL, MEKO_TOKEN (default: `meko token`).
Without MEKO_URL the agent endpoint is discovered: the "Agents (MCP, API)" line of `meko status`,
then ports 7778 and 7777 on 127.0.0.1, whichever answers Meko's API with the token. Installs differ
(the web UI and the agent API can share one port or use two), so never hard-code it.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

MEKO_URL = os.environ.get("MEKO_URL", "").rstrip("/")
CANDIDATE_PORTS = (7778, 7777)
GUIDE_ARTIFACT = "author-guide.md"
AGENT = "review-loop"


class MekoError(Exception):
    pass


def meko_exe():
    exe = shutil.which("meko") or os.path.expanduser("~/.local/bin/meko")
    return exe if os.path.exists(exe) else None


def token():
    if os.environ.get("MEKO_TOKEN"):
        return os.environ["MEKO_TOKEN"]
    exe = meko_exe()
    if not exe:
        raise MekoError("meko is not installed")
    r = subprocess.run([exe, "token"], capture_output=True, text=True, timeout=5)
    if r.returncode or not r.stdout.strip():
        raise MekoError("could not read the Meko token (`meko token`); is Meko running?")
    return r.stdout.strip()


def status_url():
    """The agent endpoint `meko status` reports, e.g. "Agents (MCP, API)   http://127.0.0.1:7778/mcp"."""
    exe = meko_exe()
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "status"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if "agent" in line.lower():
            m = re.search(r"(https?://[^\s/]+)", line)
            if m:
                return m.group(1)
    return ""


def answers(url, tok):
    req = urllib.request.Request(url + "/api/v1/datapacks", headers={"Authorization": "Bearer " + tok})
    try:
        return "data" in json.loads(urllib.request.urlopen(req, timeout=3).read())
    except (urllib.error.URLError, OSError, ValueError):
        return False


def base_url(tok):
    """MEKO_URL if set, else the first candidate that serves Meko's API with this token."""
    global MEKO_URL
    if MEKO_URL:
        return MEKO_URL
    tried = []
    for url in [status_url()] + [f"http://127.0.0.1:{p}" for p in CANDIDATE_PORTS]:
        if url and url not in tried:
            tried.append(url)
            if answers(url, tok):
                MEKO_URL = url
                return url
    raise MekoError("Meko's agent API did not answer at " + ", ".join(tried) +
                    "; is Meko running? Set MEKO_URL if it listens elsewhere.")


def call(tok, name, args, timeout=20):
    """One MCP tools/call on Meko's local endpoint."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}).encode()
    req = urllib.request.Request(base_url(tok) + "/mcp", data=body, headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    try:
        out = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise MekoError(f"{name}: Meko is not reachable at {MEKO_URL} ({e})")
    if "error" in out:
        raise MekoError(f"{name}: {out['error']}")
    text = out["result"]["content"][0]["text"]
    try:
        val = json.loads(text)
    except ValueError:
        raise MekoError(f"{name}: {text[:200]}")
    if isinstance(val, dict) and val.get("error"):
        raise MekoError(f"{name}: {val['error']} {val.get('detail', '')}".strip())
    return val


def rest(tok, path, timeout=5):
    url = base_url(tok)
    req = urllib.request.Request(url + "/api/v1" + path, headers={"Authorization": "Bearer " + tok})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())["data"]
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        raise MekoError(f"GET {url}/api/v1{path}: {e}")


def datapack_name(slug):
    return ("review-loop guide " + slug)[:60]


def datapack(tok, slug, create):
    name = datapack_name(slug)
    for d in rest(tok, "/datapacks"):
        if d.get("datapack_name") == name:
            return d["datapack_id"]
    if not create:
        return None
    boot = call(tok, "conversation_create", {"agent_id": AGENT, "session_id": "review-loop-setup",
                                             "title": "review-loop setup"})["id"]
    return call(tok, "datapack_create", {
        "name": name, "conversation_id": boot, "agent_id": AGENT,
        "description": f"Private author guide for {slug}, written by review-loop's exit step. "
                       "Never give it to reviewers."})["datapack_id"]


def pull(tok, slug):
    dp = datapack(tok, slug, create=False)
    if not dp:
        return ""
    items = rest(tok, f"/datapacks/{dp}/artifacts?q={urllib.parse.quote(GUIDE_ARTIFACT)}")["items"]
    items = [i for i in items if i["filename"] == GUIDE_ARTIFACT]
    if not items:
        return ""
    art = rest(tok, f"/datapacks/{dp}/artifacts/{items[0]['content_hash']}")
    return base64.b64decode(art["content_base64"]).decode()


def push(tok, slug, text, history):
    dp = datapack(tok, slug, create=True)
    conv = call(tok, "conversation_create", {"datapack_id": dp, "agent_id": AGENT,
                                             "session_id": "review-loop-guide-" + slug.replace("/", "__"),
                                             "title": f"Author guide revisions: {slug}"})["id"]
    art = call(tok, "artifact_put", {"datapack_id": dp, "conversation_id": conv, "agent_id": AGENT,
                                     "filename": GUIDE_ARTIFACT, "content_type": "text/markdown",
                                     "content_base64": base64.b64encode(text.encode()).decode()})
    if history:
        call(tok, "memory_add", {"datapack_id": dp, "conversation_id": conv, "agent_id": AGENT, "text": history})
    return art["content_hash"]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if cmd == "check":
            tok = token()
            rest(tok, "/datapacks")
            print(f"ok: Meko is running at {base_url(tok)}")
        elif cmd == "pull":
            sys.stdout.write(pull(token(), os.environ["RL_REPO_SLUG"]))
        elif cmd == "push":
            text = open(os.environ["RL_GUIDE_FILE"]).read()
            hist = os.environ.get("RL_HISTORY_FILE") or ""
            history = open(hist).read().strip() if hist else ""
            print("meko:" + push(token(), os.environ["RL_REPO_SLUG"], text, history)[:12])
        else:
            sys.exit(__doc__)
    except MekoError as e:
        sys.exit(f"meko store: {e}")
    except KeyError as e:
        sys.exit(f"meko store: {e.args[0]} is not set (rl.py sets it)")


if __name__ == "__main__":
    main()
