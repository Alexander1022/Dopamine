#!/bin/bash
# Tests, builds and packages a Dopamine release into dist/: DMG, zip, install.sh, release notes and SHA-256 checksums.
set -euo pipefail
cd "$(dirname "$0")"
VERSION="$(tr -d '[:space:]' < VERSION)"
DIST="dist"
STAGE="build/dmg"

echo "== Tests"
if command -v shellcheck >/dev/null; then shellcheck install.sh; else echo "warning: shellcheck not found; skipped install.sh check" >&2; fi
/usr/bin/python3 -m unittest discover -s tests
/usr/bin/python3 tests/verify_mcp_evaluation.py
if command -v node >/dev/null; then
  node tests/test_curl.js >/dev/null && node tests/test_intercept_ui.js >/dev/null && echo "UI checks: ok"
else
  echo "warning: node not found; skipped cURL and Intercept UI checks" >&2
fi

echo "== Build"
./build.sh
for file in engine/server.py engine/dopamine_mcp.py engine/tls_ca.py engine/project.py engine/rewrite.py ui/index.html ui/curl-import.js ui/mcp-settings.js ui/tls-settings.js ui/wire-view.js ui/panel-resize.js ui/guide.js ui/project-tools.js ui/design.css ui/splash.html; do
  cmp -s "$file" "Dopamine.app/Contents/Resources/$(basename "$file")" || { echo "Bundled $file differs from source" >&2; exit 1; }
done
[ "$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' Dopamine.app/Contents/Info.plist)" = "$VERSION" ]

echo "== Package"
rm -rf "$DIST" "$STAGE"
mkdir -p "$DIST" "$STAGE"
ditto Dopamine.app "$STAGE/Dopamine.app"
ln -s /Applications "$STAGE/Applications"
ditto -c -k --sequesterRsrc --keepParent Dopamine.app "$DIST/Dopamine-$VERSION-macOS.zip"
hdiutil create -quiet -volname "Dopamine $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DIST/Dopamine-$VERSION.dmg"
hdiutil verify -quiet "$DIST/Dopamine-$VERSION.dmg"
awk -v v="$VERSION" '$0 ~ "^## "v" " {on=1} on && /^## / && $0 !~ "^## "v" " {exit} on' CHANGELOG.md > "$DIST/RELEASE_NOTES-$VERSION.md"
sed "s/@VERSION@/$VERSION/" install.sh > "$DIST/install.sh"
grep -q "VERSION=\"$VERSION\"" "$DIST/install.sh"
(cd "$DIST" && shasum -a 256 "Dopamine-$VERSION.dmg" "Dopamine-$VERSION-macOS.zip" install.sh > SHA256SUMS.txt)
rm -rf "$STAGE"
echo "== Release $VERSION"
ls -lh "$DIST"
cat "$DIST/SHA256SUMS.txt"
