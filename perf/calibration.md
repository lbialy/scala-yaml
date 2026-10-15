# A/A calibration

Candidate and baseline both ran commit `3e98628bb120e58745f744b5bf63fc180f833c3d` for `core/`. The harness was the `perf-gate` tree. Three `perf/gate.sh --profile gate` runs, back to back, on this machine:

- Temurin 21.0.5+11 (`perf/jdk.txt`)
- macOS, 10 cores, 32 GB RAM

| Run | Verdict | Wall time |
|---|---|---:|
| 1 | PASS | 241s |
| 2 | PASS | 229s |
| 3 | PASS | 229s |

Run 1 includes a cold compile of the baseline worktree. Runs 2 and 3 reuse `/tmp/scala-yaml-perf-worktrees/baseline-3e98628bb120e58745f744b5bf63fc180f833c3d`. All three are under the 5 minute target.

The spread below is the maximum absolute relative delta, `(candidate - baseline) / baseline`, across every row of that metric in the run. Each gate run already averages the two interleaved samples.

| Metric | Run 1 | Run 2 | Run 3 | Max |
|---|---:|---:|---:|---:|
| `gc.alloc.rate.norm` | 1.17e-7 | 5.54e-8 | 2.22e-7 | 2.22e-7 |
| throughput | 0.0165 | 0.0216 | 0.0327 | 0.0327 |
| scaling | 0.0043 | 0.0144 | 0.0604 | 0.0604 |

The largest throughput swing was `CorpusBench.fromStringToEvents` on `TC57CIM.yml` in run 3 (23.56 ops/s vs 22.79 ops/s). The largest scaling swing was the same method in that run (ratio 1.070 vs 1.134). Allocation moved by a couple of bytes on a multi-megabyte operation.

## Tolerances

No A/A run failed at the starting tolerances, so none of them were raised.

| Metric | Observed max | Calibration floor | Chosen `t` |
|---|---:|---:|---:|
| allocation | 2.22e-7 | 2 × spread = 4.5e-7 | **0.05** |
| throughput | 0.0327 | 3 × spread = 0.0982, rounded up to 0.10 | **0.10** |
| scaling | 0.0604 | starting value (A/A passed) | **0.25** |

Throughput's floor rounds up to the starting value, so `perf/thresholds.json` stays at `0.10`. Allocation's floor is far below `0.05`. Scaling's observed spread is about a quarter of `0.25`.

## Review follow-up

These A/A runs used the earlier throughput rule: mean drop past `t`, and disjoint 99.9% intervals. At one fork and five iterations that interval is about ±3.9× the spread between iterations, so a real drop can sit inside overlapping intervals. The self-test at the time let a 20% drop pass for that reason. The rule is now: the mean is below baseline × (1 − t), and both candidate runs are slower than both baseline runs. `t` stays 0.10. The floor above was computed from the mean-score spread (3 × 0.0327 = 0.0982), which this rule still uses. The three A/A runs were not repeated after the rule change.

Allocation stays at 0.05. The observed noise is about 2e-7, so 0.01 or 0.02 would also clear one comparison. 0.05 is the budget for a whole loop session against a fixed `perf/baseline.sha`. Moving the baseline forward after each accepted change gives every later change a fresh 5%.

The capped per-scalar `:+` injection kept a similar ns/byte on every file, so the scaling rows passed. That run does not show the check firing on a slowdown that grows with mapping size. The probe for that is duplicate-key detection written as a linear scan of keys already seen in the mapping. It has not been run.

The JDK at calibration time was Temurin 21.0.5+11. The gate now accepts any Temurin 21 and records the full `java -version` text in the report.
