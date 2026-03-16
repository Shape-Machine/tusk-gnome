# Features

## Connections
- Named connection profiles stored locally
- Passwords stored securely in GNOME Keyring
- SSH tunnel support with key-based auth and optional passphrase
- Test connection before saving
- Edit and delete connections

## Database Browser
- Browse schemas, tables, and views in a tree sidebar
- Tables and views grouped separately per schema

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

## SQL Editor
- Syntax highlighting (GtkSourceView, respects dark mode)
- Auto-save with 800ms debounce
- Run query with F5 or Ctrl+Enter
- Runs selected text or full buffer
- Inline results pane (resizable)
- Connected to the active database connection

## File Explorer
- Navigate the filesystem from a sidebar pane
- Shows folders and `.sql` files only
- Create new folders and `.sql` files inline
- Double-click to open `.sql` files in the editor
- Remembers last visited folder across sessions

## GNOME Integration
- GTK4 + libadwaita, follows system dark/light mode
- Font preferences — family (system/sans/serif/mono) and size, separately for sidebar and main content
- Keyboard shortcuts (Ctrl+W, Ctrl+Tab, Alt+1–9, Ctrl+Q, Ctrl+,)
- Resizable panes with persisted positions
