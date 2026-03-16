<p align="center">
  <img src="data/icons/hicolor/scalable/apps/xyz.shapemachine.tusk-gnome.svg" width="128" height="128" alt="Tusk icon"/>
</p>

<h1 align="center">Tusk</h1>
<p align="center">A minimal, clean PostgreSQL client for GNOME.</p>

---

Tusk aims to be the best native PostgreSQL GUI on the GNOME desktop — fast, focused, and out of the way.

**Features**

- Connection manager with GNOME Keyring password storage
- SSH tunnel support (key-based auth)
- Database browser — schemas, tables, views
- Table inspector — Schema, Keys, Relations, Triggers, Indexes, DDL, Data tabs
- SQL editor with syntax highlighting, auto-save, and inline results
- File explorer sidebar for `.sql` files
- Follows GNOME conventions — dark mode, keyboard shortcuts, preferences

## Dev setup

**Requirements:** Python 3.11+, GTK4, libadwaita ≥ 1.4

```bash
# System deps (Debian/Ubuntu)
sudo apt install python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
make apt-deps          # GtkSourceView (syntax highlighting)

# Python deps + run
make deps
make run
```

**Other targets**

| Command          | Description                        |
|------------------|------------------------------------|
| `make run`       | Run from source (no install)       |
| `make install`   | Build with Meson and install       |
| `make uninstall` | Uninstall                          |
| `make clean`     | Remove build artifacts             |
| `make lint`      | Lint with ruff                     |
| `make format`    | Format with ruff                   |
