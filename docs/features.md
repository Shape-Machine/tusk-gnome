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

- Data tab is paginated — 100, 500, or 1000 rows per page (configurable); Prev/Next buttons navigate between pages
- NULL values are shown distinctly (greyed "NULL" label)
- Right-click on a data cell to copy the cell value
- Right-click on selected rows to copy as CSV, JSON, or INSERT SQL (tables only)
- Empty state shown per tab when there is no data to display
- Refresh button reloads all tabs for the current table (also Ctrl+R)

## SQL Editor
- Syntax highlighting (GtkSourceView, respects dark mode)
- Auto-save with 800ms debounce
- Manual save with Ctrl+S
- Run query with F5 or Ctrl+Enter
- Runs selected text or full buffer
- Inline results pane (resizable)
- Row count shown after each query
- Spinner shown while a query is running
- Right-click on results to copy as CSV or JSON
- Connected to the active database connection

## File Explorer
- Navigate the filesystem from a sidebar pane
- Shows folders and `.sql` files only
- Create new folders and `.sql` files inline
- Double-click to open `.sql` files in the editor
- Right-click to rename or delete files and folders
- Deleting a file closes its open editor tab
- Renaming a file updates its open editor tab title
- Remembers last visited folder across sessions

## GNOME Integration
- GTK4 + libadwaita, follows system dark/light mode
- Font preferences — family (system/sans/serif/mono) and size, separately for sidebar and main content
- Keyboard shortcuts (Ctrl+W, Ctrl+Tab, Ctrl+Shift+Tab, Alt+1–9, Ctrl+R, Ctrl+Q, Ctrl+,, Ctrl+?)
- Resizable panes with persisted positions
- Empty state shown when no tabs are open
