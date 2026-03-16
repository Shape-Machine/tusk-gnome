Run the release script for the current project with the given version.

Usage: /release <version> [options]

Steps:
1. Confirm the version with the user before proceeding
2. Run `./scripts/release.sh $ARGUMENTS` from the project root
3. Update the three download badge URLs in README.md to point to the new version's artifacts:
   - AppImage: `Tusk-{version}-x86_64.AppImage`
   - .deb: `tusk-gnome-{version}.deb`
   - .rpm: `tusk-gnome-{version}.rpm`
   Use sed or Edit to replace the previous version string in the badge href and img alt attributes.
4. Commit the README change with message "Update download links for v{version}" and push
5. Report which artifacts were created and their sizes
6. If --skip-github was not passed, confirm the GitHub release URL with a note that release notes were auto-generated from git log since the previous tag

If no version is provided, ask the user for one before proceeding.
