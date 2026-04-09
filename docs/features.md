# Features
_Generated from source: v2026.04.07-00_

## Connections

- Named connection profiles stored locally (`~/.config/tusk/connections.json`)
- Passwords and SSH passphrases stored in system keyring (GNOME Keyring or compatible); warning shown if keyring is unavailable
- SSH tunnel: host, port, user, private key file (file picker), optional passphrase
- Read-only mode — prevents accidental writes; enforced at session level
- Default schema field — sets `search_path` on connect and expands that schema in the browser
- PostgreSQL URI import: paste URI to auto-fill all fields; copy connection as URI to clipboard
- Import connections from a `.pgpass` file; export a connection to `.pgpass` format
- Edit, duplicate, and delete connections
- Active connection highlighted with an accent bar indicator in the sidebar
- Superuser role badge shown when the connected role has superuser privileges
- Disconnect option in the connection row context menu

## Schema Browser

- Tree sidebar: schemas → tables, views, sequences, enums, functions, and roles
- Roles section with attributes (superuser, createdb, createrole, inherit, replication) and group memberships
- Live filter bar — type to narrow all schema objects by name; tree expands to show matches and restores on clear (Ctrl+F to focus)
- Database switcher in the header; drop database with confirmation
- Right-click a schema: Create Schema, Rename Schema, Drop Schema (CASCADE option)
- Right-click a table: Rename, Clone (with column selection and optional data copy), Truncate (with RESTART IDENTITY option), Drop (CASCADE option)
- Right-click a view: Drop (CASCADE option)
- Pin tables and views as favourites (shown in a Favourites section at the top)
- Function browser — click a function to view its definition
- Activity panel shortcut in the sidebar
- Command palette (Ctrl+P): jump to any table, view, or function by name (max 100 results)
- Spinner shown while connecting; switching connections closes table tabs from the previous connection

## Table Inspector

- Eight tabs per table: Schema, Keys, Relations, Triggers, Indexes, DDL, Definition (views only), Data
- **Schema** — column name, type, length, nullable, default value; right-click to Rename, Change Type, Set Default, Toggle Nullable, Set as Primary Key, or Drop; toolbar `+` to add a column; per-column statistics (count, nulls, distinct, min/max, top 5 values)
- **Keys** — constraint name, type (PRIMARY KEY / UNIQUE / CHECK / FOREIGN KEY), associated columns; Add Constraint button; right-click to drop
- **Relations** — foreign key constraints with column mappings, referenced table and column, ON UPDATE/DELETE actions
- **Triggers** — name, event, timing, orientation, and statement text
- **Indexes** — name and full `CREATE INDEX` definition; Add Index button with name, type (B-tree, Hash, GiST, GIN, BRIN), column selection, UNIQUE flag, and CREATE CONCURRENTLY option; right-click to drop
- **DDL** — full `CREATE TABLE` statement (read-only); copy DDL button
- **Definition** — view definition SQL (views only; read-only)
- **Data** — paginated data browser with inline insert, edit, and delete (see Data Browser section)
- All tabs lazy-load on first access; Ctrl+R refreshes all tabs
- Row count estimate and total table size shown in a status bar (tables only); Exact Count button for precise row count

## Data Browser

- Configurable pagination: 100, 500, or 1 000 rows per page; Prev/Next navigation with current range shown; page size persisted
- Client-side filter bar — type to instantly narrow visible rows by any cell value (case-insensitive)
- Sortable columns — click header for ascending/descending; NULLs sort first
- Pinned/frozen columns — right-click a column header to pin/unpin; pinned columns stay fixed on the left during horizontal scroll
- NULL values displayed with a distinct greyed "NULL" label
- Insert new rows via modal form with type-aware inputs, required-field markers, database-default hints, and boolean toggle support (tables with primary key only)
- Edit existing rows via modal form; modified-field indicators; primary key fields locked (tables with primary key only)
- Delete selected rows with confirmation and Undo toast (5 second timeout); navigates back a page if the current page becomes empty (tables with primary key only)
- Multi-row selection: Ctrl+Click, Shift+Click
- Right-click a cell: copy cell value
- Right-click selected rows: copy as CSV, JSON, or INSERT SQL
- Export button exports the full table (all rows, no page limit) as CSV, JSON, or INSERT SQL to a file

## SQL Editor

- Syntax highlighting via GtkSourceView; respects system dark/light mode automatically
- Line numbers and current-line highlight
- Auto-save with 2 second debounce; unsaved-changes indicator in toolbar; manual save with Ctrl+S
- Run All (F5) — executes the entire buffer
- Run Selected (Ctrl+Return) — executes selected text, or the statement at the cursor
- Cancel button — stops a running query mid-execution
- EXPLAIN — runs EXPLAIN on the current statement; result shown as a collapsible tree
- EXPLAIN ANALYZE — runs EXPLAIN ANALYZE (confirmation dialog warns about side effects); tree annotated with actual row counts and timing; copy plan as text or JSON
- Custom multi-statement parser: splits on semicolons while respecting string literals, dollar-quoting, and comments; DDL statements run in autocommit mode
- Multi-statement log: each statement's outcome listed (row count, execution time, error detail); SELECT results open as additional closeable tabs
- Query history — last 50 executed statements stored with timestamp and duration; click an entry to restore it to the editor
- Toggle line comment with Ctrl+/ (adds or removes `--` prefix)
- Configurable notification threshold for long-running queries (seconds; set to 0 to disable)
- Command palette (Ctrl+P) for quick navigation to any table, view, or function

## File Explorer

- Filesystem sidebar; current path shown in the toolbar
- Up and Home buttons for quick navigation; double-click a folder to enter it; Backspace to go up
- Shows folders and `.sql` files only; hidden files not shown
- Create new folders and `.sql` files inline; new files open automatically in the editor
- Right-click to rename or delete files and folders; deleting a file closes its open editor tab
- Remembers last visited folder across sessions

## Appearance

- Font preferences: family (system default, sans-serif, serif, monospace) and size (8–20 pt), configured independently for sidebar and main content
- GTK4 + libadwaita; follows system dark/light mode automatically including syntax highlighting theme

## Keyboard Shortcuts

| Action | Shortcut |
|---|---|
| Preferences | Ctrl+, |
| Quit | Ctrl+Q |
| Quick Open | Ctrl+P |
| New SQL File | Ctrl+N |
| New Folder | Ctrl+Shift+N |
| Close Tab | Ctrl+W |
| Next Tab | Ctrl+Tab |
| Previous Tab | Ctrl+Shift+Tab |
| Go to Tab 1–9 | Alt+1–9 |
| Refresh Tab | Ctrl+R |
| Schema Filter | Ctrl+F |
| Keyboard Shortcuts | Ctrl+? |
| Run All (SQL Editor) | F5 |
| Run Selected (SQL Editor) | Ctrl+Return |
| Toggle Line Comment (SQL Editor) | Ctrl+/ |
| Save File (SQL Editor) | Ctrl+S |
