# Features

## Connections
- Named connection profiles stored locally
- Passwords stored securely in GNOME Keyring
- Warning shown if the secrets service is unavailable
- Field validation — Name, Host, and Username are required before saving
- SSH tunnel support with key-based auth and optional passphrase
- Browse button to pick the SSH private key file
- Test connection before saving
- Edit and delete connections
- Active connection highlighted with a dot indicator in the sidebar
- Active connection name shown above the database browser
- Disconnect option in the connection row menu

## Database Browser
- Browse schemas, tables, and views in a tree sidebar
- Tables and views grouped separately per schema
- Live filter bar — type to narrow tables and views by name; tree expands to show matches and restores previous expansion on clear
- Spinner shown while connecting
- Press Enter to open the selected table or view
- Switching connections closes table tabs from the previous connection

## Table Inspector
Tabs available for each object type:

| Tab | Tables | Views |
|-----|--------|-------|
| Schema | ✓ | ✓ |
| Keys | ✓ | |
| Relations | ✓ | |
| Triggers | ✓ | ✓ |
| Indexes | ✓ | |
| DDL | ✓ | |
| Definition | | ✓ |
| Data | ✓ | ✓ |

- Data tab is paginated — 100, 500, or 1000 rows per page (configurable); Prev/Next buttons navigate between pages; current row range shown
- Filter bar above the data grid — type to instantly filter visible rows by any cell value (client-side, no extra query)
- Columns are sortable — click a column header to sort ascending/descending; numeric values sort numerically, NULLs sort first
- NULL values are shown distinctly (greyed "NULL" label)
- Right-click on a data cell to copy the cell value
- Right-click on selected rows to copy as CSV, JSON, or INSERT SQL (tables only)
- Right-click anywhere to copy all visible rows as CSV, JSON, or INSERT SQL (tables only)
- Right-click to export the current page to a file as CSV, JSON, or INSERT SQL
- Export button in the nav bar exports the full table (all rows, no page limit) as CSV, JSON, or INSERT SQL (tables only)
- Row count estimate and total size on disk shown in a status bar at the bottom of the panel (tables only; sourced from PostgreSQL statistics, no extra query needed)
- Empty state shown per tab when there is no data to display
- Refresh button reloads all tabs for the current table (also Ctrl+R)

## SQL Editor
- Syntax highlighting (GtkSourceView, respects dark mode)
- Line numbers and current-line highlight
- Auto-save with 800ms debounce; unsaved-changes dot indicator shown in toolbar
- Manual save with Ctrl+S; brief "Saved" confirmation shown after saving
- Active connection name shown in the editor toolbar; run buttons disabled when no connection is active
- **Run All** (F5) — executes the entire buffer as one or more statements
- **Run Selected** (Ctrl+Enter) — executes the selected text, or the statement at the cursor if nothing is selected
- **Cancel** — stops a running query mid-execution; shown in place of the run buttons while a query is active
- Single-statement queries show results inline in the resizable results pane
- Multi-statement scripts show a results log listing each statement's outcome (rows affected, row count, error, or cancelled); SELECT results open as additional closeable tabs
- Row count shown after each query
- Spinner shown while a query is running
- Right-click on results to copy the cell value, copy selected rows as CSV or JSON, or copy all rows as CSV or JSON
- Connected to the active database connection

## File Explorer
- Navigate the filesystem from a sidebar pane; current path shown in the toolbar
- Up button and Home button for quick navigation; double-click a folder to enter it
- Shows folders and `.sql` files only; other file types are listed but not selectable
- Create new folders and `.sql` files inline (`.sql` extension appended automatically if omitted)
- New `.sql` files open automatically in the editor on creation
- Double-click to open `.sql` files in the editor
- Right-click to rename or delete files and folders
- Deleting a file closes its open editor tab
- Renaming a file updates its open editor tab title
- Remembers last visited folder across sessions

## GNOME Integration
- GTK4 + libadwaita, follows system dark/light mode
- Font preferences — family (system/sans/serif/mono) and size, separately for sidebar and main content
- Keyboard shortcuts (Ctrl+W, Ctrl+Tab, Ctrl+Shift+Tab, Alt+1–9, Ctrl+R, F5, Ctrl+Enter, Ctrl+S, Ctrl+Q, Ctrl+,, Ctrl+?)
- Resizable panes with persisted positions
- Empty state shown when no tabs are open
