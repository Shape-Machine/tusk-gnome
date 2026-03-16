Show GitHub issues for a milestone, with open issues grouped by implementation affinity.

Usage: /milestone [milestone-title]

If no milestone is provided, default to the current milestone (the earliest open milestone by due date).

Steps:

1. **Resolve the milestone:**
   - If `$ARGUMENTS` is provided, use it as the milestone title (e.g. "2026.03")
   - If not provided, run `gh api repos/Shape-Machine/tusk-gnome/milestones?state=open&sort=due_on&direction=asc` and pick the first result (earliest due date) as the current milestone

2. **Fetch issues** for the resolved milestone:
   ```
   gh issue list --milestone "<title>" --state all --json number,title,state,labels,body --limit 100
   ```

3. **Display** a clean summary:
   - Show the milestone title and due date as a header

   **Open issues — grouped by implementation affinity:**
   Analyse the open issues and group them by which ones can be implemented together in a single branch (same files, same subsystem, or tightly related behaviour). For each group:
   - Give the group a short name (e.g. "Empty states", "Loading indicators", "Connection dialog")
   - List the issues: `#number  title  [labels]`
   - Suggest a `/issue` invocation: e.g. `/issue 17 18 20`

   **Closed issues** — flat list:
   - For each issue show: `#number  title  [labels]`

   Show counts: X open, Y closed, Z total
