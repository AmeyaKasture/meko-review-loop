# Bringing review-loop's triage rules into AgentK

AgentK (the coding extension of offline Meko, `yugabyte/meko`) already runs a
strong local review loop before each PR. Its reviewers are fresh subagents,
and an independent scorer filters every finding. Its weak point is the hand-off:
the authoring task "fixes or declines" each finding with no independent check
on a decline.

These two prompt overrides add review-loop's rule to that hand-off: a blocker
or major finding can be declined only after an independent verifier agrees.
They keep every AgentK placeholder (`{{strategy}}`, `{{round}}`, `{{why}}`,
`{{after}}`, `{{outcomeHow}}`, `{{findings}}`), so AgentK's flow is unchanged.

## Use

Copy the folder into the repository AgentK works on, and commit it:

```
cp -R examples/agentk/.agent-k /path/to/repo/
```

AgentK reads `.agent-k/stages/<name>.md` only from trusted repositories, and
never from a public repository's PR head. Install the `review-loop` plugin so
that `review-loop:finding-verifier` is available in AgentK's Claude Code
sessions. Without the plugin, the prompts fall back to a general-purpose
subagent.

## Caveat

These overrides replace AgentK's built-in wording for these two prompts. When
AgentK changes its own versions (`src/server/prompts.ts`), compare them and
carry over anything new.
