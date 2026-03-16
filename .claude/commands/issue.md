Fix one or more GitHub issues end-to-end on a single feature branch.

Usage: /issue <issue-number> [issue-number ...]

Steps:

1. **Fetch all issues** listed in `$ARGUMENTS` using `gh issue view <number> --json number,title,body,labels,milestone` for each. Read titles, bodies, and any comments.

2. **Verify validity** of each issue by reading the relevant source files. Confirm:
   - The bug/problem described still exists in the current code
   - It has not already been fixed
   - If an issue is no longer valid, note it and exclude it from the plan.

3. **Draft a combined implementation plan** — be specific:
   - Which files will change and why (grouped by issue)
   - Exact functions/lines affected
   - What each fix looks like (pseudocode or actual code if straightforward)
   - Any edge cases each fix must handle
   Present the plan to the user and **wait for explicit approval before continuing**.

4. **Upon approval:**
   - Post the approved plan as a comment on each GitHub issue: `gh issue comment <number> --body "..."`
   - Derive the branch name from all issue numbers and a short slug from the first issue title:
     - Single issue: `issue-{number}-{short-slug}`
     - Multiple issues: `issue-{n1}-{n2}-...-{short-slug}`
   - Create the branch from main: `git checkout main && git checkout -b <branch-name>`

5. **Implement all fixes** — make all necessary code changes across all issues.

6. **Commit** with a message referencing all issues: `Fix #<n1>, #<n2>: <short summary>` plus a `Co-Authored-By` trailer. Do NOT push.

7. Report what was changed per issue and remind the user to review and push when ready.
