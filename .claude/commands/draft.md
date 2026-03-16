Scan the codebase for issues and propose them one by one for GitHub logging.

Usage: /draft <focus>

Focus can be: bugs, performance, ux, features — or any free-text description of what to look for.

Steps:

1. **Understand the focus** from `$ARGUMENTS`. If empty, default to a general scan covering bugs, performance, and UX.

2. **Scan the codebase** in `src/` relevant to the focus:
   - bugs → logic errors, unhandled exceptions, race conditions, resource leaks, incorrect assumptions
   - performance → blocking main thread, unnecessary DB queries, missing caching, repeated work
   - ux → missing empty states, missing loading/error feedback, confusing flows, GTK anti-patterns
   - features → gaps in functionality, obvious missing capabilities given the app's purpose

3. **For each finding**, present it clearly:
   - One or two sentences describing the problem or opportunity
   - File and line reference if applicable
   - Suggested GitHub issue title

   Then ask the user: **"Log this as a GitHub issue? (yes / no / stop)"**
   - `yes` → create the issue immediately using `gh issue create` with an appropriate `--label` (priority:high / priority:medium / priority:low — create the label if it doesn't exist) and `--milestone` set to the current milestone (fetch with `gh api repos/Shape-Machine/tusk-gnome/milestones?state=open&sort=due_on&direction=asc` and use the first result's title)
   - `no` → skip and move to the next finding
   - `stop` → end the session

4. After all findings are exhausted (or user says stop), print a summary: how many findings total, how many logged.
