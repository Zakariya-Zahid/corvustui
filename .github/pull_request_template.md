## What changed

<!-- Describe the change and the user-visible behavior. -->

## Why

<!-- Link the issue or roadmap epic. -->

Closes #

## How it was tested

- [ ] `pytest -q`
- [ ] `pytest -q -m integration`
- [ ] `ruff check src tests --select F,E9,I001`
- [ ] Manual CLI check
- [ ] Manual TUI check at 80x24 and 120x40

## Risk areas

- [ ] Providers
- [ ] Safety / approvals
- [ ] Storage / sessions
- [ ] Dependencies
- [ ] TUI only
- [ ] Docs only

## Checklist

- [ ] Tests added or updated
- [ ] Docs updated
- [ ] No secrets, `.env` files, local databases, or generated build files included
- [ ] PR is focused and reasonably small
- [ ] New dependencies are justified and pinned
