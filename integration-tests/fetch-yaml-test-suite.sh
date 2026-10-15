#!/usr/bin/env bash
# Vendors a yaml-test-suite data release into integration-tests/yaml-test-suite/.
#
# The release's tags/ directory is a tree of symlinks. It is flattened into
# tags.txt: one line per case, "<id> <tag> <tag> ...", sorted with LC_ALL=C.
# name/ (more symlinks) is dropped; every case already carries its name in ===.
#
# Usage: integration-tests/fetch-yaml-test-suite.sh [tag]   (default data-2022-01-17)
set -euo pipefail

tag="${1:-data-2022-01-17}"
here="$(cd "$(dirname "$0")" && pwd)"
dest="$here/yaml-test-suite"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

git -c advice.detachedHead=false clone -q --depth 1 --branch "$tag" https://github.com/yaml/yaml-test-suite.git "$tmp/src"
commit="$(git -C "$tmp/src" rev-parse HEAD)"

rm -rf "$dest"
mkdir -p "$dest"
(
  cd "$tmp/src"
  for d in */; do
    d="${d%/}"
    case "$d" in tags | name) continue ;; esac
    cp -R "$d" "$dest/$d"
  done

  # tags/<tag>/<id>[/<sub>] -> "<id>[/<sub>] <tag>"
  find tags -type l | sed 's|^tags/||' |
    awk '{ t = $0; sub(/\/.*/, "", t); id = substr($0, length(t) + 2); print id, t }' |
    LC_ALL=C sort |
    awk '$1 != prev { if (NR > 1) printf "\n"; printf "%s", $1; prev = $1 } { printf " %s", $2 } END { printf "\n" }' \
      >"$dest/tags.txt"
)

printf 'tag: %s\ncommit: %s\nsource: https://github.com/yaml/yaml-test-suite\n' "$tag" "$commit" >"$dest/VERSION"

cases="$(find "$dest" -name in.yaml | wc -l | tr -d ' ')"
echo "Vendored $tag ($commit): $cases cases"
