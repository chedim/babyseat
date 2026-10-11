#!/bin/sh
# Couchbase Lite C 3.2.4 Enterprise + its vector search extension 1.0.1 ->
# vendor/cbl/{macos,android}/ (not in git). Checksums from packages.couchbase.com.
# usage: scripts/fetch-cbl.sh [macos|android|linux-x86_64|linux-arm64|all]
# (all = this machine's platform + android, the seat's phone)
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
V=3.2.4 VS=1.0.1
R=https://packages.couchbase.com/releases
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
get() {  # url -> $T/<name>, verified against the published .sha256
  f="$T/$(basename "$1")"
  curl -sfL -o "$f" "$1"
  want="$(curl -sfL "$1.sha256" | awk '{print $1}')"
  have="$(shasum -a 256 "$f" 2>/dev/null | awk '{print $1}' || sha256sum "$f" | awk '{print $1}')"
  if [ -n "$want" ] && [ "$want" != "$have" ]; then echo "checksum mismatch: $1" >&2; exit 1; fi
  echo "$f"
}
want="${1:-all}"
case "$(uname -s)-$(uname -m)" in
  Darwin-*) here=macos ;; Linux-x86_64) here=linux-x86_64 ;; Linux-aarch64|Linux-arm64) here=linux-arm64 ;; *) here=none ;;
esac
if [ "$want" = all ] && [ "$here" != macos ]; then want_local="$here"; fi
if [ "$want" = all ] || [ "$want" = macos ]; then
  D="$HERE/vendor/cbl/macos"; rm -rf "$D"; mkdir -p "$D"
  z="$(get $R/couchbase-lite-c/$V/couchbase-lite-c-enterprise-$V-macos.zip)"; unzip -q "$z" -d "$T/m"
  cp "$T/m/libcblite-$V/lib/libcblite.$V.dylib" "$D/libcblite.dylib"
  z="$(get $R/couchbase-lite-vector-search/$VS/couchbase-lite-vector-search_xcframework_$VS.zip)"; unzip -q "$z" -d "$T/mv"
  cp -R "$T/mv/CouchbaseLiteVectorSearch.xcframework/macos-arm64_x86_64/CouchbaseLiteVectorSearch.framework" "$D/"
  echo "macos -> $D"
fi
if [ "$want" = all ] || [ "$want" = android ]; then
  D="$HERE/vendor/cbl/android"; rm -rf "$D"; mkdir -p "$D"
  z="$(get $R/couchbase-lite-c/$V/couchbase-lite-c-enterprise-$V-android.zip)"; unzip -q "$z" -d "$T/a"
  cp "$T/a/libcblite-$V/lib/aarch64-linux-android/libcblite.so" "$D/"
  z="$(get $R/couchbase-lite-vector-search/$VS/couchbase-lite-vector-search-$VS-android-arm64-v8a.zip)"; unzip -q "$z" -d "$T/av"
  cp "$T/av/lib/libCouchbaseLiteVectorSearch.so" "$D/"
  echo "android -> $D"
fi
for L in linux-x86_64 linux-arm64; do
  if [ "$want" = "$L" ] || [ "${want_local:-}" = "$L" ]; then
    D="$HERE/vendor/cbl/$L"; rm -rf "$D"; mkdir -p "$D"
    z="$(get $R/couchbase-lite-c/$V/couchbase-lite-c-enterprise-$V-$L.tar.gz)"; mkdir -p "$T/$L"; tar xzf "$z" -C "$T/$L"
    cp -P "$T/$L/libcblite-$V"/lib/*-linux-gnu/libcblite.so* "$D/"
    z="$(get $R/couchbase-lite-vector-search/$VS/couchbase-lite-vector-search-$VS-$L.zip)"; unzip -q "$z" -d "$T/${L}v"
    cp -P "$T/${L}v"/lib/*.so* "$D/"
    echo "$L -> $D"
  fi
done
