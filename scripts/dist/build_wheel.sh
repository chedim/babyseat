#!/bin/sh
# Build the platform wheel of babyseat from a dist folder (docs/packaging-plan.md, the wrapper). CI calls it once per platform.
#   scripts/dist/build_wheel.sh <platform tag> (--dist <dist folder> | --payload <payload dir>) [--out <dir>]
#   <platform tag>   macosx_11_0_arm64 | macosx_10_9_x86_64 | manylinux_2_28_x86_64 | manylinux_2_28_aarch64 | musllinux_1_2_x86_64 | musllinux_1_2_aarch64 | ...  (the wheel is py3-none-<tag>)
#   --dist           a dist folder (scripts/dist/build.sh made it): verified against its MANIFEST.sha256 and packed with assemble_wheel_payload.sh
#   --payload        an already assembled payload dir (<payload>/dist/...): what release-binaries.yml uploads as `wheel-payload-<target>`
#   --out            where the wheel goes (default <repo>/dist-wheels)
#   env PYTHON       a python that has `build` and `setuptools` (default python3)
# The wheel is built in a throwaway copy of the wrapper's files (pyproject.toml setup.py README.md LICENSE membrane/{__init__,launcher,__main__}.py), so the checkout stays clean.
# The payload's VERSION must be the package's version; the finished wheel is checked (tag, the executable bit, VERSION, both console scripts) before it is reported.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
tag=${1:?usage: scripts/dist/build_wheel.sh <platform tag> (--dist <dist folder> | --payload <payload dir>) [--out <dir>]}
shift
dist=''; payload=''; out=$root/dist-wheels
while [ $# -gt 0 ]; do
  case "$1" in
    --dist) dist=${2:?--dist needs a folder}; shift 2 ;;
    --payload) payload=${2:?--payload needs a folder}; shift 2 ;;
    --out) out=${2:?--out needs a folder}; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "build_wheel: unknown option $1 (--dist --payload --out)" >&2; exit 2 ;;
  esac
done
[ -n "$dist" ] || [ -n "$payload" ] || { echo "build_wheel: name --dist <dist folder> or --payload <payload dir>" >&2; exit 2 ; }
[ -z "$dist" ] || [ -z "$payload" ] || { echo "build_wheel: --dist and --payload are alternatives" >&2; exit 2; }
py=${PYTHON:-python3}
abs() { (cd "$1" && pwd); }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM
if [ -n "$dist" ]; then
  sh "$here/assemble_wheel_payload.sh" "$dist" "$work/payload" >&2
  payload=$work/payload
else
  payload=$(abs "$payload")
  sh "$here/verify.sh" "$payload/dist" >/dev/null || { echo "build_wheel: $payload/dist does not match its MANIFEST.sha256 (run scripts/dist/verify.sh)" >&2; exit 1; }
fi
pkg=$("$py" -c "import tomllib; print(tomllib.load(open('$root/pyproject.toml', 'rb'))['project']['version'])")
have=$(cat "$payload/dist/VERSION")
[ "$have" = "$pkg" ] || { echo "build_wheel: the payload is version $have but the package (pyproject.toml) is $pkg: build the dist folder of this version" >&2; exit 1; }
src=$work/src
mkdir -p "$src/membrane"
cp "$root/pyproject.toml" "$root/setup.py" "$root/README.md" "$root/LICENSE" "$src/"
cp "$root/membrane/__init__.py" "$root/membrane/launcher.py" "$root/membrane/__main__.py" "$src/membrane/"
mkdir -p "$out"
out=$(abs "$out")
BABYSEAT_PAYLOAD=$payload BABYSEAT_PLATFORM_TAG=$tag "$py" -m build --wheel --outdir "$out" "$src" >&2
whl="$out/babyseat-$pkg-py3-none-$tag.whl"
[ -f "$whl" ] || { echo "build_wheel: expected $whl" >&2; exit 1; }
"$py" - "$whl" "$pkg" <<'PY'
import stat, sys, zipfile
w, ver = sys.argv[1:]
z = zipfile.ZipFile(w)
n = set(z.namelist())
for f in ("membrane/dist/bin/cliffvm", "membrane/dist/VERSION", "membrane/dist/MANIFEST.sha256", "membrane/launcher.py"):
    assert f in n, f"{f} is not in the wheel"
assert z.getinfo("membrane/dist/bin/cliffvm").external_attr >> 16 & stat.S_IXUSR, "bin/cliffvm lost its executable bit"
assert z.read("membrane/dist/VERSION").decode().strip() == ver
ep = z.read(f"babyseat-{ver}.dist-info/entry_points.txt").decode()
assert "babyseat = membrane.launcher:main" in ep and "membrane = membrane.launcher:main" in ep
PY
echo "$whl"
