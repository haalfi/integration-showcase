---
name: fix-pr
description: Read review comments from a PR, fix each issue, resolve threads, validate
disable-model-invocation: true
argument-hint: "[PR number]"
---

PR: `$ARGUMENTS`. Repo: `haalfi/integration-showcase`.

For all GitHub API calls: use `github-pat` first, fall back to `MCP_DOCKER` for reads only.

## Step 1: Fetch comments

Check out the PR branch. Fetch all comment sources (`owner: "haalfi"`, `repo: "integration-showcase"`):

| # | Tool | Method | Returns |
|---|---|---|---|
| 1 | `pull_request_read` | `get_review_comments` | Inline thread comments |
| 2 | `pull_request_read` | `get_comments` | Review-level comments |
| 3 | `pull_request_read` | `get_reviews` | Review body/summary |
| 4 | `issue_read` | `get_comments` | Top-level conversation |

If `gh` CLI available, fetch thread IDs for resolution:
```bash
gh api graphql -f query='query($owner:String!,$repo:String!,$number:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviewThreads(last:100){nodes{id isResolved path line comments(first:10){nodes{body}}}}}}}' -f owner='haalfi' -f repo='integration-showcase' -F number=$ARGUMENTS
```

## Step 2: Triage

Build work list. Skip resolved, outdated, bot comments. Verify each claim against the code.

## Step 3: Fix

Read each file in full. Make the fix. Verify against `sdd/DESIGN.md` if the change touches
envelope invariants or the remote-store boundary.

## Step 4: Resolve threads

If gh available, batch-resolve fixed threads via GraphQL `resolveReviewThread` mutation.
Only resolve threads you fixed.

## Step 5: Validate

Run `hatch run lint` then `hatch run test`. Fix failures, re-run until clean.

## Step 6: Commit and push

Stage, commit (`fix: address PR #$ARGUMENTS review`), push. Report what was fixed/resolved/skipped.

## Rules

- Do not merge, close, or approve the PR.
- Fix what was asked -- don't refactor surrounding code.
