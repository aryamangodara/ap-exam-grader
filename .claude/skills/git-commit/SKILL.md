---
name: git-commit
description: Use whenever the user asks to commit, stage, or push changes for this repo. Examples that trigger this skill - "commit and push", "commit these changes", "push to GitHub", "stage X", "create a commit for...", "commit the fix". Walks through pre-commit checks, the project's commit-message style (lowercase imperative subject, root-cause + fix body, per-file bullets, Co-Authored-By trailer), safe staging (no `git add -A`, never stage secrets / student PDFs / generated output), HEREDOC commit syntax, push safety, splitting logically distinct changes across commits, recovery from pre-commit hook failures, and notebook-specific commit hygiene (the `nbstripout` filter strips outputs at staging time).
---

# Commit workflow

This skill encodes the commit conventions for the AP FRQ Auto-Grader project so every commit is review-ready and safe. Follow it whenever the user asks to commit, stage, or push.

## 1. Pre-commit checks (run in parallel)

```bash
git status                # see all untracked files — NEVER use `-uall`
git diff --stat           # quick summary of what changed
git log --oneline -5      # match the recent commit-style
```

Look at the last 3-5 commit subjects to mirror their cadence (lowercase, imperative, no trailing period). If you're about to commit a notebook, also confirm the `nbstripout` filter is active — the diff should show only source-line changes, no `outputs` arrays or base64 image blobs.

## 2. Draft the message

The project uses lowercase imperative subjects under ~70 characters that capture the **why**, not just the **what**. The body explains in three short paragraphs: the problem, the root cause, the fix.

### Subject verbs

- `fix X` — bug fix
- `add X` — new file or capability
- `handle X` / `support X` — new code path
- `factor X` / `refactor X` — code reorganisation, no behaviour change
- `tighten X` — typing or validation hardening
- `require X` — new prompt or contract constraint
- `normalize X` — data-shape consistency
- `rescue X` / `recover X` — resilience fix
- `update X` — prose / config / dependency tweak

Avoid `improve X`, `tweak X`, `misc X`, `WIP`, leading capitals, trailing periods, and emoji.

### Body shape

```
subject line (under 70 chars, lowercase imperative)

opening paragraph: the user-facing problem. Name the file, the function,
the scorecard row, the expected vs actual behaviour. Be concrete.

root-cause paragraph: what was structurally wrong. Reference the helper
or invariant by name (`_looks_like_subpart`, `flatten_rubric_by_subpart`,
the granularity invariant in CLAUDE.md, etc.).

fix paragraph: what changed and why this shape. If the fix has two or
three parts, enumerate them — keep the sentences short.

- path/to/file_one.py: one-line summary of what changed there
- path/to/file_two.py: one-line summary of what changed there

Co-Authored-By: Claude <noreply@anthropic.com>
```

The per-file bullets are short — they exist so a reviewer can navigate the diff, not to restate the body. Keep them to one line each.

### Co-Author trailer

Always end Claude-drafted messages with `Co-Authored-By: Claude <noreply@anthropic.com>`. If the current Claude model is known (e.g. `Sonnet 4.6`, `Opus 4.7`), include it: `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`. The trailer must be the last line.

## 3. Stage safely

**NEVER use `git add -A` or `git add .`** — both can sweep in untracked content that slipped past `.gitignore` (a stray `.env`, a debug PDF, a `.secrets/` payload, large binaries). Always stage specific files by path:

```bash
git add helpers.py grader.ipynb config.py
```

If the user has staged unrelated changes already, run `git status` first and confirm with them before adding more.

### Paths that must never be committed

(All `.gitignore`d, but be explicit — defence in depth):

- `.env`, anything under `.secrets/`, `*service-account*.json`, `*-credentials*.json`
- `data/**/*.pdf`, `rubrics/*.pdf` — student exam scans and College Board rubrics (copyright)
- `*.parsed.json` — rubric parse caches (large, regenerable)
- `out/*` (except `out/.gitkeep`) — generated scorecards (regenerable)
- `__pycache__/`, `*.pyc`, `.ipynb_checkpoints/`
- Scratch / disposable scripts: `_apply_*.py`, `_tmp_*.py`, `.tmp_*.py`, `_verify.py` — delete these before committing if they're left over from the session

## 4. Commit with HEREDOC

For multi-paragraph messages, use a single-quoted HEREDOC. The single quotes around `'EOF'` prevent shell expansion of `$variables`, backticks, and backslashes inside the message:

```bash
git commit -m "$(cat <<'EOF'
subject line here

problem paragraph...

root-cause paragraph...

fix paragraph...

- file1.py: what changed
- file2.py: what changed

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

After committing, run `git status` to confirm a clean tree before pushing.

## 5. Push

Only after a clean commit, and only when the user asked:

```bash
git push origin main
```

If push is rejected because remote is ahead:

```bash
git fetch origin
git log HEAD..origin/main          # see what's there
git pull --rebase origin main      # reapply local commits on top
# resolve any conflicts, then:
git push origin main
```

## 6. Split commits when changes are logically distinct

If the staged set mixes (a) a substantive fix, (b) an unrelated refactor, and (c) an incidental settings tweak — split. Each commit should be reviewable on its own. Typical order: substantive fix first, then refactors, then incidentals.

To unstage selectively without losing changes:

```bash
git reset HEAD path/to/unrelated_file.py     # unstage that file
git diff --cached --stat                     # confirm what's left staged
```

Do not use `git add -p` to split — too easy to commit half a logical hunk and break tests.

## 7. Forbidden operations

These come from `CLAUDE.md` and apply unless the user **explicitly** asks for the destructive form:

- `git commit --amend` — always make a NEW commit, even after a pre-commit hook failure
- `git push --force`, `git push --force-with-lease` to `main` — both rewrite shared history; never to `main` without an explicit instruction
- `git commit --no-verify`, `--no-gpg-sign` — don't skip hooks or signing
- `git reset --hard`, `git clean -f`, `git checkout --` for destructive use — only on explicit instruction
- `git config` updates — only when the user asks
- `git rebase -i`, `git add -i` — interactive editors that don't work in this harness

## 8. If a pre-commit hook fails

The commit did **not** happen — there's nothing to amend. Steps:

1. Read the hook output to identify the failure
2. Fix the underlying issue (linter complaint, format violation, large file, missing test)
3. Re-stage the now-fixed files (`git add ...`)
4. Create a NEW commit with the same message

Do not chain failed attempts via `--amend` — each commit attempt is independent.

## 9. Notebook (`.ipynb`) commit hygiene

`grader.ipynb` is the live notebook; `grader2.ipynb` / `grader3.ipynb` are read-only backups. Notebook commits have specific rules (see CLAUDE.md "Editing the notebook"):

1. **Never hand-edit the .ipynb JSON directly** when changing cell sources. Write a small Python apply-script that reads the JSON, replaces the target cell's `source` (load the new code from a `.txt` first to avoid `\n`-escaping in f-strings), and writes back with `json.dump(..., indent=1, ensure_ascii=True) + "\n"`. Then delete the apply-script before staging.

2. **Verify the diff before committing.** Run `git diff --stat grader.ipynb` — if you see hundreds of lines of diff in output cells, the `nbstripout` filter isn't running. Fix that before committing rather than committing the bloated diff.

3. **Stage `grader.ipynb` by name** — the `nbstripout` filter strips outputs at staging time, so the on-disk file keeps its outputs for the user's interactive session while the committed version is output-free.

4. **Trailing newline on the last cell source.** Convention is "no trailing newline on the last line of the last cell" — apply-scripts should `.rstrip("\n")` the final element of the source list to keep the diff to source changes only.

## Quick template

For a typical fix-and-commit-and-push cycle:

```bash
# 1. Pre-flight (parallel)
git status; git diff --stat; git log --oneline -5

# 2. Stage specific files
git add helpers.py grader.ipynb

# 3. Commit
git commit -m "$(cat <<'EOF'
fix <thing> in <component>

<problem paragraph>

<root-cause paragraph>

<fix paragraph>

- helpers.py: <one-line summary>
- grader.ipynb: <one-line summary>

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"

# 4. Push
git push origin main
```

## Sanity-check the result

After pushing, the commit should:

- Have a subject under 70 characters, lowercase, imperative
- Have a body that someone unfamiliar with the conversation could understand
- Touch only files the user expected (no `.env`, no PDFs, no `out/`, no scratch scripts)
- Carry the Co-Authored-By trailer
- Not appear above any user-authored commit they didn't ask you to amend or rebase

If any of those is off, mention it to the user — don't try to silently `--amend` to fix it.
