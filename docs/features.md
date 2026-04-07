# Features
_Generated from source: v2026.04.06-00_

## Connections

- Named connection profiles stored locally (`~/.config/tusk/connections.json`)
- Passwords and SSH passphrases stored in system keyring; warning shown if keyring is unavailable
- SSH tunnel: host, port, user, private key file (file picker), optional passphrase
- Test connection before saving
- Read-only mode — prevents accidental writes; enforced at session level
- Default schema setting — sets `search_path` on connect and expands that schema in the browser
- PostgreSQL URI import: paste URI to auto-fill all fields; copy connection as URI to clipboard
- Import connections from `.pgpass` file
- Edit, duplicate, and delete connections
- Database switcher with drop database option
- Superuser role badge on the connection row

## Schema Browser

- Tree sidebar: schemas → tables, views, sequences, enums, functions, and roles
- Live filter bar — type to narrow by name; tree expands to show matches and restores on clear
- Create, rename, and drop schemas
- Create, rename, drop, truncate, and clone tables
- Create views
- Pin tables and views as favourites (shown in a Favourites section at the top)
- Role browser — view and open role detail panels
- Function browser — click a function to view its definition
- Activity panel shortcut in the sidebar

## Table Inspector

- Eight tabs per table: Schema, Keys, Relations, Triggers, Indexes, DDL, Definition (views only), Data
- **Schema** — column name, type, length, nullable, default value
- **Keys** — constraint name, type (PRIMARY KEY / UNIQUE / FOREIGN KEY), column list
- **Relations** — foreign key constraints with column, referenced table and column, ON UPDATE/DELETE actions
- **Triggers** — name, event, timing, orientation, statement
- **Indexes** — name, full `CREATE INDEX` definition; create index with type and CONCURRENTLY option
- **DDL** — full `CREATE TABLE` statement (read-only)
- **Definition** — view definition SQL (views only)
- **Data** — paginated data browser with inline insert, edit, and delete (see Data Browser section)
- Ctrl+R refreshes all tabs; row count estimate and total size shown in status bar (tables only)

## Data Browser

- Paginated data grid (page size: 100 / 500 / 1 000 rows); Previous / Next navigation with current range shown
- Column text filter — type to instantly narrow visible rows
- Sortable columns — click header for ascending / descending; NULLs sort first
- NULL values displayed with a distinct greyed "NULL" label
- Insert new rows via modal form with type-aware inputs, required-field markers, and database-default hints; boolean toggle support (tables with primary key only)
- Edit existing rows via modal form; modified-field indicators; primary key fields locked (tables with primary key only)
- Delete selected rows with confirmation (tables with primary key only)
- Right-click a cell to copy its value
- Right-click selected rows to copy as CSV, JSON, or INSERT SQL
- Export full table (all rows) as CSV, JSON, or INSERT SQL
- Pinned/frozen columns

## SQL Editor

- SQL syntax highlighting via GtkSourceView; respects system dark/light mode
- Line numbers displayed
- Auto-save with 800 ms debounce; unsaved-changes indicator; manual save with Ctrl+S
- Run All (F5) — executes entire buffer
- Run Selected (Ctrl+Return) — executes selected text or statement at cursor
- Cancel running query
- Custom multi-statement parser: splits on semicolons while respecting string literals, dollar-quoting, and comments
- Multi-statement results: log lists each statement's outcome; SELECT results open as closeable tabs
- Toggle line comment (Ctrl+/)
- Query history (last 50 entries)
- Optional SQL formatter (via sqlparse)
- Configurable notification threshold for long-running queries (seconds; set to 0 to disable)
- Command palette (Ctrl+P) for quick navigation

## File Explorer

- Filesystem sidebar; current path shown in toolbar
- Up and Home buttons; double-click a folder or press Return to enter it; Backspace to go up
- Shows folders and `.sql` files only
- Create new folders and `.sql` files inline; new files open automatically in the editor
- Right-click to rename or delete files and folders; deleting a file closes its open editor tab
- Remembers last visited folder across sessions

## Appearance

- Font family picker (system default / sans-serif / serif / monospace) — separate for sidebar and content
- Font size slider (8–20 pt) — separate for sidebar and content
- GTK4 + libadwaita; follows system dark/light mode automatically

## Keyboard Shortcuts

| Action | Shortcut |
|---|---|
| Preferences | Ctrl+, |
| Quit | Ctrl+Q |
| Quick Open | Ctrl+P |
| Close Tab | Ctrl+W |
| Next Tab | Ctrl+Tab |
| Previous Tab | Ctrl+Shift+Tab |
| Go to Tab 1–9 | Alt+1–9 |
| Refresh Tab | Ctrl+R |
| Keyboard Shortcuts | Ctrl+? |
| Run All (SQL Editor) | F5 |
| Run Selected (SQL Editor) | Ctrl+Return |
| Toggle Line Comment (SQL Editor) | Ctrl+/ |
| Save File (SQL Editor) | Ctrl+S |
