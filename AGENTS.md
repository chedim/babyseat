# AGENTS.md

Working instructions for AI coding agents in this repository (membrane).

## What membrane is
membrane is a memory proxy that sits between AI clients and the model server: it remembers per entity (files, symbols, commands, words) and gives agents continuity across turns. Start at README.md and doctrine.md; design notes live in docs/ (entity-field.md, opencode-usage.md, shared-field-workflow.md, background-mind.md).

## Ground rules
1. Run the tests before committing: pytest (suites live in tests/).
2. Keep docs honest in the same change: README.md maps the entry points, doctrine.md holds the operating doctrine, docs/ holds deep dives. CHANGELOG.md is edited only at release time, never in a feature or fix PR.
3. Sync with origin via fetch + ff-only; never rewrite shared history.
4. No secrets in code, docs or comments: tokens come from the environment.
5. Keep new commands small and named; prefer composing existing ones over one-off scripts.

## Claim an issue before working on it
Several agents work this repository at once, all under the same GitHub account, so an issue must be claimed before any code is written. Without a claim, two runs that start together both see "no PR yet" and build the same fix (#605 and #606 both fixed #597).

1. Check: skip the issue if it has the `claimed` label or an open PR mentions it (`gh issue view N --json labels`, `gh pr list --search "N"`).
2. Claim: `gh issue edit N --add-label claimed`, then comment `claim: issue-N` (add your worktree path).
3. Re-read the issue's comments. If an earlier `claim:` comment than yours exists, delete yours and pick another issue. The earliest claim wins.
4. Work on the branch `issue-N` exactly. If another run got past step 3 anyway, its push to the same branch fails instead of opening a second PR.
5. Release: the PR's `Fixes #N` closes the issue. If you give up, remove the label and comment `released: issue-N`.

A run that hands issues to parallel workers claims each issue itself before handing it out, one issue per worker.

## When you finish a task
Before declaring a task complete, re-check that README.md, doctrine.md and the touched docs/ pages still match reality; this file is the agents-side checklist.
