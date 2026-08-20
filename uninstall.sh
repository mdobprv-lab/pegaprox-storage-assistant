#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ID="storage-assistant"
PEGAPROX_DIR="${PEGAPROX_DIR:-/opt/PegaProx}"
DEST="$PEGAPROX_DIR/plugins/$PLUGIN_ID"
DATA="$PEGAPROX_DIR/config/$PLUGIN_ID"

case "$DEST" in
  "$PEGAPROX_DIR"/plugins/storage-assistant) ;;
  *) echo "Refusing unexpected destination: $DEST" >&2; exit 1 ;;
esac

read -r -p "Remove the Storage Assistant plugin code from $DEST? [y/N] " answer
[[ "${answer:-N}" == "y" ]] || { echo "Aborted."; exit 0; }
rm -rf -- "$DEST"

if [[ "${PURGE_DATA:-0}" == "1" ]]; then
  case "$DATA" in
    "$PEGAPROX_DIR"/config/storage-assistant) rm -rf -- "$DATA" ;;
    *) echo "Refusing unexpected data path: $DATA" >&2; exit 1 ;;
  esac
  echo "Plugin code and saved resource definitions removed."
else
  echo "Plugin code removed. Saved definitions remain in $DATA."
fi
