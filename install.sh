#!/bin/sh
# Installs Dopamine from its GitHub release. release.sh fills in the version and attaches this script to the release.
#
#   curl -fsSL https://github.com/Alexander1022/Dopamine/releases/latest/download/install.sh | sh
#   sh install.sh --uninstall
#
# It checks the zip against the release's SHA256SUMS.txt, then copies Dopamine.app to /Applications, or to
# ~/Applications when /Applications is not writable. It never asks for a password and never touches your
# projects, settings or certificate authority. The checksum file comes from the same release as the zip, so it
# catches a damaged download, not a tampered release.
set -eu

VERSION="@VERSION@"
REPO="Alexander1022/Dopamine"
BASE="https://github.com/$REPO/releases/download/v$VERSION"
ZIP="Dopamine-$VERSION-macOS.zip"
DATA="$HOME/Library/Application Support/Dopamine"

say() { printf '%s\n' "$*"; }
fail() { printf 'Dopamine installer: %s\n' "$*" >&2; exit 1; }

installed_app() {
  for dir in /Applications "$HOME/Applications"; do
    if [ -d "$dir/Dopamine.app" ]; then
      printf '%s\n' "$dir/Dopamine.app"
      return 0
    fi
  done
  return 1
}

quit_first() {
  if pgrep -x Dopamine >/dev/null 2>&1; then
    fail "Dopamine is running. Quit it (Dopamine → Quit Dopamine), then run this again."
  fi
}

# Reads an answer from the terminal even when the script itself arrives on stdin through a pipe.
ask() {
  printf '%s [y/N] ' "$1"
  answer=""
  if [ -r /dev/tty ]; then read -r answer < /dev/tty || answer=""; fi
  case "$answer" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

uninstall() {
  quit_first
  if app=$(installed_app); then
    rm -rf "$app"
    say "Removed $app."
  else
    say "Dopamine.app is not in /Applications or ~/Applications."
  fi
  say ""
  say "Your Dopamine certificate may still be trusted. Remove it in Keychain Access (search for 'Dopamine CA'),"
  say "or find and delete it from a terminal:"
  say "  security find-certificate -a -c 'Dopamine CA' -Z ~/Library/Keychains/login.keychain-db | grep SHA-1"
  say "  security delete-certificate -Z <SHA-1 from above> ~/Library/Keychains/login.keychain-db"
  say ""
  if [ -d "$DATA" ]; then
    if ask "Also delete settings and the certificate authority in $DATA?"; then
      rm -rf "$DATA"
      say "Deleted $DATA."
    else
      say "Kept $DATA."
    fi
  fi
  say "Projects in ~/Documents/Dopamine Projects were not touched."
}

install() {
  [ "$(uname -s)" = Darwin ] || fail "Dopamine runs on macOS only."
  major=$(sw_vers -productVersion | cut -d. -f1)
  [ "$major" -ge 13 ] || fail "Dopamine needs macOS 13 or later; this Mac has $(sw_vers -productVersion)."
  if ! xcode-select -p >/dev/null 2>&1 || ! /usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    fail "Dopamine needs Python 3.9+ from the Xcode Command Line Tools. Install them with: xcode-select --install"
  fi
  quit_first

  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT INT TERM
  say "Downloading Dopamine $VERSION…"
  curl -fsSL --proto '=https' --tlsv1.2 -o "$tmp/$ZIP" "$BASE/$ZIP" || fail "Could not download $BASE/$ZIP"
  curl -fsSL --proto '=https' --tlsv1.2 -o "$tmp/SHA256SUMS.txt" "$BASE/SHA256SUMS.txt" || fail "Could not download SHA256SUMS.txt"
  grep "  $ZIP\$" "$tmp/SHA256SUMS.txt" > "$tmp/expected" || fail "SHA256SUMS.txt has no entry for $ZIP"
  (cd "$tmp" && shasum -a 256 -c expected >/dev/null) || fail "Checksum mismatch for $ZIP. Nothing was installed."
  say "Checksum OK."

  ditto -x -k "$tmp/$ZIP" "$tmp/unpacked"
  [ -d "$tmp/unpacked/Dopamine.app" ] || fail "The download does not contain Dopamine.app"

  if [ -w /Applications ]; then
    dest=/Applications
  else
    dest="$HOME/Applications"
    mkdir -p "$dest"
    say "/Applications is not writable for this user; installing to $dest instead."
  fi
  rm -rf "$dest/Dopamine.app"
  ditto "$tmp/unpacked/Dopamine.app" "$dest/Dopamine.app"
  say "Installed Dopamine $VERSION in $dest."
  say "Open it with: open \"$dest/Dopamine.app\""
}

case "${1:-}" in
  "") install ;;
  --uninstall) uninstall ;;
  -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) fail "Unknown option $1. Use --uninstall, or no option to install." ;;
esac
