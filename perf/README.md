# Performance gate

`perf/gate.sh` rejects a change that makes YAML parsing slower, more allocation-heavy, or asymptotically worse than the commit in `perf/baseline.sha`. It does not optimize anything. `core/` is the only code allowed to differ between the two sides.

The loop must also run `sbt benchmark/test`. Golden event digests fail when a parser change drops or reshapes events, which a throughput win from doing less work would otherwise hide.

## What is measured

`CorpusBench` parses three LinkML schemas (`cgmes-core.yml`, `cgmes-dynamics.yml`, `TC57CIM.yml`) two ways:

- `fromStringToEvents` — `ParserImpl(Tokenizer.make(yaml)).getEvents()`
- `fromStringToNode` — `parseYaml(yaml)`

There is no struct decoding. `ParsingBench` is unchanged and runs only in the `full` profile.

Both sides run the same harness. The script checks out `perf/baseline.sha` in a git worktree, then copies `benchmark/` and `build.sbt` from this tree onto it. `build.sbt` is copied because sbt-jmh's `Jmh` config extends `Test`, so `Jmh/run` compiles benchmark tests, and those tests need the integration dependency. After the copy, `benchmark/` must match byte for byte (build output under `target/` and `.DS_Store` are ignored) and `core/`, `integration-tests/`, and `project/` on the baseline worktree must still match the baseline commit.

Runs are ordered baseline, candidate, baseline, candidate. Each side's score is the mean of its two runs. That cancels a slow drift in temperature or turbo.

| Metric | JMH source | Fail when |
|---|---|---|
| Allocation | `gc.alloc.rate.norm` (B/op) from `-prof gc` | candidate > baseline × (1 + t) |
| Throughput | primary score (ops/s) | candidate < baseline × (1 − t) **and** the 99.9% intervals do not overlap |
| Scaling | (ns/byte on `TC57CIM.yml`) / (ns/byte on `cgmes-core.yml`) | candidate ratio > baseline ratio × (1 + t) |

ns/byte uses on-disk file size and the mean throughput. Scaling is computed per `CorpusBench` method. Touching interval endpoints count as overlap. With two runs per side, the intervals are disjoint only when every baseline interval is disjoint from every candidate interval.

`t` lives in `perf/thresholds.json`. Do not put absolute ops/s or B/op numbers in the repo as limits. They do not transfer across machines. See `perf/calibration.md` for the A/A spread these values came from.

## Running

Both sides must use the JDK in `perf/jdk.txt` (Temurin 21.0.5+11). The script searches `PERF_JAVA_HOME`, `JAVA_HOME`, sdkman, and the usual macOS and Linux install paths, and exits 2 if `java -version` is not an exact match. B/op depends on the JDK and on the JVM flags in `CommonParams`.

```bash
perf/gate.sh --profile gate
perf/gate.sh --profile full
```

`gate` is the default. It runs:

```text
benchmark/Jmh/run -f 1 -wi 3 -i 5 -w 1s -r 1s -prof gc -rf json -rff <out>.json CorpusBench
```

`full` uses the annotation defaults (5 forks, 10 warmup iterations, 10 measurement iterations, 1 second each) and also runs `ParsingBench`. There is no time limit on `full`.

Exit codes: `0` pass, `1` regression, `2` infrastructure error (JDK mismatch, JMH failure, missing result, harness diff).

Reports are written to `perf/out/report.json` and `perf/out/report.md`. That directory is gitignored. Raw JMH JSON is `perf/out/baseline-*.json` and `perf/out/candidate-*.json`.

On this machine the `gate` profile took 241s, 229s, and 229s for three A/A runs. The first includes a cold baseline compile. Keep the host quiet: AC power, no other heavy jobs, and no thermal throttle if you can avoid it.

The baseline worktree is cached at `/tmp/scala-yaml-perf-worktrees/baseline-<sha>`. Delete that directory and run `git worktree prune` when you want a clean compile.

## Re-pointing the baseline

Put the full 40-character SHA of the loop's starting commit in `perf/baseline.sha`, one line, no other text. The commit must already be in this clone. The next run builds a new worktree for that SHA. Do not point it at a commit that already contains the change under test.

## Regenerating event digests

Digests are SHA-256 of the UTF-8 bytes of `TestRunnerUtils.convertEventToYamlTestSuiteFormat` applied to `events.map(_.kind)`. The hex is stored, plus a trailing newline, in `benchmark/src/test/resources/linkml/<file>.events.sha256`. Event `toString` is not hashed. `Anchor` is a value class without a stable `toString`.

From the repository root:

```bash
sbt -Dregenerate=true 'benchmark/test'
```

A human does this when an intended event-format change lands. The planned case is flow-collection markers `{}` / `[]` on `SequenceStart` / `MappingStart`. A mismatch is a signal for review. The loop must not regenerate digests to make the suite pass.

`benchmark/test` reads the formatter from `integration-tests` via `benchmark.dependsOn(integration % "test->compile")` in `build.sbt`. That dependency compiles on Scala 3. The formatter was not copied.

## Files the loop must not edit

`perf/PROTECTED`:

```text
benchmark/**
perf/**
build.sbt
```
