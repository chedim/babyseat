#!/bin/sh
# membrane HA integrations (ai_terminal + membrane_ai) -> agdroid deploy.
# Namespaced staging ONLY: touches
#   - /data/local/tmp/aid_stage/          (staging dir)
#   - <hass-config>/custom_components/ai_terminal/   (ours, v1+v2)
#   - <hass-config>/custom_components/membrane_ai/   (ours, new)
#   - nothing else: no nginx, no boatserver, no announcer paths.
# The operator (or a later scripted step) edits configuration.yaml +
# restarts HA once, after BOTH components are staged.
set -e
DEV="${ADB_SERIAL:-192.168.1.159:5565}"
HC="/data/data/com.termux/files/home/hass-config"
STAGE="/data/local/tmp/aid_stage"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "== pushing components to staging =="
adb -s "$DEV" shell "rm -rf $STAGE && mkdir -p $STAGE"
adb -s "$DEV" push "$HERE/custom_components/ai_terminal" "$STAGE/" | tail -1
adb -s "$DEV" push "$HERE/custom_components/membrane_ai" "$STAGE/" | tail -1

echo "== install into hass-config (namespaced) =="
adb -s "$DEV" shell "su 0 -c '\
  rm -rf $HC/custom_components/ai_terminal $HC/custom_components/membrane_ai && \
  cp -r $STAGE/ai_terminal $HC/custom_components/ && \
  cp -r $STAGE/membrane_ai $HC/custom_components/ && \
  rm -rf $HC/custom_components/ai_terminal/__pycache__ $HC/custom_components/membrane_ai/__pycache__ && \
  ls $HC/custom_components/ai_terminal $HC/custom_components/membrane_ai'"

echo "== verify (www assets present) =="
adb -s "$DEV" shell "su 0 -c 'ls $HC/custom_components/ai_terminal/www'"
echo "staged. next: config snippet (serve lane creds) + ONE HA restart."
