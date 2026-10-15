#!/usr/bin/env python3
"""Compare interleaved JMH runs for the scala-yaml performance gate.

Python 3 stdlib only. Exit 0 on pass, 1 on regression, 2 on an infrastructure error.

Each side is two JMH JSON files (the A,B,A,B schedule). Scores are the mean of
the two runs. Allocation uses gc.alloc.rate.norm (B/op). Throughput uses the
primary score (ops/s). A throughput row fails when the mean is below
baseline * (1 - t) and both candidate runs are slower than both baseline runs.

Scaling is (ns per byte of TC57CIM.yml) / (ns per byte of cgmes-core.yml),
using on-disk byte sizes and the mean throughput. It is computed per benchmark
method that measured both files.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

ALLOC_SUFFIX = "gc.alloc.rate.norm"
LARGE_FILE = "TC57CIM.yml"
SMALL_FILE = "cgmes-core.yml"
FILE_ORDER = (
    "cgmes-core.yml",
    "cgmes-dynamics.yml",
    "TC57CIM.yml",
    "docker-compose.yaml",
    "geo.yaml",
)


class InfraError(Exception):
    pass


def infra(message):
    raise InfraError(message)


def finite(value, what):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        infra(f"{what} is not a number: {value!r} ({exc})")
    if not math.isfinite(number):
        infra(f"{what} is not finite: {number}")
    return number


def interval_of(metric, what):
    score = finite(metric["score"], what + " score")
    confidence = metric.get("scoreConfidence")
    if isinstance(confidence, list) and len(confidence) == 2:
        low = finite(confidence[0], what + " confidence low")
        high = finite(confidence[1], what + " confidence high")
    elif "scoreError" in metric:
        error = finite(metric["scoreError"], what + " scoreError")
        low, high = score - error, score + error
    else:
        infra(f"{what} has no scoreConfidence or scoreError")
    if low > high:
        low, high = high, low
    return score, low, high


def alloc_metric(secondary, what):
    if not isinstance(secondary, dict):
        infra(f"{what} secondaryMetrics is not an object")
    for key, value in secondary.items():
        if str(key).endswith(ALLOC_SUFFIX):
            return value
    infra(
        f"{what} is missing {ALLOC_SUFFIX}; secondary metrics: {sorted(secondary)}"
    )


def load_jmh(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        infra(f"cannot read {path}: {exc}")
    if not isinstance(data, list) or not data:
        infra(f"{path} is not a non-empty JMH JSON array")
    runs = {}
    for index, row in enumerate(data):
        if not isinstance(row, dict):
            infra(f"{path}[{index}] is not an object")
        bench = row.get("benchmark")
        params = row.get("params") or {}
        if not isinstance(bench, str) or not bench:
            infra(f"{path}[{index}] has no benchmark name")
        if not isinstance(params, dict) or len(params) != 1:
            infra(f"{path}[{index}] ({bench}) should have exactly one param, got {params!r}")
        filename = next(iter(params.values()))
        if not isinstance(filename, str) or not filename:
            infra(f"{path}[{index}] param value is not a file name")
        mode = row.get("mode")
        if mode not in (None, "thrpt"):
            infra(f"{path}[{index}] mode is {mode!r}, expected thrpt")
        primary = row.get("primaryMetric")
        if not isinstance(primary, dict):
            infra(f"{path}[{index}] has no primaryMetric")
        where = f"{bench} {filename}"
        score, low, high = interval_of(primary, where)
        if score <= 0:
            infra(f"{where} throughput is {score}")
        alloc = alloc_metric(row.get("secondaryMetrics"), where)
        alloc_score, _, _ = interval_of(alloc, where + " alloc")
        if alloc_score < 0:
            infra(f"{where} allocation is {alloc_score}")
        key = (bench, filename)
        if key in runs:
            infra(f"duplicate result for {where} in {path}")
        runs[key] = {
            "score": score,
            "low": low,
            "high": high,
            "unit": primary.get("scoreUnit") or "ops/s",
            "alloc": alloc_score,
            "alloc_unit": alloc.get("scoreUnit") or "B/op",
        }
    return runs


def merge_pair(first, second, label):
    if set(first) != set(second):
        missing = sorted(set(first) ^ set(second))
        infra(f"{label} runs measured different benchmarks: {missing}")
    merged = {}
    for key in first:
        left, right = first[key], second[key]
        merged[key] = {
            "score": (left["score"] + right["score"]) / 2.0,
            "intervals": [(left["low"], left["high"]), (right["low"], right["high"])],
            "unit": left["unit"],
            "alloc": (left["alloc"] + right["alloc"]) / 2.0,
            "alloc_unit": left["alloc_unit"],
            "scores": [left["score"], right["score"]],
            "allocs": [left["alloc"], right["alloc"]],
        }
    return merged


def intervals_separated(left_intervals, right_intervals):
    """True when no left interval overlaps any right interval."""
    for left_low, left_high in left_intervals:
        for right_low, right_high in right_intervals:
            if right_low <= left_high and left_low <= right_high:
                return False
    return True


def relative_delta(candidate, baseline):
    if baseline == 0:
        if candidate == 0:
            return 0.0
        return math.inf
    return (candidate - baseline) / baseline


def file_sizes(repo, names):
    root = Path(repo) / "benchmark" / "src" / "main" / "resources"
    sizes = {}
    for name in sorted(names):
        found = None
        for candidate in (root / "linkml" / name, root / name):
            if candidate.is_file():
                found = candidate
                break
        if found is None:
            infra(f"missing benchmark resource {name}")
        size = found.stat().st_size
        if size <= 0:
            infra(f"{name} is empty")
        sizes[name] = size
    return sizes


def ns_per_byte(ops, nbytes):
    if ops <= 0 or nbytes <= 0:
        infra(f"cannot compute ns/byte from ops={ops} bytes={nbytes}")
    return 1e9 / (ops * float(nbytes))


def short_bench(name):
    prefix = "org.virtuslab.yaml.benchmark."
    if name.startswith(prefix):
        return name[len(prefix) :]
    return name


def file_rank(name):
    try:
        return FILE_ORDER.index(name)
    except ValueError:
        return len(FILE_ORDER)


def load_thresholds(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        infra(f"cannot read thresholds {path}: {exc}")
    if not isinstance(data, dict):
        infra("thresholds file must be a JSON object")
    out = {}
    for key in ("allocation", "throughput", "scaling"):
        if key not in data:
            infra(f"thresholds missing {key}")
        value = finite(data[key], f"threshold {key}")
        if value < 0:
            infra(f"threshold {key} is negative")
        out[key] = value
    return out


def evaluate(baseline, candidate, thresholds, sizes):
    if set(baseline) != set(candidate):
        missing = sorted(set(baseline) ^ set(candidate))
        infra(f"baseline and candidate measured different benchmarks: {missing}")

    rows = []
    by_bench = {}
    for key in baseline:
        bench, filename = key
        by_bench.setdefault(bench, {})[filename] = key

    for bench in sorted(by_bench):
        filenames = sorted(by_bench[bench], key=lambda name: (file_rank(name), name))
        for filename in filenames:
            base = baseline[(bench, filename)]
            cand = candidate[(bench, filename)]
            alloc_delta = relative_delta(cand["alloc"], base["alloc"])
            alloc_fail = cand["alloc"] > base["alloc"] * (1.0 + thresholds["allocation"])
            rows.append(
                {
                    "benchmark": bench,
                    "file": filename,
                    "metric": "gc.alloc.rate.norm",
                    "unit": base["alloc_unit"],
                    "baseline": base["alloc"],
                    "candidate": cand["alloc"],
                    "delta": alloc_delta,
                    "tolerance": thresholds["allocation"],
                    "baseline_runs": base["allocs"],
                    "candidate_runs": cand["allocs"],
                    "rule": "candidate <= baseline * (1 + t)",
                    "verdict": "FAIL" if alloc_fail else "PASS",
                }
            )

            mean_below = cand["score"] < base["score"] * (1.0 - thresholds["throughput"])
            # Both candidate samples sit strictly below both baseline samples.
            runs_ordered = max(cand["scores"]) < min(base["scores"])
            separated = intervals_separated(base["intervals"], cand["intervals"])
            if mean_below and runs_ordered:
                throughput_verdict = "FAIL"
                note = "mean drop exceeds t and both candidate runs are slower"
            elif mean_below:
                throughput_verdict = "PASS"
                note = "mean drop exceeds t but the two sides' runs overlap"
            else:
                throughput_verdict = "PASS"
                note = None
            rows.append(
                {
                    "benchmark": bench,
                    "file": filename,
                    "metric": "throughput",
                    "unit": base["unit"],
                    "baseline": base["score"],
                    "candidate": cand["score"],
                    "delta": relative_delta(cand["score"], base["score"]),
                    "tolerance": thresholds["throughput"],
                    "baseline_runs": base["scores"],
                    "candidate_runs": cand["scores"],
                    "baseline_intervals": base["intervals"],
                    "candidate_intervals": cand["intervals"],
                    "intervals_overlap": not separated,
                    "runs_ordered": runs_ordered,
                    "rule": (
                        "fail when the mean is below baseline * (1 - t) "
                        "and both candidate runs are slower than both baseline runs"
                    ),
                    "note": note,
                    "verdict": throughput_verdict,
                }
            )

        files_for_bench = by_bench[bench]
        if LARGE_FILE in files_for_bench and SMALL_FILE in files_for_bench:
            rows.append(
                scaling_row(
                    bench,
                    baseline,
                    candidate,
                    thresholds["scaling"],
                    sizes,
                )
            )
        elif "CorpusBench" in bench:
            infra(f"{bench} is missing {SMALL_FILE} or {LARGE_FILE}")

    verdict = "FAIL" if any(row["verdict"] == "FAIL" for row in rows) else "PASS"
    return rows, verdict


def scaling_row(bench, baseline, candidate, tolerance, sizes):
    def ratio(side):
        large = side[(bench, LARGE_FILE)]["score"]
        small = side[(bench, SMALL_FILE)]["score"]
        large_ns = ns_per_byte(large, sizes[LARGE_FILE])
        small_ns = ns_per_byte(small, sizes[SMALL_FILE])
        return large_ns / small_ns, large_ns, small_ns

    base_ratio, base_large, base_small = ratio(baseline)
    cand_ratio, cand_large, cand_small = ratio(candidate)
    failed = cand_ratio > base_ratio * (1.0 + tolerance)
    return {
        "benchmark": bench,
        "file": f"{LARGE_FILE} / {SMALL_FILE}",
        "metric": "scaling",
        "unit": "ns/byte ratio",
        "baseline": base_ratio,
        "candidate": cand_ratio,
        "delta": relative_delta(cand_ratio, base_ratio),
        "tolerance": tolerance,
        "baseline_ns_per_byte": {LARGE_FILE: base_large, SMALL_FILE: base_small},
        "candidate_ns_per_byte": {LARGE_FILE: cand_large, SMALL_FILE: cand_small},
        "rule": "candidate ratio <= baseline ratio * (1 + t)",
        "verdict": "FAIL" if failed else "PASS",
    }


def fmt_num(value):
    if value is None or (isinstance(value, float) and math.isinf(value)):
        return "inf"
    if abs(value) >= 1000:
        return f"{value:,.1f}"
    if abs(value) >= 10:
        return f"{value:.2f}"
    return f"{value:.4f}"


def fmt_delta(value):
    if value is None or (isinstance(value, float) and math.isinf(value)):
        return "inf"
    return f"{value * 100:+.2f}%"


def result_cell(row):
    note = row.get("note")
    if note and row["verdict"] == "PASS":
        return "PASS (runs overlap)"
    return row["verdict"]


def render_markdown(report):
    lines = [
        f"# {report['verdict']}",
        "",
        f"Profile: `{report['profile']}`",
        f"Baseline: `{report['baseline_sha']}`",
        f"Candidate: `{report['candidate_sha']}`"
        + (" (dirty)" if report["candidate_dirty"] else ""),
        f"Wall time: {report['wall_seconds']:.1f}s",
        "JDK:",
        "",
        "```",
        report["jdk"].rstrip("\n"),
        "```",
        "",
        "| Benchmark | File | Metric | Baseline | Candidate | Delta | Result |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for row in report["rows"]:
        lines.append(
            "| {bench} | {file} | {metric} | {base} | {cand} | {delta} | {result} |".format(
                bench=short_bench(row["benchmark"]),
                file=row["file"],
                metric=row["metric"] if row["metric"] != "gc.alloc.rate.norm" else "B/op",
                base=fmt_num(row["baseline"]),
                cand=fmt_num(row["candidate"]),
                delta=fmt_delta(row["delta"]),
                result=result_cell(row),
            )
        )
    lines.extend(
        [
            "",
            "B/op fails when candidate > baseline × (1 + t). "
            "Throughput fails when the mean is below baseline × (1 − t) and both "
            "candidate runs are slower than both baseline runs. "
            "Scaling fails when the TC57CIM.yml / cgmes-core.yml ns-per-byte ratio "
            "exceeds the baseline ratio × (1 + t).",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args, rows, verdict, thresholds, sizes):
    jdk_text = Path(args.jdk_file).read_text(encoding="utf-8").strip()
    jdk_line = jdk_text.splitlines()[0] if jdk_text else "unknown"
    return {
        "verdict": verdict,
        "profile": args.profile,
        "baseline_sha": args.baseline_sha,
        "candidate_sha": args.candidate_sha,
        "candidate_dirty": args.dirty == "true",
        "wall_seconds": args.wall_seconds,
        "jdk": jdk_text,
        "jdk_line": jdk_line,
        "thresholds": thresholds,
        "file_bytes": sizes,
        "rows": rows,
    }


def write_report(report, json_path, md_path):
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")


def compare(args):
    thresholds = load_thresholds(args.thresholds)
    if len(args.baseline) != 2 or len(args.candidate) != 2:
        infra("expected exactly two baseline JSON files and two candidate JSON files")
    baseline = merge_pair(load_jmh(args.baseline[0]), load_jmh(args.baseline[1]), "baseline")
    candidate = merge_pair(
        load_jmh(args.candidate[0]), load_jmh(args.candidate[1]), "candidate"
    )
    names = {filename for _, filename in baseline}
    sizes = file_sizes(args.repo, names)
    rows, verdict = evaluate(baseline, candidate, thresholds, sizes)
    report = build_report(args, rows, verdict, thresholds, sizes)
    write_report(report, Path(args.report_json), Path(args.report_md))
    print(render_markdown(report))
    return 0 if verdict == "PASS" else 1


def sample_row(bench, filename, score, error, alloc):
    return {
        "benchmark": bench,
        "mode": "thrpt",
        "params": {"name": filename},
        "primaryMetric": {
            "score": score,
            "scoreError": error,
            "scoreConfidence": [score - error, score + error],
            "scoreUnit": "ops/s",
        },
        "secondaryMetrics": {
            "·gc.alloc.rate.norm": {
                "score": alloc,
                "scoreError": 0.0,
                "scoreConfidence": [alloc, alloc],
                "scoreUnit": "B/op",
            }
        },
    }


def write_run(directory, name, rows):
    path = Path(directory) / name
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def run_case(
    directory,
    thresholds,
    sizes,
    baseline_rows,
    candidate_rows,
    baseline_rows_2=None,
    candidate_rows_2=None,
):
    if baseline_rows_2 is None:
        baseline_rows_2 = baseline_rows
    if candidate_rows_2 is None:
        candidate_rows_2 = candidate_rows
    base_1 = write_run(directory, "b1.json", baseline_rows)
    base_2 = write_run(directory, "b2.json", baseline_rows_2)
    cand_1 = write_run(directory, "c1.json", candidate_rows)
    cand_2 = write_run(directory, "c2.json", candidate_rows_2)
    thresholds_path = Path(directory) / "thresholds.json"
    thresholds_path.write_text(json.dumps(thresholds), encoding="utf-8")
    jdk = Path(directory) / "jdk.txt"
    jdk.write_text('openjdk version "test"\n', encoding="utf-8")
    report_json = Path(directory) / "report.json"
    report_md = Path(directory) / "report.md"
    args = argparse.Namespace(
        profile="gate",
        thresholds=str(thresholds_path),
        baseline=[str(base_1), str(base_2)],
        candidate=[str(cand_1), str(cand_2)],
        repo=str(directory),
        report_json=str(report_json),
        report_md=str(report_md),
        wall_seconds=1.0,
        baseline_sha="a" * 40,
        candidate_sha="b" * 40,
        dirty="false",
        jdk_file=str(jdk),
    )
    # file_sizes reads the repo. Supply sizes directly for unit cases.
    baseline = merge_pair(load_jmh(base_1), load_jmh(base_2), "baseline")
    candidate = merge_pair(load_jmh(cand_1), load_jmh(cand_2), "candidate")
    rows, verdict = evaluate(baseline, candidate, thresholds, sizes)
    return verdict, rows


def self_test():
    bench = "org.virtuslab.yaml.benchmark.CorpusBench.fromStringToNode"
    thresholds = {"allocation": 0.05, "throughput": 0.10, "scaling": 0.25}
    sizes = {SMALL_FILE: 100, LARGE_FILE: 400, "cgmes-dynamics.yml": 200}
    files = (SMALL_FILE, "cgmes-dynamics.yml", LARGE_FILE)

    def both(score, error, alloc, large_score=None):
        rows = []
        for name in files:
            this_score = large_score if name == LARGE_FILE and large_score is not None else score
            rows.append(sample_row(bench, name, this_score, error, alloc))
        return rows

    with tempfile.TemporaryDirectory() as tmp:
        verdict, _ = run_case(tmp, thresholds, sizes, both(100, 1, 1000), both(100, 1, 1000))
        expect(verdict == "PASS", "identical runs pass")

        verdict, rows = run_case(tmp, thresholds, sizes, both(100, 1, 1000), both(100, 1, 1060))
        alloc = next(row for row in rows if row["metric"] == "gc.alloc.rate.norm")
        expect(verdict == "FAIL" and alloc["verdict"] == "FAIL", "alloc +6% fails")

        verdict, rows = run_case(tmp, thresholds, sizes, both(100, 1, 1000), both(100, 1, 1040))
        alloc = next(row for row in rows if row["metric"] == "gc.alloc.rate.norm")
        expect(verdict == "PASS" and alloc["verdict"] == "PASS", "alloc +4% passes")

        slow = both(80, 1, 1000)
        base = both(100, 1, 1000)
        verdict, rows = run_case(tmp, thresholds, sizes, base, slow)
        thr = next(row for row in rows if row["metric"] == "throughput")
        expect(verdict == "FAIL" and thr["verdict"] == "FAIL", "ordered throughput drop fails")
        expect(thr["runs_ordered"], "20% drop with both runs slower is ordered")

        # Same 20% drop. The 99.9% intervals overlap (error 30), and the old
        # rule let this pass. Both runs are still slower than both baseline runs.
        wide = both(80, 30, 1000)
        verdict, rows = run_case(tmp, thresholds, sizes, base, wide)
        thr = next(row for row in rows if row["metric"] == "throughput")
        expect(
            verdict == "FAIL" and thr["verdict"] == "FAIL" and thr["intervals_overlap"],
            "overlapping intervals still fail when every candidate run is slower",
        )

        # Means are 100 and 85 (drop 15%), but candidate 100 beats baseline 80.
        interleaved_base_hi = both(120, 1, 1000)
        interleaved_base_lo = both(80, 1, 1000)
        interleaved_cand_hi = both(100, 1, 1000)
        interleaved_cand_lo = both(70, 1, 1000)
        verdict, rows = run_case(
            tmp,
            thresholds,
            sizes,
            interleaved_base_hi,
            interleaved_cand_hi,
            interleaved_base_lo,
            interleaved_cand_lo,
        )
        thr = next(row for row in rows if row["metric"] == "throughput" and row["file"] == SMALL_FILE)
        expect(
            verdict == "PASS" and thr["verdict"] == "PASS" and not thr["runs_ordered"],
            "mean drop past t passes when the runs are not ordered",
        )

        # Exactly t. 90 < 100 * (1 - 0.10) is false, so the boundary passes.
        boundary = both(90, 0.1, 1000)
        verdict, rows = run_case(tmp, thresholds, sizes, base, boundary)
        thr = next(row for row in rows if row["metric"] == "throughput" and row["file"] == SMALL_FILE)
        expect(thr["verdict"] == "PASS" and thr["runs_ordered"], "drop equal to t passes")

        mild = both(95, 0.1, 1000)
        verdict, rows = run_case(tmp, thresholds, sizes, base, mild)
        thr = next(row for row in rows if row["metric"] == "throughput" and row["file"] == SMALL_FILE)
        expect(thr["verdict"] == "PASS", "drop inside tolerance passes")

        faster = both(130, 1, 900)
        verdict, _ = run_case(tmp, thresholds, sizes, base, faster)
        expect(verdict == "PASS", "faster and smaller passes")

        # Same small-file throughput, much slower large file -> worse ratio.
        # ns/byte large uses score 50 vs small 100. Baseline uses 100 for both,
        # so baseline ratio is (1/100/400) / (1/100/100) = 100/400 = 0.25.
        # Candidate large score 50 -> ratio 0.5, delta +100%, above 0.25.
        skewed = both(100, 1, 1000, large_score=50)
        verdict, rows = run_case(tmp, thresholds, sizes, base, skewed)
        scaling = next(row for row in rows if row["metric"] == "scaling")
        expect(verdict == "FAIL" and scaling["verdict"] == "FAIL", "superlinear ratio fails")

        slight = both(100, 1, 1000, large_score=90)
        verdict, rows = run_case(tmp, thresholds, sizes, base, slight)
        scaling = next(row for row in rows if row["metric"] == "scaling")
        expect(scaling["verdict"] == "PASS", "mild ratio shift passes")

    print("compare.py self-test passed")
    return 0


def expect(condition, label):
    if not condition:
        infra(f"self-test failed: {label}")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Compare scala-yaml JMH gate runs")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--profile", choices=("gate", "full"))
    parser.add_argument("--thresholds")
    parser.add_argument("--baseline", action="append", default=[])
    parser.add_argument("--candidate", action="append", default=[])
    parser.add_argument("--repo")
    parser.add_argument("--report-json")
    parser.add_argument("--report-md")
    parser.add_argument("--wall-seconds", type=float)
    parser.add_argument("--baseline-sha")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--dirty", choices=("true", "false"), default="false")
    parser.add_argument("--jdk-file")
    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        required = [
            args.profile,
            args.thresholds,
            args.repo,
            args.report_json,
            args.report_md,
            args.baseline_sha,
            args.candidate_sha,
            args.jdk_file,
        ]
        if args.wall_seconds is None or any(item is None for item in required):
            infra("missing required arguments")
        return compare(args)
    except InfraError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
