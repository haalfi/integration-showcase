# Claude Code Instructions

## Project

Python showcase: Temporal + Azure Blob Storage (via [remote-store](https://github.com/haalfi/remote-store)) +
OpenTelemetry for distributed saga orchestration with full traceability.
Spec-Driven Development (SDD). Concept document lives in `sdd/research/` (German).

## Principles

1. **Ship complete**: a change is finished when everything it touches is consistent:
   code, tests, docs, BACKLOG. Track gaps as `[~]`.
2. **Verify beyond the diff**: search for what references the thing you changed.
3. **Repo describes reality at every commit**: docs and backlog reflect current state,
   not future intent. Same commit, or mark `[~]`.
4. **Single source of truth**: authoritative references live in one place — link, don't copy.
5. **Specs are source of truth**: code vs. spec conflict: code is wrong.
6. **Run it, don't just type-check it**: verify behavior, not signatures.
7. **Be critical, not agreeable**: challenge assumptions, flag what's missing.

## Dev commands

Scripts are defined in `pyproject.toml` under `[tool.hatch.envs.default.scripts]`.
Run `hatch run` to list them. `hatch run all` is the pre-commit gate.

Claude-specific shell constraints:

- **No `&&`, `||`, or `;`.** Split into separate Bash tool calls for auto-approval.
- **No heredoc in git commits.** Use multiple `-m` flags instead.
- **No `/tmp/`.** Use `./tmp/` instead (gitignored).

## Branching

- **Never commit or push directly to main.** Always create a feature branch.
- Branch naming: `is-002-blob-client`, `fix-envelope-validation`, etc.
- Push the feature branch; the user will create PRs or ask you to.

## Backlog (mandatory)

- See `sdd/BACKLOG.md` for workflow rules and active items.
  Completed items live in `sdd/BACKLOG-DONE.md`.
- **Completing work:** done -> move item to `BACKLOG-DONE.md` (same commit).
  Partially done -> split: ship done part to `BACKLOG-DONE.md`, new ID here for remainder.
- Commit messages start with item ID when applicable (e.g., `IS-002: Add blob client`).

## GitHub operations

**Primary:** `github-pat` MCP server (fine-grained PAT, read+write).
**Fallback:** `MCP_DOCKER` for reads only.
**Last resort:** `gh` CLI.

PR workflows: `/pr`, `/review-pr`, `/fix-pr`.

## Code conventions

See `sdd/DESIGN.md` for code style and invariant rules.
See `sdd/TESTING.md` for testing conventions.
Run `hatch run lint` before committing.

## Documentation conventions

Concept/research documents in `sdd/research/` are written in German.
Code, comments, and API docs are in English.
