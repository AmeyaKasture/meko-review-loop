# Examples

| File | What it shows |
|---|---|
| `typescript-app.review-loop.json` | A single-package TypeScript app: typecheck, lint, changed unit tests. |
| `python-service.review-loop.json` | A Python service: format and lint in check mode, mypy, fast tests only. |
| `go-monorepo.review-loop.json` | A monorepo: scoped Go checks (`{go_packages}`) for one module, plus a check for another sub-project. |
| `guide-store/git-store.sh` | An external author-guide store backed by a private git repository. |

Copy a settings file to your repository root as `.review-loop.json`, or keep
it private as `~/.review-loop/repos/<owner>__<repo>.json`. Then run
`rl.py config --probe` (or `/review-loop:setup`, which writes one for you).
