# Features
_Generated from source: v2026.04.01-00_

## Connections

- Named connection profiles stored locally (`~/.config/tusk/connections.json`)
- Passwords and SSH passphrases stored in system keyring (GNOME Keyring or compatible); warning shown if keyring is unavailable
- SSH tunnel: host, port, user, private key file (file picker), optional passphrase
- Read-only mode toggle — disables insert, edit, and delete operations for that connection
- Default schema field — sets the active schema after connecting
- Test connection before saving
- Import connections from a `.pgpass` file (wildcard entries are skipped)
- Copy connection as a PostgreSQL URI to the clipboard
- Edit and delete connections
- Active connection highlighted with an accent bar indicator in the sidebar
- Connection role badge (🛡) shown when the connected role has superuser privileges
- Disconnect option in the connection row context menu

## Schema Browser

- Tree sidebar with schemas → tables, views, sequences, enums, and functions per schema
- Roles section: login roles (person icon) and group roles (key icon), with attributes (superuser, createdb, createrole, inherit, replication) and group memberships displayed
- Live filter bar — type to narrow all schema objects by name; tree expands to show matches and restores on clear (`Ctrl+F` to focus)
- Database switcher in the header; drop database with confirmation
- Right-click a schema: Create Schema, Rename Schema, Drop Schema (CASCADE option)
- Right-click a table: Rename, Clone (with column selection), Truncate (with RESTART IDENTITY option), Drop (CASCADE option)
- Right-click a view: Drop (CASCADE option)
- Spinner shown while connecting; switching connections closes table tabs from the previous connection

## Table Inspector

- Eight tabs per table: Schema, Keys, Relations, Triggers, Indexes, DDL, Definition (views only), Data
- **Schema** — column name, type, length, nullable, default value; right-click to Rename, Change Type, Set Default, Toggle Nullable, Set as Primary Key, or Drop; toolbar `+` to add a column
- **Keys** — constraint name, type (PRIMARY KEY / UNIQUE / CHECK / FOREIGN KEY), associated columns; Add Constraint button; right-click to drop
- **Relations** — foreign key constraints with column mappings, referenced table and column, ON UPDATE/DELETE actions
- **Triggers** — name, event, timing, orientation, and statement text
- **Indexes** — name and full `CREATE INDEX` definition; Add Index button with name, type (B-tree, Hash, GiST, GIN, BRIN), column selection with sort order, UNIQUE flag, and CREATE CONCURRENTLY option; right-click to drop
- **DDL** — full `CREATE TABLE` statement (read-only); copy DDL button
- **Definition** — view definition SQL (views only; read-only)
- **Data** — paginated data browser with inline insert, edit, and delete (see Data Browser section)
- All tabs lazy-load on first access; `Ctrl+R` refreshes all tabs
- Row count estimate and total table size shown in a status bar (tables only)

## Data Browser

- Configurable pagination: 100, 500, or 1,000 rows per page; Prev/Next navigation with current range shown; page size persisted
- Client-side filter bar — type to instantly narrow visible rows by any cell value (case-insensitive)
- Sortable columns — click header for ascending/descending; NULLs sort first
- Pinned (frozen) columns — right-click a column header to pin/unpin; pinned columns stay fixed on the left during horizontal scroll
- NULL values displayed with a distinct greyed "NULL" label
- Insert new rows via modal form with type-aware inputs and NULL/default handling (tables with a primary key only)
- Edit existing rows via modal form; generates `UPDATE` via primary key (tables with a primary key only)
- Delete selected rows with confirmation; navigates back a page if the current page becomes empty (tables with a primary key only)
- Multi-row selection: Ctrl+Click, Shift+Click
- Right-click a cell: copy cell value
- Right-click selected rows: copy as CSV, JSON, or INSERT SQL
- Export button exports the full table (all rows, no page limit) as CSV, JSON, or INSERT SQL to a file

## SQL Editor

- Syntax highlighting via GtkSourceView; respects system dark/light mode automatically
- Line numbers and current-line highlight
- Auto-save with 800 ms debounce; unsaved-changes indicator in toolbar; manual save with `Ctrl+S`
- **Run All** (`F5`) — executes the entire buffer
- **Run Selected** (`Ctrl+Enter`) — executes selected text, or the statement at the cursor
- **Cancel** button — stops a running query mid-execution
- **EXPLAIN** — runs EXPLAIN on the current statement; result shown as a collapsible tree
- **EXPLAIN ANALYZE** — runs EXPLAIN ANALYZE (confirmation dialog warns about side effects); tree annotated with actual row counts and timing; copy plan as text or JSON
- Custom multi-statement parser: splits on semicolons while respecting string literals, dollar-quoting (`$tag$…$tag$`), and comments; DDL statements run in autocommit mode
- Multi-statement log: each statement's outcome listed (row count, execution time, error detail/hint); SELECT results open as additional closeable tabs
- Query history — last 50 executed statements stored with timestamp and duration; click an entry to restore it to the editor
- Toggle line comment with `Ctrl+/` (adds or removes `--` prefix)
- Right-click results: copy cell, copy selected rows as CSV or JSON, copy all rows as CSV or JSON

## File Explorer

- Filesystem sidebar; current path shown in the toolbar
- Up and Home buttons for quick navigation; double-click a folder to enter it
- Shows folders and `.sql` files only; hidden files not shown
- Create new folders and `.sql` files inline; new files open automatically in the editor
- Double-click a `.sql` file to open it in the editor
- Right-click to rename or delete files and folders; deleting a file closes its open editor tab
- Remembers last visited folder across sessions

## Appearance

- Font preferences: family (system default, sans-serif, serif, monospace) and size (8–20 pt in 1 pt increments), configured independently for sidebar and main content
- Font settings previewed in real time; persisted to `~/.config/tusk/prefs.json`
- GTK4 + libadwaita; follows system dark/light mode automatically including syntax highlighting theme
- Resizable panes (sidebar, results panel) with persisted widths

## Keyboard Shortcuts

| Action | Shortcut |
|---|---|
| Preferences | Ctrl+, |
| Quit | Ctrl+Q |
| Close Tab | Ctrl+W |
| Next Tab | Ctrl+Tab |
| Previous Tab | Ctrl+Shift+Tab |
| Go to Tab N | Alt+1–9 |
| Refresh Tab | Ctrl+R |
| Schema filter | Ctrl+F |
| Run All | F5 |
| Run Selected | Ctrl+Enter |
| Save file | Ctrl+S |
| Toggle line comment | Ctrl+/ |
| Keyboard shortcuts reference | Ctrl+? |
