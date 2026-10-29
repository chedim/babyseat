#!/bin/sh
# membrane HA integrations (membrane_ai + the NEW `membrane`) -> agdroid deploy.
# Namespaced staging ONLY: touches
#   - /data/local/tmp/aid_stage/          (staging dir)
#   - <hass-config>/custom_components/membrane_ai/   (ours, new)
#   - <hass-config>/custom_components/membrane/      (ours, the HACS integration; 2026-10-12,
#     the operator: "update my hass membrane to be updatable from the repo ... detached mode")
#   - nothing else: no nginx, no boatserver, no announcer paths, no .storage, no seat data.
# The operator (or a later scripted step) edits configuration.yaml +
# restarts HA once, after the components are staged.
set -e
DEV="${ADB_SERIAL:-192.168.1.159:5565}"
HC="/data/data/com.termux/files/home/hass-config"
STAGE="/data/local/tmp/aid_stage"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

echo "== staging the new membrane integration (manifest patched: requirements [] --"
echo "   connect mode needs no package, and membrane-seat 0.3.0 is not on PyPI yet) =="
STAGE_CC="$(mktemp -d)/membrane"
cp -r "$REPO/custom_components/membrane" "$STAGE_CC"
python3 - "$STAGE_CC/manifest.json" <<'PY'
import json, sys
p = sys.argv[1]
m = json.load(open(p))
m["requirements"] = []
json.dump(m, open(p, "w"), indent=2)
PY

echo "== pushing components to staging =="
adb -s "$DEV" shell "rm -rf $STAGE && mkdir -p $STAGE"
adb -s "$DEV" push "$STAGE_CC" "$STAGE/" | tail -1
adb -s "$DEV" push "$HERE/custom_components/membrane_ai" "$STAGE/" | tail -1

echo "== install into hass-config (namespaced) =="
adb -s "$DEV" shell "su 0 -c '\
  rm -rf $HC/custom_components/membrane_ai && \
  cp -r $STAGE/membrane $HC/custom_components/ && \
  cp -r $STAGE/membrane_ai $HC/custom_components/ && \
  rm -rf $HC/custom_components/membrane_ai/__pycache__ \
         $HC/custom_components/membrane/__pycache__ && \
  ls $HC/custom_components/membrane_ai $HC/custom_components/membrane'"

echo "== verify (the membrane manifest carries no requirements) =="
adb -s "$DEV" shell "su 0 -c 'cat $HC/custom_components/membrane/manifest.json'"
echo "staged. next: the Membrane integration (connect mode) through HA's UI + ONE HA restart."