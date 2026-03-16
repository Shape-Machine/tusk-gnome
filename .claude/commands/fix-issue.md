Fix a GitHub issue end-to-end.

Usage: /fix-issue <issue-number>

Steps:

1. **Fetch the issue** using `gh issue view $ARGUMENTS --json number,title,body,labels,milestone`. Read its title, body, and any comments.

2. **Verify validity** by reading the relevant source files. Confirm:
   - The bug/problem described still exists in the current code
   - It has not already been fixed
   - If the issue is no longer valid, tell the user and stop.

3. **Draft an implementation plan** — be specific:
   - Which files will change and why
   - Exact functions/lines affected
   - What the fix looks like (pseudocode or actual code if straightforward)
   - Any edge cases the fix must handle
   Present the plan to the user and **wait for explicit approval before continuing**.

4. **Upon approval:**
   - Post the approved plan as a comment on the GitHub issue: `gh issue comment $ISSUE --body "..."`
   - Create a branch named `issue-{number}-{short-slug}` from main: `git checkout main && git checkout -b issue-{number}-{slug}`

5. **Implement the fix** — make all necessary code changes.

6. **Commit** with message `Fix #<number>: <issue title>` plus a `Co-Authored-By` trailer. Do NOT push.

7. Report what was changed and remind the user to review and push when ready.
