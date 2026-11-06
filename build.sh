#!/bin/bash
# Builds Dopamine.app (universal: Apple Silicon and Intel) from the sources in this folder.
set -euo pipefail
cd "$(dirname "$0")"
VERSION="$(tr -d '[:space:]' < VERSION)"
BUILD_NUMBER="${BUILD_NUMBER:-1}"
APP="Dopamine.app"
WORK="build"
RESOURCES=(assets/Dopamine.icns assets/startup.m4a engine/server.py engine/dopamine_mcp.py engine/tls_ca.py engine/project.py engine/rewrite.py ui/index.html ui/curl-import.js ui/mcp-settings.js ui/tls-settings.js ui/wire-view.js ui/panel-resize.js ui/guide.js ui/project-tools.js ui/design.css ui/splash.html)

grep -q "VERSION = '$VERSION'" engine/dopamine_mcp.py || { echo "engine/dopamine_mcp.py VERSION does not match $VERSION" >&2; exit 1; }
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$WORK/module-cache"
for arch in arm64 x86_64; do
  swiftc -O -parse-as-library macos/Desktop.swift macos/NativeMenus.swift macos/Projects.swift -o "$WORK/Dopamine-$arch" \
    -framework AppKit -framework WebKit -module-cache-path "$WORK/module-cache" -target "$arch-apple-macosx13.0"
done
lipo -create "$WORK/Dopamine-arm64" "$WORK/Dopamine-x86_64" -output "$APP/Contents/MacOS/Dopamine"
cp "${RESOURCES[@]}" "$APP/Contents/Resources/"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Dopamine</string>
<key>CFBundleDisplayName</key><string>Dopamine</string>
<!-- Kept from earlier builds so saved Repeater tabs and appearance carry over. -->
<key>CFBundleIdentifier</key><string>io.github.alexander1022.dopamine</string>
<key>CFBundleVersion</key><string>$BUILD_NUMBER</string>
<key>CFBundleShortVersionString</key><string>$VERSION</string>
<key>CFBundleExecutable</key><string>Dopamine</string>
<key>CFBundleIconFile</key><string>Dopamine.icns</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
<key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>NSHumanReadableCopyright</key><string>Dopamine $VERSION</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
<key>CFBundleDocumentTypes</key><array><dict>
  <key>CFBundleTypeName</key><string>Dopamine Project</string>
  <key>CFBundleTypeRole</key><string>Editor</string>
  <key>LSHandlerRank</key><string>Owner</string>
  <key>LSItemContentTypes</key><array><string>io.github.alexander1022.dopamine.project</string></array>
</dict></array>
<key>UTExportedTypeDeclarations</key><array><dict>
  <key>UTTypeIdentifier</key><string>io.github.alexander1022.dopamine.project</string>
  <key>UTTypeDescription</key><string>Dopamine Project</string>
  <key>UTTypeConformsTo</key><array><string>public.data</string></array>
  <key>UTTypeTagSpecification</key><dict><key>public.filename-extension</key><array><string>dopamine</string></array></dict>
</dict></array>
</dict></plist>
PLIST
codesign --force --sign - "$APP"
codesign --verify --strict "$APP"
echo "Built: $PWD/$APP ($VERSION, $(lipo -archs "$APP/Contents/MacOS/Dopamine"))"
