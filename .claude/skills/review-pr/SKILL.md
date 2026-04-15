---
name: review-pr
description: Post inline review comments on a GitHub PR. Find real issues only.
disable-model-invocation: true
context: fork
argument-hint: "[PR number] [optional context]"
allowed-tools: Read, Grep, Glob, mcp__github-pat__pull_request_read, mcp__github-pat__list_commits, mcp__github-pat__get_file_contents, mcp__github-pat__pull_request_review_write, mcp__github-pat__add_comment_to_pending_review, mcp__MCP_DOCKER__pull_request_read, mcp__MCP_DOCKER__list_commits, mcp__MCP_DOCKER__get_file_contents, mcp__MCP_DOCKER__pull_request_review_write, mcp__MCP_DOCKER__add_comment_to_pending_review
---

## ROLE: You are a REVIEWER. You are NOT an author. You do NOT fix anything.

Your ONLY output is review comments. Nothing else must be created or changed.

PR number and optional context are in `$ARGUMENTS`. Repo: `haalfi/integration-showcase`.

For all GitHub API calls: use `github-pat` first, fall back to `MCP_DOCKER` for reads.

## Step 1: Gather context

Use `pull_request_read` with `owner: "haalfi"`, `repo: "integration-showcase"`, `pullNumber: $ARGUMENTS`.
Read every changed file in full -- you need surrounding context.

## Step 2: Analyze

Priority: (1) Correctness, (2) Spec compliance (`sdd/DESIGN.md` invariants),
(3) Test coverage, (4) Consistency, (5) Security.

**Skip:** style (ruff handles it), docstrings on unchanged code, "consider X" without reason, praise.

**Envelope invariant check:** For any change touching `shared/envelope.py` or activity code,
verify the five invariants in `sdd/DESIGN.md § Envelope invariants`.

**remote-store boundary check:** Verify blob I/O goes through `shared/blob.py` (Store API),
never raw Azure SDK calls.

## Step 3: Consolidate findings

Deduplicate by category: Bug / Spec / Test / Consistency / Security.
Only post findings >=80% confidence.

## Step 4: Post review (pending-review flow)

1. `pull_request_review_write` with `method: "create"`, **no `event` parameter** (pending).
2. `add_comment_to_pending_review` per finding -- `subjectType: "LINE"` required.
3. `pull_request_review_write` with `method: "submit_pending"`, `event: "COMMENT"`.

Verify: call `pull_request_read` with `method: "get_review_comments"` -- if `totalCount: 0`,
retry once from step 1. After retry, stop and report.

**Never** use APPROVE or REQUEST_CHANGES.

## Step 5: Report

```
## PR #N Review -- X comments posted
Bug: N | Spec: N | Test: N | Consistency: N | Security: N
```

Then stop. Do not offer follow-ups.
