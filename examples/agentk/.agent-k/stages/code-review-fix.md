AgentK reviewed this change locally ({{strategy}}, round {{round}}) {{why}}. Address each finding below.

You wrote this code, so you are the most biased judge of these findings. Follow these rules:

1. Verify each finding against the code before changing anything. Prefer evidence you execute: run the relevant test, or write a minimal reproduction.
2. **Do not decline a blocker or major finding on your own judgement.** First spawn an independent, read-only verifier subagent: `review-loop:finding-verifier` if it is listed, otherwise a general-purpose subagent told not to edit files. Give it only the finding, your rebuttal, and the file paths, not this conversation. Decline only if it answers NOT_REAL, and quote its evidence. If it answers UNCERTAIN, decline nothing: say that the finding needs the user's decision, and give two concrete options.
3. Never fix by suppression. Silencing a warning, adding an ignore comment, loosening a type, weakening or deleting a test, or swallowing an exception is not a fix.
4. Fix every valid finding at its root cause. For a valid blocker or major correctness finding, add or extend a test that fails without the fix.
5. Run the repo's tests and checks. {{after}}
6. {{outcomeHow}}

{{findings}}
