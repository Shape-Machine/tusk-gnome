#!/usr/bin/env bash
# Usage: ./scripts/release.sh <version> [--skip-flatpak] [--skip-appimage] [--skip-deb] [--skip-rpm] [--skip-pacman] [--skip-aur] [--skip-github]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Use venv tools (pip, meson/ninja) if available
[[ -d "$ROOT/.venv/bin" ]] && export PATH="$ROOT/.venv/bin:$PATH"

# ── Args ──────────────────────────────────────────────────────────────────────

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "Usage: $0 <version> [--skip-flatpak] [--skip-appimage] [--skip-deb] [--skip-rpm] [--skip-pacman] [--skip-aur] [--skip-github]"
    exit 1
fi
shift

DO_FLATPAK=1 DO_APPIMAGE=1 DO_DEB=1 DO_RPM=1 DO_PACMAN=1 DO_AUR=1 DO_GITHUB=1
for arg in "$@"; do
    case "$arg" in
        --skip-flatpak)  DO_FLATPAK=0  ;;
        --skip-appimage) DO_APPIMAGE=0 ;;
        --skip-deb)      DO_DEB=0      ;;
        --skip-rpm)      DO_RPM=0      ;;
        --skip-pacman)   DO_PACMAN=0   ;;
        --skip-aur)      DO_AUR=0      ;;
        --skip-github)   DO_GITHUB=0   ;;
    esac
done

APP_ID="xyz.shapemachine.tusk-gnome"
DIST="$ROOT/dist/$VERSION"
mkdir -p "$DIST"

log()  { echo "▶ $*"; }
ok()   { echo "✓ $*"; }
skip() { echo "– $* (skipped)"; }

# ── Check tools ───────────────────────────────────────────────────────────────

check() {
    command -v "$1" &>/dev/null || { echo "✗ missing: $1 — $2"; exit 1; }
}

[[ $DO_FLATPAK  == 1 ]] && check flatpak-builder "sudo apt install flatpak-builder"
[[ $DO_APPIMAGE == 1 ]] && check appimagetool    "download from https://github.com/AppImage/appimagetool/releases"
[[ $DO_DEB      == 1 ]] && check fpm             "sudo gem install fpm"
[[ $DO_RPM      == 1 ]] && check fpm             "sudo gem install fpm"
[[ $DO_PACMAN   == 1 ]] && check fpm             "sudo gem install fpm"
[[ $DO_AUR      == 1 ]] && check makepkg        "install base-devel (Arch only)"
[[ $DO_AUR      == 1 ]] && check git            "sudo apt install git"
[[ $DO_GITHUB   == 1 ]] && check gh              "sudo apt install gh"

# ── 1. Patch version in meson.build ──────────────────────────────────────────

STAGING="$ROOT/_release_staging"
BUILD="$ROOT/_release_build"

log "Staging install tree (prefix=/usr/local, version=$VERSION)"
rm -rf "$BUILD" "$STAGING"

# Generate config.py with the release version
mkdir -p "$STAGING/usr/local/share/tusk-gnome"
sed "s/@VERSION@/$VERSION/g; s/@APP_ID@/xyz.shapemachine.tusk-gnome/g" \
    "$ROOT/src/config.py.in" > "$STAGING/usr/local/share/tusk-gnome/config.py"

# Copy Python sources
for f in "$ROOT"/src/*.py; do
    [[ "$(basename $f)" == "config.py" ]] && continue
    cp "$f" "$STAGING/usr/local/share/tusk-gnome/"
done

# Vendor psycopg (not in most distro repos) into the package
python3 -m pip install --quiet --target="$STAGING/usr/local/share/tusk-gnome/vendor" "psycopg[binary]" "sqlparse"

# Launcher script
mkdir -p "$STAGING/usr/local/bin"
cat > "$STAGING/usr/local/bin/tusk" <<LAUNCHER
#!/bin/bash
export PYTHONPATH="/usr/local/share/tusk-gnome/vendor:/usr/local/share/tusk-gnome:\$PYTHONPATH"
exec python3 /usr/local/share/tusk-gnome/main.py "\$@"
LAUNCHER
chmod +x "$STAGING/usr/local/bin/tusk"

# XDG integration files — must go under /usr/share (not /usr/local/share)
# so appstreamcli, GNOME Software, and update-desktop-database find them
mkdir -p "$STAGING/usr/share/applications"
mkdir -p "$STAGING/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$STAGING/usr/share/glib-2.0/schemas"
mkdir -p "$STAGING/usr/share/metainfo"
cp "$ROOT/data/xyz.shapemachine.tusk-gnome.desktop" \
   "$STAGING/usr/share/applications/"
cp "$ROOT/data/icons/hicolor/scalable/apps/xyz.shapemachine.tusk-gnome.svg" \
   "$STAGING/usr/share/icons/hicolor/scalable/apps/"
cp "$ROOT/data/xyz.shapemachine.tusk-gnome.gschema.xml" \
   "$STAGING/usr/share/glib-2.0/schemas/"
cp "$ROOT/data/xyz.shapemachine.tusk-gnome.metainfo.xml" \
   "$STAGING/usr/share/metainfo/"

ok "Staging complete → $STAGING"

# ── 3. Flatpak ────────────────────────────────────────────────────────────────

if [[ $DO_FLATPAK == 1 ]]; then
    log "Building Flatpak"
    FLATPAK_REPO="$ROOT/_flatpak_repo"
    FLATPAK_BUILD="$ROOT/_flatpak_build"
    # Keep manifest alongside original so relative "path": "../.." resolves correctly
    FLATPAK_MANIFEST="$ROOT/packaging/flatpak/_tmp_$APP_ID.json"
    rm -rf "$FLATPAK_BUILD"

    # Substitute @VERSION@ in manifest
    sed "s/@VERSION@/$VERSION/g" "$ROOT/packaging/flatpak/$APP_ID.json" > "$FLATPAK_MANIFEST"

    flatpak-builder \
        --force-clean \
        --repo="$FLATPAK_REPO" \
        "$FLATPAK_BUILD" \
        "$FLATPAK_MANIFEST"

    flatpak build-bundle \
        "$FLATPAK_REPO" \
        "$DIST/$APP_ID-$VERSION.flatpak" \
        "$APP_ID"

    rm -f "$FLATPAK_MANIFEST"
    ok "Flatpak → $DIST/$APP_ID-$VERSION.flatpak"
else
    skip "Flatpak"
fi

# ── 4. AppImage ───────────────────────────────────────────────────────────────

if [[ $DO_APPIMAGE == 1 ]]; then
    log "Building AppImage"
    APPDIR="$ROOT/_appimage/$APP_ID.AppDir"
    rm -rf "$ROOT/_appimage"
    mkdir -p "$APPDIR"

    # Copy installed files into AppDir (app code from /usr/local, XDG files from /usr/share)
    cp -r "$STAGING/usr/local/"* "$APPDIR/"
    cp -r "$STAGING/usr/share/"* "$APPDIR/share/"

    # AppDir metadata (root-level symlinks required by AppImage spec)
    cp "$STAGING/usr/share/applications/$APP_ID.desktop" "$APPDIR/$APP_ID.desktop"
    cp "$STAGING/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg" "$APPDIR/$APP_ID.svg"

    cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/bin:$PATH"
export PYTHONPATH="$HERE/share/tusk-gnome/vendor:$HERE/share/tusk-gnome:$PYTHONPATH"
export XDG_DATA_DIRS="$HERE/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
exec python3 "$HERE/share/tusk-gnome/main.py" "$@"
APPRUN
    chmod +x "$APPDIR/AppRun"

    ARCH=x86_64 appimagetool "$APPDIR" "$DIST/Tusk-$VERSION-x86_64.AppImage"
    ok "AppImage → $DIST/Tusk-$VERSION-x86_64.AppImage"
else
    skip "AppImage"
fi

# ── 5. .deb ───────────────────────────────────────────────────────────────────

if [[ $DO_DEB == 1 ]]; then
    log "Building .deb"
    fpm \
        -s dir \
        -t deb \
        -n tusk-gnome \
        -v "$VERSION" \
        --description "PostgreSQL client for GNOME" \
        --url "https://shapemachine.xyz/tusk" \
        --maintainer "Shape Machine <tusk.gnome@shapemachine.xyz>" \
        --depends "python3" \
        --depends "python3-gi" \
        --depends "gir1.2-gtk-4.0" \
        --depends "gir1.2-adw-1" \
        --depends "python3-keyring" \
        --depends "python3-paramiko" \
        --package "$DIST/tusk-gnome-$VERSION.deb" \
        -C "$STAGING" \
        usr

    ok ".deb → $DIST/tusk-gnome-$VERSION.deb"
else
    skip ".deb"
fi

# ── 6. .rpm ───────────────────────────────────────────────────────────────────

if [[ $DO_RPM == 1 ]]; then
    log "Building .rpm"
    fpm \
        -s dir \
        -t rpm \
        -n tusk-gnome \
        -v "$VERSION" \
        --description "PostgreSQL client for GNOME" \
        --url "https://shapemachine.xyz/tusk" \
        --maintainer "Shape Machine <tusk.gnome@shapemachine.xyz>" \
        --depends "python3" \
        --depends "python3-gobject" \
        --depends "gtk4" \
        --depends "libadwaita" \
        --package "$DIST/tusk-gnome-$VERSION.rpm" \
        -C "$STAGING" \
        usr

    ok ".rpm → $DIST/tusk-gnome-$VERSION.rpm"
else
    skip ".rpm"
fi

# ── 7. .pkg.tar.zst (Arch / CachyOS) ─────────────────────────────────────────

if [[ $DO_PACMAN == 1 ]]; then
    log "Building Arch package (.pkg.tar.zst)"
    fpm \
        -s dir \
        -t pacman \
        -n tusk-gnome \
        -v "$VERSION" \
        --architecture any \
        --description "PostgreSQL client for GNOME" \
        --url "https://shapemachine.xyz/tusk" \
        --maintainer "Shape Machine <tusk.gnome@shapemachine.xyz>" \
        --depends "python" \
        --depends "python-gobject" \
        --depends "gtk4" \
        --depends "libadwaita" \
        --depends "python-keyring" \
        --depends "gtksourceview5" \
        --depends "python-paramiko" \
        --package "$DIST/tusk-gnome-$VERSION-any.pkg.tar.zst" \
        -C "$STAGING" \
        usr

    ok "Arch package → $DIST/tusk-gnome-$VERSION-any.pkg.tar.zst"
else
    skip "Arch package"
fi

# ── 8. AUR (tusk-gnome-bin) ───────────────────────────────────────────────────

if [[ $DO_AUR == 1 ]]; then
    log "Publishing AUR package (tusk-gnome-bin)"

    PKG_FILE="$DIST/tusk-gnome-$VERSION-any.pkg.tar.zst"
    if [[ ! -f "$PKG_FILE" ]]; then
        echo "✗ AUR publish requires the pacman package — re-run without --skip-pacman"
        exit 1
    fi

    SHA256=$(sha256sum "$PKG_FILE" | awk '{print $1}')

    AUR_TMP=$(mktemp -d)
    trap 'rm -rf "$AUR_TMP"' EXIT

    log "Cloning AUR repo"
    git clone ssh://aur@aur.archlinux.org/tusk-gnome-bin.git "$AUR_TMP"

    # Substitute version and checksum into PKGBUILD template
    # pkgver cannot contain hyphens — replace with dots
    PKGVER="${VERSION//-/.}"
    sed "s/@VERSION@/$VERSION/g; s/@PKGVER@/$PKGVER/g; s/@SHA256SUM@/$SHA256/g" \
        "$ROOT/packaging/aur/PKGBUILD" > "$AUR_TMP/PKGBUILD"

    # Generate .SRCINFO
    (cd "$AUR_TMP" && makepkg --printsrcinfo > .SRCINFO)

    # Commit and push
    git -C "$AUR_TMP" add PKGBUILD .SRCINFO
    git -C "$AUR_TMP" commit -m "Update to v$VERSION"
    git -C "$AUR_TMP" push

    ok "AUR published → https://aur.archlinux.org/packages/tusk-gnome-bin"
else
    skip "AUR"
fi

# ── 9. GitHub release ─────────────────────────────────────────────────────────

if [[ $DO_GITHUB == 1 ]]; then
    log "Publishing GitHub release v$VERSION"
    ASSETS=()
    for f in "$DIST"/*; do
        [[ -f "$f" ]] && ASSETS+=("$f")
    done

    PREV_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
    if [[ -n "$PREV_TAG" ]]; then
        log "Generating changelog since $PREV_TAG"
        NOTES=$(git log "$PREV_TAG..HEAD" --pretty=format:"- %s" --no-merges)
    else
        NOTES=$(git log --pretty=format:"- %s" --no-merges)
    fi

    gh release create "v$VERSION" \
        --title "v$VERSION" \
        --notes "$NOTES" \
        "${ASSETS[@]}"

    ok "GitHub release published → https://github.com/Shape-Machine/tusk-gnome/releases/tag/v$VERSION"
else
    skip "GitHub release"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "────────────────────────────────────────"
echo "  Release $VERSION complete"
echo "  Artifacts in: $DIST"
ls -lh "$DIST"
echo "────────────────────────────────────────"
