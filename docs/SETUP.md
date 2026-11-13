# Setting up review-loop

You can set up review-loop by hand (see the README), or let Claude Code do
it. Open Claude Code in the repository you want to set up and paste:

```
Set up review-loop for this repository by following
https://github.com/AmeyaKasture/claude-review-loop/blob/main/docs/SETUP.md
```

If you have a local clone, point it at the file instead:

```
Set up review-loop for this repository by following ~/claude-review-loop/docs/SETUP.md
```

Claude installs the plugin, surveys the repository, and, if you agree, adds
whatever is missing: a linter, a type-checker, a first test, a CI workflow,
and an `AGENTS.md` with the project's rules. Then it drafts the settings and
a review skill, asks you a few questions, writes the files, and checks that
every configured command runs. It works on a brand-new, empty project too.
It never commits or pushes.

If the plugin is already installed, run `/review-loop:setup` instead.

---

## Instructions for Claude

Follow these steps in order. The user has asked you to set up review-loop for
the repository in the current working directory.

### 1. Preconditions

- Run `git rev-parse --show-toplevel`. If it fails, ask whether this
  directory is the new project; if yes, the setup skill will offer
  `git init`. If not, stop and ask the user to open Claude Code in the
  project directory.
- Run `claude --version` and `python3 --version`. review-loop needs Python 3.8
  or later and `git`; `gh` is optional (only for answering PR comments).
  If something is missing, tell the user what to install and stop.

### 2. Install the plugin

1. Run `claude plugin list`. If it lists `review-loop@review-loop` as
   enabled, skip to step 3.
2. Ask the user which source to install from, recommending the first:
   - GitHub: `AmeyaKasture/claude-review-loop`
   - A local clone: its absolute path (for example `~/claude-review-loop`)
3. Ask whether to install for every project (scope `user`, recommended) or
   only this repository (scope `project`, which writes
   `.claude/settings.json` in the repository).
4. Install:
   ```
   claude plugin marketplace add <source> --scope <scope>
   claude plugin install review-loop@review-loop --scope <scope>
   ```
5. Run `claude plugin list` again and confirm `review-loop@review-loop` is
   enabled. Note the path it prints for the plugin (`Read from:` or `Path:`);
   call it `PLUGIN_DIR`. If it does not print one, find it with
   `find ~/.claude/plugins -path '*review-loop*/skills/setup/SKILL.md'`.

### 3. Run the setup skill

Plugins installed from the command line load in new sessions, so this session
may not list `/review-loop:setup` yet.

- If the Skill tool lists `review-loop:setup`, invoke it.
- Otherwise read `$PLUGIN_DIR/skills/setup/SKILL.md` and follow it exactly,
  with `${CLAUDE_SKILL_DIR}` meaning `$PLUGIN_DIR/skills/setup`.

### 4. Finish

Tell the user:

- the plugin was installed (or already present), from where, and at which
  scope;
- the files the setup skill wrote, and which ones they may want to commit;
- to start a new Claude Code session so the plugin's skills, agents, and
  hooks are active, then use `/review-loop:review-loop` after their next
  change.
