---
name: pr
description: Create a pull request for the current branch
disable-model-invocation: true
argument-hint: "[base branch]"
---

Create a PR from the current branch. Base: `$ARGUMENTS` (default: `main`).
Repo: `haalfi/integration-showcase`.

For all GitHub API calls: use `github-pat` first (read+write), fall back to `MCP_DOCKER` for reads only.

## Steps

1. **Pre-check:** Verify not on main, working tree clean, branch pushed to remote.
   Push with `-u` if needed.

2. **Coverage gate:** Check `git diff main...HEAD --name-only` for files under `src/` or `tests/`.
   - If any match: run `hatch run test-cov` (requires 80%). Stop and report if it fails.
   - If none match (docs/config-only): skip coverage.

3. **Gather context:** `git log main..HEAD --oneline` and `git diff main...HEAD`.

4. **Draft PR:** Title (<70 chars) + body:
   ```
   ## Summary
   <1-3 bullet points>

   ## Test plan
   - [ ] ...
   ```

5. **Create PR** using `create_pull_request`:
   - `owner: "haalfi"`, `repo: "integration-showcase"`
   - `head:` current branch, `base:` main (or `$ARGUMENTS`)

6. **Report** the PR URL.

## Rules

- Only creates the PR. Do not merge or approve.
- Do not push to main.
