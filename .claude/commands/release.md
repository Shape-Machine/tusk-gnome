Run the release script for the current project with the given version.

Usage: /release [version] [options]

Steps:
1. Determine the version:
   - If a version argument was provided, use it directly.
   - If no version was provided, run `gh release list --limit 1 --json tagName --jq '.[0].tagName'` to get the latest release tag (e.g. `v2026.03.17-00`). Strip the leading `v`. The version format is `YYYY.MM.DD-NN` where NN is a two-digit patch counter. Increment NN by 1 (zero-padded to two digits) to get the next patch version. If today's date differs from the tag's date, reset NN to `00` and use today's date instead.
2. Confirm the version with the user before proceeding.
3. Run `PATH="$HOME/bin:$PATH" ./scripts/release.sh {version}` from the project root — run ALL package types including Flatpak, never pass --skip-flatpak
4. Update the four download badge URLs in README.md to point to the new version's artifacts:
   - Flatpak: `xyz.shapemachine.tusk-gnome-{version}.flatpak`
   - AppImage: `Tusk-{version}-x86_64.AppImage`
   - .deb: `tusk-gnome-{version}.deb`
   - .rpm: `tusk-gnome-{version}.rpm`
   Use sed or Edit to replace the previous version string in the badge href and img alt attributes.
5. Commit the README change with message "Update download links for v{version}" and push
6. Report which artifacts were created and their sizes
7. If --skip-github was not passed, confirm the GitHub release URL with a note that release notes were auto-generated from git log since the previous tag
