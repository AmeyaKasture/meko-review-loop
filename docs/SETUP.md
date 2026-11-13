# Setting up Meko Review Loop

You can set it up by hand (see the README), or let your coding agent do it.
Open your agent (Claude Code, Codex, Cursor, Gemini CLI, or another) in the
repository you want to set up and paste:

```
Set up Meko Review Loop for this repository by following
https://github.com/AmeyaKasture/meko-review-loop/blob/main/docs/SETUP.md
```

The agent installs Meko Review Loop for itself, surveys the repository, and,
if you agree, adds whatever is missing: a linter, a type-checker, a first
test, a CI workflow, and an `AGENTS.md` with the project's rules. It checks
for Meko and offers to install and connect it. Then it drafts the settings
and a review skill, asks you a few questions, writes the files, and checks
that every configured command runs. It works on a brand-new, empty project
too. It never commits or pushes.

If it is already installed, run the setup skill instead
(`/review-loop:setup` in Claude Code, `review-loop-setup` elsewhere).

---

## Instructions for the agent

Follow these steps in order. The user has asked you to set up Meko Review
Loop for the repository in the current working directory. Where a step says
*ask*, use your question tool if you have one, otherwise a short numbered
list of options.

### 1. Preconditions

- Run `git rev-parse --show-toplevel`. If it fails, ask whether this
  directory is the new project; if yes, the setup skill will offer
  `git init`. If not, stop and ask the user to open the agent in the
  project directory.
- Run `python3 --version` and `git --version`. Python 3.8 or later and git
  are required; `gh` is optional (only for answering PR comments). If
  something is missing, tell the user what to install and stop.
- Work out which agent you are: `claude`, `codex`, `cursor`, `gemini`, or
  another (use `agents`).

### 2. Install

1. Check for an existing install: `claude plugin list` (Claude Code) lists
   `review-loop@meko-review-loop`; for other agents look for
   `review-loop-setup/SKILL.md` in `.agents/skills`, `.codex/skills`,
   `.cursor/skills`, or `.gemini/skills`, in the repository and the home
   directory. If found, skip to step 3.
2. Ask where to get it from, recommending the first:
   - GitHub: `git clone https://github.com/AmeyaKasture/meko-review-loop.git ~/meko-review-loop`
   - A local clone the user names.
3. Ask whether to install for every project (recommended) or only this
   repository (`--project .`).
4. Run `<clone>/install.sh --agent <your agent> [--project .]`. For an agent
   with a skills directory not listed in the README, use
   `--dir <that directory>`.
5. Note where the setup skill now is (`install.sh` prints it; for Claude Code
   use the path `claude plugin list` prints, plus `/skills/setup/SKILL.md`).

### 3. Run the setup skill

Agents usually load new skills only in a new session.

- If your skill list already includes the setup skill, invoke it.
- Otherwise read the setup skill's `SKILL.md` from step 2.5 and follow it
  exactly. Where it says `${CLAUDE_SKILL_DIR}`, use that file's directory.

### 4. Finish

Tell the user:

- that Meko Review Loop was installed (or already present), from where, for
  which agent, and at which scope;
- what the setup skill reported (files written, Meko status, who reviews);
- to start a new session so the skills are loaded, then run the review-loop
  skill after their next change.
