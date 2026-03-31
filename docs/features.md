# Features

_Generated from source: v2026.03.30-00_

## Connections

- Named connection profiles stored locally (`~/.config/tusk/connections.json`)
- Passwords stored securely in GNOME Keyring; warning shown if keyring is unavailable
- SSH tunnel support: host, port, user, private key file (file picker), optional passphrase
- Test connection before saving
- Edit and delete connections
- Active connection highlighted with a dot indicator in the sidebar
- Active connection name shown above the database browser
- Disconnect option in the connection row menu

## Schema Browser

- Tree sidebar with schemas, tables, views, sequences, enums, and functions
- Tables and views grouped separately per schema
- Live filter bar — type to narrow by name; tree expands to show matches and restores on clear
- Spinner shown while connecting; switching connections closes table tabs from the previous connection

## Table Inspector

- Eight tabs per table: Schema, Keys, Relations, Triggers, Indexes, DDL, Definition (views only), Data
- **Schema** — column name, type, length, nullable, default value
- **Keys** — constraint name, type (PRIMARY KEY / UNIQUE), column list
- **Relations** — foreign key constraints with column, referenced table and column, ON UPDATE/DELETE actions
- **Triggers** — name, event, timing, orientation, statement
- **Indexes** — name and full `CREATE INDEX` definition
- **DDL** — full `CREATE TABLE` statement (read-only)
- **Definition** — view definition SQL (views only)
- **Data** — paginated data browser with inline insert, edit, and delete (see Data Browser section)
- All tabs lazy-load on first access; refresh button reloads all tabs (Ctrl+R)
- Row count estimate and total size on disk shown in a status bar (tables only)

## Data Browser

- Configurable pagination: 100, 500, or 1,000 rows per page; Prev/Next navigation; current range shown
- Client-side filter bar — type to instantly narrow visible rows by any cell value
- Sortable columns — click header for ascending/descending; numeric values sort numerically; NULLs sort first
- NULL values shown distinctly with a greyed "NULL" label
- Insert new rows via modal form with type-aware inputs and NULL/default handling (tables with a primary key only)
- Edit existing rows via modal form; generates `UPDATE` via primary key (tables with a primary key only)
- Delete selected rows with confirmation; navigates back a page if the current page becomes empty (tables with a primary key only)
- Right-click a cell to copy its value
- Right-click selected rows to copy as CSV, JSON, or INSERT SQL
- Export button exports the full table (all rows, no page limit) as CSV, JSON, or INSERT SQL

## SQL Editor

- Syntax highlighting via GtkSourceView; respects system dark/light mode
- Line numbers and current-line highlight
- Auto-save with 800 ms debounce; unsaved-changes indicator shown in toolbar; manual save with Ctrl+S
- **Run All** (F5) — executes the entire buffer as one or more statements
- **Run Selected** (Ctrl+Enter) — executes selected text, or the statement at the cursor
- **EXPLAIN** — runs EXPLAIN on the current statement; results shown as a collapsible tree
- **EXPLAIN ANALYZE** — runs EXPLAIN ANALYZE; tree annotated with actual row counts and timing
- **Cancel** — stops a running query mid-execution
- Custom multi-statement parser: splits on semicolons while respecting string literals, dollar-quoting, and comments
- Multi-statement results: a log lists each statement's outcome; SELECT results open as additional closeable tabs
- Query history — recent queries listed with timestamp and execution duration; click to restore
- Toggle line comment with Ctrl+/ (adds or removes `--` prefix)
- Row count shown after each query; spinner shown while a query is running
- Right-click results to copy a cell value, copy selected rows as CSV or JSON, or copy all rows as CSV or JSON

## File Explorer

- Filesystem sidebar; current path shown in the toolbar
- Up and Home buttons for quick navigation; double-click a folder to enter it
- Shows folders and `.sql` files only
- Create new folders and `.sql` files inline; new files open automatically in the editor
- Double-click to open `.sql` files in the editor
- Right-click to rename or delete files and folders; deleting a file closes its open editor tab
- Remembers last visited folder across sessions

## Appearance

- Font preferences: family (system default, sans-serif, serif, monospace) and size (8–20 pt), separately for sidebar and main content
- GTK4 + libadwaita; follows system dark/light mode automatically
- Resizable panes with persisted positions

## Keyboard Shortcuts

- `Ctrl+,` — Preferences
- `Ctrl+Q` — Quit
- `Ctrl+W` — Close Tab
- `Ctrl+Tab` / `Ctrl+Shift+Tab` — Next / Previous Tab
- `Alt+1`–`Alt+9` — Go to Tab N
- `Ctrl+R` — Refresh Tab
- `F5` — Run All (SQL editor)
- `Ctrl+Enter` — Run Selected (SQL editor)
- `Ctrl+S` — Save file (SQL editor)
- `Ctrl+?` — Keyboard shortcuts reference
