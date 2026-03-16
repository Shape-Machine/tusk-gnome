Review the codebase and update `docs/features.md` to reflect the current state of the app.

Usage: /sync-docs

Steps:

1. **Read the current docs** — read `docs/features.md` in full.

2. **Scan the source** — read all files in `src/` to understand what the app currently does:
   - `window.py` — top-level UI structure, keyboard shortcuts, pane layout
   - `connection_dialog.py` — connection form fields, validation, SSH tunnel, keyring
   - `file_explorer.py` — file tree behaviour, signals, context menu actions
   - `sql_editor.py` — editor capabilities, run triggers, autosave, results pane
   - `table_panel.py` — browser tabs and what each tab shows
   - `data_grid.py` — copy/export capabilities (CSV, JSON, INSERT SQL)
   - `prefs.py` — persisted preferences (font, pane positions, last folder, etc.)
   - Any other `.py` files present

3. **Diff docs vs code** — identify:
   - Features present in code but **missing** from docs
   - Features documented but **no longer present** in code (removed/renamed)
   - Descriptions that are **inaccurate or incomplete** compared to the code

4. **Rewrite `docs/features.md`** — produce an updated version that:
   - Keeps the same general structure (sections per subsystem)
   - Uses concise bullet points, present tense, user-facing language
   - Does **not** mention implementation details (class names, file names, GTK internals)
   - Adds a bullet for every distinct user-visible feature found in code
   - Removes bullets for anything no longer in the code
   - Keeps the Table Inspector matrix table format (it's clear and useful)

5. **Commit and push** — stage and commit `docs/features.md` with the message `Update features.md` and push to the current branch. No confirmation needed.

6. **Show a summary** of what changed: bullets added, bullets removed, bullets updated.
