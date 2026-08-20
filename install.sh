#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ID="storage-assistant"
PEGAPROX_DIR="${PEGAPROX_DIR:-/opt/PegaProx}"
PLUGINS_DIR="$PEGAPROX_DIR/plugins"
DEST="$PLUGINS_DIR/$PLUGIN_ID"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "$PEGAPROX_DIR" || ! -d "$PLUGINS_DIR" ]]; then
  echo "PegaProx was not found under $PEGAPROX_DIR" >&2
  echo "For the official Docker container run: PEGAPROX_DIR=/app ./install.sh" >&2
  exit 1
fi

case "$DEST" in
  "$PEGAPROX_DIR"/plugins/storage-assistant) ;;
  *) echo "Refusing unexpected destination: $DEST" >&2; exit 1 ;;
esac

echo "Installing $PLUGIN_ID into $DEST"
if [[ -d "$DEST" ]]; then
  rm -rf -- "$DEST/src" "$DEST/locales"
fi
mkdir -p "$DEST/src" "$DEST/locales"
cp -f "$SRC/__init__.py" "$SRC/manifest.json" "$DEST/"
if [[ ! -f "$DEST/config.json" ]]; then
  cp -f "$SRC/config.json" "$DEST/config.json"
fi
cp -a "$SRC/src/." "$DEST/src/"
cp -a "$SRC/locales/." "$DEST/locales/"

if [[ -d "$PEGAPROX_DIR/config" ]]; then
  mkdir -p "$PEGAPROX_DIR/config/$PLUGIN_ID"
  chmod 700 "$PEGAPROX_DIR/config/$PLUGIN_ID" 2>/dev/null || true
fi

if command -v systemctl >/dev/null 2>&1 && systemctl cat pegaprox >/dev/null 2>&1; then
  svc_user="$(systemctl show -p User --value pegaprox 2>/dev/null || true)"
  if [[ -n "$svc_user" && "$svc_user" != "root" ]]; then
    svc_group="$(id -gn "$svc_user" 2>/dev/null || echo "$svc_user")"
    chown -R "$svc_user:$svc_group" "$DEST" "$PEGAPROX_DIR/config/$PLUGIN_ID" 2>/dev/null || true
  fi
fi

echo "Installed as plugin ID: $PLUGIN_ID"
echo "Rescan and enable Storage Assistant in PegaProx Settings > Plugins."
echo "The runtime registry is stored under config/$PLUGIN_ID and survives Docker recreation when config/ is mounted."
