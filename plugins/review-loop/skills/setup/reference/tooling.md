# Default tooling per stack

Used by the setup skill's "Fill the gaps" step when a repository lacks a
linter, a type-checker, tests, CI, or a rule file. Prefer what the repository
already uses; these are defaults for what is missing, not replacements.

## General rules

- Use the package manager the repository already uses (lockfile decides:
  `uv.lock` -> uv, `poetry.lock` -> poetry, `pnpm-lock.yaml` -> pnpm,
  `yarn.lock` -> yarn, `package-lock.json` -> npm). For a new project, pick
  the first one listed for the stack.
- Install tools as development dependencies only, never runtime ones.
- Put tool configuration in the existing manifest when the tool supports it
  (`pyproject.toml`, `package.json`), not in extra files.
- Never reformat or mass-fix existing code. Run each new linter once; if it
  reports more than about 20 problems on existing code, start from a smaller
  rule set (or exclude the existing directories) and tell the user the count,
  so the check passes on the default branch today and catches new problems.
- The first test is a smoke test: import the main module, or call one pure
  function with a known answer. Do not write a test suite.
- Every command must also be what the CI workflow runs, so the loop and CI agree.

## Python

| Piece | Default | Install (uv / poetry / pip) | Check command |
|---|---|---|---|
| Lint + format | ruff | `uv add --dev ruff` / `poetry add --group dev ruff` / `pip install ruff` (+ `requirements-dev.txt`) | `ruff check .` and `ruff format --check .` |
| Types | mypy | same pattern | `mypy <package dir>` |
| Tests | pytest | same pattern | `pytest -q` |

Configuration in `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.mypy]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Smoke test: `tests/test_smoke.py` importing the package.

## JavaScript / TypeScript

| Piece | Default | Install | Check command (as `package.json` scripts) |
|---|---|---|---|
| Lint | ESLint (flat config) with `typescript-eslint` for TS | `<pm> add -D eslint @eslint/js typescript-eslint` | `"lint": "eslint ."` |
| Types | `tsc` (TS only) | `<pm> add -D typescript` | `"typecheck": "tsc --noEmit"` |
| Tests | Vitest | `<pm> add -D vitest` | `"test": "vitest run"` |

`eslint.config.js`:

```js
import js from "@eslint/js";
import tseslint from "typescript-eslint";
export default tseslint.config(js.configs.recommended, ...tseslint.configs.recommended,
  { ignores: ["dist/", "build/", "coverage/"] });
```

Add a minimal `tsconfig.json` (`"strict": true`, `"noEmit": true`) only if none exists.
Smoke test: `src/<main>.test.ts` (or `tests/smoke.test.ts`).

## Go

Nothing to install for the basics.

| Piece | Check command |
|---|---|
| Lint | `go vet ./...` (add `golangci-lint run ./...` only if installed) |
| Format | `test -z "$(gofmt -l .)"` |
| Tests | `go test ./...` |

Smoke test: `<pkg>_test.go` next to the main package.

## Rust

| Piece | Check command |
|---|---|
| Lint | `cargo clippy --all-targets -- -D warnings` |
| Format | `cargo fmt --check` |
| Tests | `cargo test` |

Smoke test: a `#[cfg(test)] mod tests` in `src/lib.rs` or `src/main.rs`.

## Java / Kotlin

Gradle (`./gradlew check`) and Maven (`mvn -q verify`) already compile and
run the tests. Add nothing; use those commands. If there are no tests,
add one JUnit smoke test under `src/test/`.

## Other stacks

Ask the user for their usual lint and test commands rather than guessing.

## CI workflow (GitHub Actions)

`.github/workflows/ci.yml`, only when the repository is on GitHub and has
no CI. Set up the language (`actions/setup-python`, `actions/setup-node`,
`actions/setup-go`, `dtolnay/rust-toolchain`), install dependencies the way
the project does, then run exactly the chosen check commands, one `run:` step
each. Trigger on `push` and `pull_request`.

## AGENTS.md

Only when the repository has no `AGENTS.md`, `CLAUDE.md`, or `REVIEW.md`.
At most about 60 lines, written from what you observed, not generic advice:

```markdown
# <Project name>

<One sentence: what this project is.>

## Commands
- Lint: `<cmd>`
- Type-check: `<cmd>`
- Test: `<cmd>`

## Layout
- `<dir>/`: <what lives there>

## Rules
- <Conventions visible in the existing code: error handling, naming, logging,
  where tests go. Only what the code actually does.>
- Add or update a test for every behaviour change.
- Never log secrets, tokens, or personal data.
```

If `CLAUDE.md` does not exist, suggest `ln -s AGENTS.md CLAUDE.md` so Claude
Code loads the same rules.
