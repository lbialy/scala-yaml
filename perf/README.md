# Performance gate

`perf/gate.sh` rejects a change that makes YAML parsing slower, more allocation-heavy, or asymptotically worse than the commit in `perf/baseline.sha`. It does not optimize anything. `core/` is the only tree allowed to differ between the two measured sides.

The gate runs `benchmark/testOnly *CorpusBenchSuite` on the candidate before JMH. That suite checks the golden event digests. A failing suite exits 1. A digest mismatch means the parser dropped or reshaped events, which a throughput win from doing less work would otherwise hide.

## What is measured

`CorpusBench` parses three LinkML schemas (`cgmes-core.yml`, `cgmes-dynamics.yml`, `TC57CIM.yml`) two ways:

- `fromStringToEvents` — `ParserImpl(Tokenizer.make(yaml)).getEvents()`
- `fromStringToNode` — `parseYaml(yaml)`

There is no struct decoding. `ParsingBench` is unchanged and runs only in the `full` profile.

Both sides run the same harness. The script checks out `perf/baseline.sha` in a git worktree, then copies `benchmark/` and `build.sbt` from this tree onto it. `build.sbt` is copied because sbt-jmh's `Jmh` config extends `Test`, so `Jmh/run` compiles benchmark tests, and those tests need the integration dependency. After the copy, `benchmark/` must match byte for byte (build output under `target/` and `.DS_Store` are ignored) and `core/`, `integration-tests/`, and `project/` on the baseline worktree must still match the baseline commit.

The candidate tree is checked the same way before the run. `project/` and `integration-tests/` must match `perf/baseline.sha`, including untracked files. That covers `project/plugin.sbt`, `project/BuildHelper.scala`, and the event formatter. A change there exits 2.

Runs are ordered baseline, candidate, baseline, candidate. Each side's score is the mean of its two runs. That cancels a slow drift in temperature or turbo.

| Metric | JMH source | Fail when |
|---|---|---|
| Allocation | `gc.alloc.rate.norm` (B/op) from `-prof gc` | candidate > baseline × (1 + t) |
| Throughput | primary score (ops/s) | the mean is below baseline × (1 − t), and both candidate runs are slower than both baseline runs |
| Scaling | (ns/byte on `TC57CIM.yml`) / (ns/byte on `cgmes-core.yml`) | candidate ratio > baseline ratio × (1 + t) |

ns/byte uses on-disk file size and the mean throughput. Scaling is computed per `CorpusBench` method.

The two runs are the noise control for throughput. A drop that is past `t` in the mean, and that shows up in every candidate run, fails. A mean that is past `t` while one candidate run is still as fast as a baseline run passes. JMH's 99.9% interval at one fork and five iterations is about ±3.9× the spread between iterations, so requiring those intervals to be disjoint lets a real slowdown through.

Scaling is the check for a cost that grows with the keys already stored in a mapping. Duplicate-key detection written as a linear scan of the keys seen so far is that shape, and `TC57CIM.yml`'s mappings are what expose it. A per-scalar cost that stays proportional to file size leaves the ratio alone.

`t` lives in `perf/thresholds.json`. Do not put absolute ops/s or B/op numbers in the repo as limits. They do not transfer across machines. See `perf/calibration.md` for the A/A spread these values came from.

## Running

Both sides share one JDK. `perf/jdk.txt` names the vendor and the major version (`Temurin 21`). Any patch of that JDK is accepted. The script searches `PERF_JAVA_HOME`, `JAVA_HOME`, sdkman, and the usual macOS and Linux install paths, and exits 2 when none match. The report records the full `java -version` output. Lines that start with `Picked up ` are ignored, so `JAVA_TOOL_OPTIONS` does not fail the check. B/op depends on the JDK and on the JVM flags in `CommonParams`.

```bash
perf/gate.sh --profile gate
perf/gate.sh --profile full
```

`gate` is the default. It runs `CorpusBenchSuite`, then:

```text
benchmark/Jmh/run -f 1 -wi 3 -i 5 -w 1s -r 1s -prof gc -rf json -rff <out>.json CorpusBench
```

`full` uses the annotation defaults (5 forks, 10 warmup iterations, 10 measurement iterations, 1 second each) and also runs `ParsingBench`. There is no time limit on `full`.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | pass |
| 1 | regression: a metric failed, or `CorpusBenchSuite` failed |
| 2 | infrastructure: JDK mismatch, lock held, `project/` or `integration-tests/` drift, JMH failure, missing result, harness diff |

Reports are written to `perf/out/report.json` and `perf/out/report.md`. That directory is gitignored. Raw JMH JSON is `perf/out/baseline-*.json` and `perf/out/candidate-*.json`.

On this machine the `gate` profile took 241s, 229s, and 229s for three A/A runs. Those times are from before `CorpusBenchSuite` was part of the gate. The first includes a cold baseline compile. Keep the host quiet: AC power, no other heavy jobs, and no thermal throttle if you can avoid it.

The baseline worktree is cached at `/tmp/scala-yaml-perf-worktrees/baseline-<sha>`. Delete that directory and run `git worktree prune` when you want a clean compile.

## One gate per machine

The cached baseline worktree is shared, and two JMH runs at once ruin each other's numbers. The script takes a machine-wide lock by creating the directory `/tmp/scala-yaml-perf-gate.lock` (`mkdir` is the atomic operation; this is the macOS lock). A live pid in that directory makes a second gate exit 2. A stale lock whose pid is dead is removed. A loop that works on tickets in parallel worktrees still runs one gate on the machine.

## Re-pointing the baseline

Leave `perf/baseline.sha` at the loop session's starting commit for the whole session. The 5% allocation tolerance is the budget for every change measured against that commit. Moving the baseline forward after each accepted change gives every later change a fresh 5%. The observed A/A allocation noise is about 1e-7, so 1% or 2% would also clear a single comparison. The file holds the full 40-character SHA, one line, no other text. The commit must already be in this clone. The next run builds a new worktree for that SHA. Point it at a commit that does not already contain the change under test.

## Regenerating event digests

Digests are SHA-256 of the UTF-8 bytes of `TestRunnerUtils.convertEventToYamlTestSuiteFormat` applied to `events.map(_.kind)`. The hex is stored, plus a trailing newline, in `benchmark/src/test/resources/linkml/<file>.events.sha256`. Event `toString` is not hashed. `Anchor` is a value class without a stable `toString`.

From the repository root:

```bash
sbt -Dregenerate=true 'benchmark/test'
```

A human does this when an intended event-format change lands. The last one was the yaml-test-suite `data-2022-01-17` update, which made the formatter escape values like `test.event` and print anchor and tag names, and added flow-collection markers `{}` / `[]` on `SequenceStart` / `MappingStart`. The corpus has no flow collections, so the marker change on its own does not require new digests. The corpus is full of double-quoted scalars with escapes (`’`, `\r\n`, line continuations) that the parser does not decode yet. Decoding them changes these digests. The schemas do have multi-line scalars whose continuation lines start with `[`, such as the IEEE note under `ExcIEEEAC7B` in `cgmes-dynamics.yml`. A flow-collection change that treats a line-leading `[` as a flow sequence fails these digests.

The digests cover parser events only. `fromStringToNode` is checked for `isRight` and by the benchmark. `compose/` stays outside the loop's allowed paths.

A mismatch is a signal for review. The loop must not regenerate digests to make the suite pass.

`benchmark/test` reads the formatter from `integration-tests` via `benchmark.dependsOn(integration % "test->compile")` in `build.sbt`. That dependency compiles on Scala 3. The formatter was not copied.

## Files the loop must not edit

`perf/PROTECTED`:

```text
benchmark/**
perf/**
build.sbt
project/**
integration-tests/**
```

`integration-tests` holds `TestRunnerUtils.convertEventToYamlTestSuiteFormat`. Editing that formatter can make a changed event stream hash to the stored digest.

## Loop rules

- One gate at a time on the machine. The script holds `/tmp/scala-yaml-perf-gate.lock`.
- Leave `perf/baseline.sha` fixed for the session.
- Leave the paths in `perf/PROTECTED` untouched, and do not pass `-Dregenerate=true`.
- Leave `compose/` outside the allowed paths. The digest suite pins parser events.
