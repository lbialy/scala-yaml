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
