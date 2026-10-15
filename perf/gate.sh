#!/usr/bin/env bash
# Performance regression gate for scala-yaml.
#
#   perf/gate.sh [--profile gate|full]
#
# Exit 0: pass. Exit 1: regression. Exit 2: infrastructure error.
#
# The baseline commit is perf/baseline.sha. Its core is built in a git worktree
# under /tmp/scala-yaml-perf-worktrees and reused across invocations.
# benchmark/ and build.sbt are copied from this tree first, so both sides run
# the same harness and only core/ differs. build.sbt has to be copied because
# sbt-jmh's Jmh config extends Test, so Jmh/run compiles benchmark tests.
#
# The candidate's project/ and integration-tests/ must match the baseline
# commit. One gate per machine: the lock is /tmp/scala-yaml-perf-gate.lock.

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT="$ROOT/perf/out"
LOCK_DIR=/tmp/scala-yaml-perf-gate.lock
LOCK_HELD=
JDK_VERSION_FILE=
SUITE_LOG=

log() {
  printf '%s\n' "$*" >&2
}

die() {
  log "error: $*"
  exit 2
}

cleanup() {
  if [[ -n "$LOCK_HELD" ]]; then
    rm -rf "$LOCK_DIR"
    LOCK_HELD=
  fi
  if [[ -n "$JDK_VERSION_FILE" ]]; then
    rm -f "$JDK_VERSION_FILE"
  fi
  if [[ -n "$SUITE_LOG" ]]; then
    rm -f "$SUITE_LOG"
  fi
}

trap cleanup EXIT
trap 'exit 2' INT TERM

usage() {
  cat <<'EOF'
usage: perf/gate.sh [--profile gate|full]

  gate  CorpusBench, one fork, 3x1s warmup, 5x1s measurement, -prof gc
  full  annotation defaults (5 forks, 10+10 x 1s) for CorpusBench and ParsingBench

exit 0  pass
exit 1  regression (a metric, or CorpusBenchSuite)
exit 2  infrastructure error
EOF
}

acquire_lock() {
  local attempt holder again
  attempt=0
  while [[ "$attempt" -lt 5 ]]; do
    attempt=$((attempt + 1))
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      echo $$ >"$LOCK_DIR/pid" || die "failed to record pid in $LOCK_DIR"
      LOCK_HELD=1
      return 0
    fi
    holder=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
      die "another gate is running (pid $holder). One gate per machine."
    fi
    again=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    if [[ "$again" == "$holder" ]]; then
      rm -rf "$LOCK_DIR"
    fi
  done
  die "could not acquire $LOCK_DIR"
}

PROFILE=gate
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || die "missing value for --profile"
      PROFILE=$2
      shift 2
      ;;
    --profile=*)
      PROFILE=${1#--profile=}
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

case "$PROFILE" in
  gate | full) ;;
  *) die "unknown profile: $PROFILE (expected gate or full)" ;;
esac

command -v git >/dev/null 2>&1 || die "git is not on PATH"
command -v python3 >/dev/null 2>&1 || die "python3 is not on PATH"
command -v sbt >/dev/null 2>&1 || die "sbt is not on PATH"
[[ -f "$ROOT/perf/baseline.sha" ]] || die "missing perf/baseline.sha"
[[ -f "$ROOT/perf/jdk.txt" ]] || die "missing perf/jdk.txt"
[[ -f "$ROOT/perf/thresholds.json" ]] || die "missing perf/thresholds.json"
[[ -f "$ROOT/perf/compare.py" ]] || die "missing perf/compare.py"

python3 "$ROOT/perf/compare.py" --self-test >/dev/null

BASELINE_SHA=$(awk 'NF && $1 !~ /^#/ { print $1; exit }' "$ROOT/perf/baseline.sha")
if [[ ! "$BASELINE_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then
  die "perf/baseline.sha must contain a full 40-character commit"
fi
git -C "$ROOT" cat-file -e "${BASELINE_SHA}^{commit}" 2>/dev/null \
  || die "baseline commit is not in this repository: $BASELINE_SHA"

JDK_SPEC=$(awk 'NF && $1 !~ /^#/ { print; exit }' "$ROOT/perf/jdk.txt")
JDK_VENDOR=${JDK_SPEC%% *}
JDK_MAJOR=${JDK_SPEC#* }
if [[ -z "$JDK_VENDOR" || -z "$JDK_MAJOR" || "$JDK_VENDOR" == "$JDK_MAJOR" ]]; then
  die "perf/jdk.txt must contain \"<vendor> <major>\", for example \"Temurin 21\""
fi
if [[ ! "$JDK_MAJOR" =~ ^[0-9]+$ ]]; then
  die "perf/jdk.txt major version must be an integer, got: $JDK_MAJOR"
fi

# Drop the "Picked up JAVA_TOOL_OPTIONS" line. java -version writes to stderr.
write_java_version() {
  local home=$1
  local dest=$2
  local raw
  [[ -x "$home/bin/java" ]] || return 1
  raw=$(mktemp /tmp/scala-yaml-jdk.XXXXXX)
  if ! "$home/bin/java" -version >"$raw" 2>&1; then
    rm -f "$raw"
    return 1
  fi
  grep -v '^Picked up ' "$raw" >"$dest" || true
  rm -f "$raw"
  return 0
}

version_matches() {
  local home=$1
  local out
  out=$(mktemp /tmp/scala-yaml-jdk.XXXXXX)
  if ! write_java_version "$home" "$out"; then
    rm -f "$out"
    return 1
  fi
  if ! grep -q -F -- "$JDK_VENDOR" "$out"; then
    rm -f "$out"
    return 1
  fi
  if ! grep -E -q "version \"${JDK_MAJOR}([.\"])" "$out"; then
    rm -f "$out"
    return 1
  fi
  rm -f "$out"
  return 0
}

find_java() {
  local home
  local jh
  local d
  if [[ -n "${PERF_JAVA_HOME:-}" ]] && version_matches "$PERF_JAVA_HOME"; then
    printf '%s\n' "$PERF_JAVA_HOME"
    return 0
  fi
  if [[ -n "${JAVA_HOME:-}" ]] && version_matches "$JAVA_HOME"; then
    printf '%s\n' "$JAVA_HOME"
    return 0
  fi
  if [[ -d "$HOME/.sdkman/candidates/java" ]]; then
    for d in "$HOME/.sdkman/candidates/java"/*; do
      if version_matches "$d"; then
        printf '%s\n' "$d"
        return 0
      fi
    done
  fi
  if [[ -d /Library/Java/JavaVirtualMachines ]]; then
    for d in /Library/Java/JavaVirtualMachines/*/Contents/Home; do
      if version_matches "$d"; then
        printf '%s\n' "$d"
        return 0
      fi
    done
  fi
  if [[ -d /usr/lib/jvm ]]; then
    for d in /usr/lib/jvm/*; do
      if version_matches "$d"; then
        printf '%s\n' "$d"
        return 0
      fi
    done
  fi
  jh=$(/usr/libexec/java_home -v "$JDK_MAJOR" 2>/dev/null || true)
  if [[ -n "$jh" ]] && version_matches "$jh"; then
    printf '%s\n' "$jh"
    return 0
  fi
  return 1
}

acquire_lock

JAVA_HOME=$(find_java) || die "no JDK matches perf/jdk.txt (${JDK_VENDOR} ${JDK_MAJOR} is required)"
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"
JDK_VERSION_FILE=$(mktemp /tmp/scala-yaml-jdk.XXXXXX)
write_java_version "$JAVA_HOME" "$JDK_VERSION_FILE" || die "java -version failed"
log "JDK: $JAVA_HOME"
cat "$JDK_VERSION_FILE" >&2

# Tracked and unstaged differences against the baseline commit. Untracked
# files are a separate check: git diff does not list them.
set +e
git -C "$ROOT" diff --quiet "$BASELINE_SHA" -- project integration-tests
diff_rc=$?
set -e
if [[ "$diff_rc" -eq 1 ]]; then
  git -C "$ROOT" diff --stat "$BASELINE_SHA" -- project integration-tests >&2 || true
  die "candidate project/ or integration-tests/ differs from $BASELINE_SHA"
elif [[ "$diff_rc" -ne 0 ]]; then
  die "git diff failed while checking project/ and integration-tests/ ($diff_rc)"
fi
untracked=$(git -C "$ROOT" ls-files --others --exclude-standard -- project integration-tests)
if [[ -n "$untracked" ]]; then
  log "$untracked"
  die "untracked files under project/ or integration-tests/"
fi

CANDIDATE_SHA=$(git -C "$ROOT" rev-parse HEAD)
if [[ -n $(git -C "$ROOT" status --porcelain --untracked-files=no) ]]; then
  DIRTY=true
else
  DIRTY=false
fi

CACHE_ROOT="/tmp/scala-yaml-perf-worktrees"
BASELINE_WT="$CACHE_ROOT/baseline-$BASELINE_SHA"
START=$(date +%s)

log "==> candidate CorpusBenchSuite"
SUITE_LOG=$(mktemp /tmp/scala-yaml-suite.XXXXXX)
set +e
(
  cd "$ROOT"
  sbt --java-home "$JAVA_HOME" -batch 'benchmark/testOnly *CorpusBenchSuite'
) >"$SUITE_LOG" 2>&1
suite_rc=$?
set -e
cat "$SUITE_LOG" >&2
if [[ "$suite_rc" -ne 0 ]]; then
  log "GATE FAIL (CorpusBenchSuite)"
  exit 1
fi
if ! grep -E -q '\[info\] Passed: Total [1-9][0-9]*, Failed 0, Errors 0, Passed [1-9][0-9]*' "$SUITE_LOG"; then
  die "CorpusBenchSuite did not report a positive passing total"
fi
rm -f "$SUITE_LOG"
SUITE_LOG=

mkdir -p "$CACHE_ROOT"
if [[ -d "$BASELINE_WT" ]]; then
  git -C "$BASELINE_WT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "baseline path exists but is not a git worktree: $BASELINE_WT"
  actual=$(git -C "$BASELINE_WT" rev-parse HEAD)
  [[ "$actual" == "$BASELINE_SHA" ]] || die "cached worktree HEAD is $actual, expected $BASELINE_SHA"
  log "reusing baseline worktree $BASELINE_WT"
  git -C "$BASELINE_WT" reset --hard "$BASELINE_SHA" >/dev/null \
    || die "baseline reset failed"
  git -C "$BASELINE_WT" clean -fd >/dev/null \
    || die "baseline clean failed"
else
  log "creating baseline worktree $BASELINE_WT"
  git -C "$ROOT" worktree add --detach "$BASELINE_WT" "$BASELINE_SHA" >/dev/null \
    || die "git worktree add failed"
fi

if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude 'target/' \
    --exclude '.DS_Store' \
    "$ROOT/benchmark/" "$BASELINE_WT/benchmark/" \
    || die "failed to copy benchmark sources onto the baseline worktree"
else
  rm -rf "$BASELINE_WT/benchmark"
  mkdir -p "$BASELINE_WT/benchmark"
  tar -C "$ROOT" \
    --exclude 'benchmark/target' \
    --exclude '.DS_Store' \
    -cf - benchmark | tar -C "$BASELINE_WT" -xf - \
    || die "failed to copy benchmark sources onto the baseline worktree"
fi

if ! python3 - "$ROOT/benchmark" "$BASELINE_WT/benchmark" <<'PY'
import filecmp, os, sys
left, right = sys.argv[1], sys.argv[2]

def files(root):
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name != "target"]
        for name in filenames:
            if name == ".DS_Store":
                continue
            full = os.path.join(dirpath, name)
            found[os.path.relpath(full, root)] = full
    return found

a, b = files(left), files(right)
only_a = sorted(set(a) - set(b))
only_b = sorted(set(b) - set(a))
differ = [
    rel for rel in sorted(set(a) & set(b))
    if not filecmp.cmp(a[rel], b[rel], shallow=False)
]
if only_a or only_b or differ:
    print("benchmark trees differ after copy", file=sys.stderr)
    for rel in only_a:
        print(f"only in candidate: {rel}", file=sys.stderr)
    for rel in only_b:
        print(f"only in baseline: {rel}", file=sys.stderr)
    for rel in differ:
        print(f"differs: {rel}", file=sys.stderr)
    sys.exit(1)
PY
then
  die "benchmark trees differ after copy"
fi

# Jmh/run compiles benchmark tests. The corpus digest suite needs the
# integration test dependency, which is not on the baseline commit.
cp "$ROOT/build.sbt" "$BASELINE_WT/build.sbt" \
  || die "failed to copy build.sbt onto the baseline worktree"
cmp -s "$ROOT/build.sbt" "$BASELINE_WT/build.sbt" \
  || die "build.sbt differs after copy"
if ! git -C "$BASELINE_WT" diff --quiet -- core integration-tests project; then
  die "baseline core, integration-tests, or project drifted from $BASELINE_SHA"
fi

mkdir -p "$OUT"
rm -f "$OUT"/baseline-*.json "$OUT"/candidate-*.json "$OUT"/report.json "$OUT"/report.md

if [[ "$PROFILE" == "gate" ]]; then
  JMH_FLAGS=(-f 1 -wi 3 -i 5 -w 1s -r 1s -prof gc -rf json)
  JMH_BENCHES=(CorpusBench)
else
  JMH_FLAGS=(-prof gc -rf json)
  JMH_BENCHES=(CorpusBench ParsingBench)
fi

run_jmh() {
  local label=$1
  local dir=$2
  local outfile=$3
  local t0
  local t1
  local joined
  log "==> $label"
  t0=$(date +%s)
  joined="${JMH_FLAGS[*]} -rff $outfile ${JMH_BENCHES[*]}"
  if ! (
    cd "$dir"
    sbt --java-home "$JAVA_HOME" -batch "benchmark/Jmh/run $joined"
  ); then
    die "JMH failed for $label"
  fi
  t1=$(date +%s)
  [[ -s "$outfile" ]] || die "JMH wrote no result file for $label ($outfile)"
  log "    $label finished in $((t1 - t0))s"
}

run_jmh "baseline 1" "$BASELINE_WT" "$OUT/baseline-1.json"
run_jmh "candidate 1" "$ROOT" "$OUT/candidate-1.json"
run_jmh "baseline 2" "$BASELINE_WT" "$OUT/baseline-2.json"
run_jmh "candidate 2" "$ROOT" "$OUT/candidate-2.json"
END=$(date +%s)
WALL=$((END - START))

set +e
python3 "$ROOT/perf/compare.py" \
  --profile "$PROFILE" \
  --thresholds "$ROOT/perf/thresholds.json" \
  --baseline "$OUT/baseline-1.json" \
  --baseline "$OUT/baseline-2.json" \
  --candidate "$OUT/candidate-1.json" \
  --candidate "$OUT/candidate-2.json" \
  --repo "$ROOT" \
  --report-json "$OUT/report.json" \
  --report-md "$OUT/report.md" \
  --wall-seconds "$WALL" \
  --baseline-sha "$BASELINE_SHA" \
  --candidate-sha "$CANDIDATE_SHA" \
  --dirty "$DIRTY" \
  --jdk-file "$JDK_VERSION_FILE"
rc=$?
set -e

if [[ "$rc" -ne 0 && "$rc" -ne 1 ]]; then
  die "comparator exited $rc"
fi
if [[ "$rc" -eq 0 ]]; then
  log "GATE PASS (${WALL}s)"
else
  log "GATE FAIL (${WALL}s)"
fi
exit "$rc"
