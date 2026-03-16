Show GitHub issues for a milestone.

Usage: /milestone [milestone-title]

If no milestone is provided, default to the current milestone (the earliest open milestone by due date).

Steps:

1. **Resolve the milestone:**
   - If `$ARGUMENTS` is provided, use it as the milestone title (e.g. "2026.03")
   - If not provided, run `gh api repos/Shape-Machine/tusk-gnome/milestones?state=open&sort=due_on&direction=asc` and pick the first result (earliest due date) as the current milestone

2. **Fetch issues** for the resolved milestone:
   ```
   gh issue list --milestone "<title>" --state all --json number,title,state,labels,assignees --limit 100
   ```

3. **Display** a clean summary grouped by state:
   - Show the milestone title and due date as a header
   - Group into **Open** and **Closed** sections
   - For each issue show: `#number  title  [labels]`
   - Show counts: X open, Y closed, Z total
