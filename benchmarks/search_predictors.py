from __future__ import annotations

# SPDX-License-Identifier: Apache-2.0

import argparse
import bz2
import gzip
import json
import lzma
import platform
import statistics
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from benchmark_real_data import (  # noqa: E402
    DEFAULT_EXCLUDE_GLOBS,
    RealDataOptions,
    discover_files,
    escape_md,
    format_bytes,
    path_label,
    saved_pct,
    size_delta_pct,
)
from starlight_codec.codec import decode_slb1, encode_slb1, sha256_digest  # noqa: E402


SEARCH_MAGIC = b"SLP1"
COMPRESSORS = ("gzip", "zlib", "bz2", "lzma")
OFFSETS = (1, 2, 4, 8, 16)


@dataclass(frozen=True)
class Candidate:
    name: str
    transform: str
    compressor: str
    params: dict[str, int]


@dataclass(frozen=True)
class SearchOptions:
    paths: list[Path]
    label_root: Path | None = None
    recursive: bool = True
    include_hidden: bool = False
    max_file_bytes: int = 1024 * 1024
    file_limit: int = 64
    exclude_globs: list[str] | None = None
    candidate_limit: int = 64
    time_limit_seconds: float = 30.0
    max_passes: int = 2
    search_mode: str = "adaptive"
    min_improvement_pct: float = 1.0
    max_worst_regression_pct: float = 2.0
    include_file_results: bool = False


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be 0 or greater")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def compress_payload(data: bytes, compressor: str) -> bytes:
    if compressor == "gzip":
        return gzip.compress(data, compresslevel=9, mtime=0)
    if compressor == "zlib":
        return zlib.compress(data, level=9)
    if compressor == "bz2":
        return bz2.compress(data, compresslevel=9)
    if compressor == "lzma":
        return lzma.compress(data, preset=9)
    raise ValueError(f"Unsupported compressor: {compressor}")


def decompress_payload(data: bytes, compressor: str) -> bytes:
    if compressor == "gzip":
        return gzip.decompress(data)
    if compressor == "zlib":
        return zlib.decompress(data)
    if compressor == "bz2":
        return bz2.decompress(data)
    if compressor == "lzma":
        return lzma.decompress(data)
    raise ValueError(f"Unsupported compressor: {compressor}")


def transform_identity(data: bytes, params: dict[str, int]) -> bytes:
    return data


def inverse_identity(data: bytes, params: dict[str, int]) -> bytes:
    return data


def transform_delta_prev(data: bytes, params: dict[str, int]) -> bytes:
    offset = int(params["offset"])
    output = bytearray(len(data))
    for index, byte in enumerate(data):
        prediction = data[index - offset] if index >= offset else 0
        output[index] = (byte - prediction) & 0xFF
    return bytes(output)


def inverse_delta_prev(residual: bytes, params: dict[str, int]) -> bytes:
    offset = int(params["offset"])
    output = bytearray(len(residual))
    for index, delta in enumerate(residual):
        prediction = output[index - offset] if index >= offset else 0
        output[index] = (prediction + delta) & 0xFF
    return bytes(output)


def transform_xor_prev(data: bytes, params: dict[str, int]) -> bytes:
    offset = int(params["offset"])
    output = bytearray(len(data))
    for index, byte in enumerate(data):
        prediction = data[index - offset] if index >= offset else 0
        output[index] = byte ^ prediction
    return bytes(output)


def inverse_xor_prev(residual: bytes, params: dict[str, int]) -> bytes:
    offset = int(params["offset"])
    output = bytearray(len(residual))
    for index, value in enumerate(residual):
        prediction = output[index - offset] if index >= offset else 0
        output[index] = value ^ prediction
    return bytes(output)


def transform_delta_avg2(data: bytes, params: dict[str, int]) -> bytes:
    output = bytearray(len(data))
    for index, byte in enumerate(data):
        if index >= 2:
            prediction = (data[index - 1] + data[index - 2]) // 2
        elif index == 1:
            prediction = data[index - 1]
        else:
            prediction = 0
        output[index] = (byte - prediction) & 0xFF
    return bytes(output)


def inverse_delta_avg2(residual: bytes, params: dict[str, int]) -> bytes:
    output = bytearray(len(residual))
    for index, delta in enumerate(residual):
        if index >= 2:
            prediction = (output[index - 1] + output[index - 2]) // 2
        elif index == 1:
            prediction = output[index - 1]
        else:
            prediction = 0
        output[index] = (prediction + delta) & 0xFF
    return bytes(output)


TRANSFORMS: dict[str, tuple[Callable[[bytes, dict[str, int]], bytes], Callable[[bytes, dict[str, int]], bytes]]] = {
    "identity": (transform_identity, inverse_identity),
    "delta-prev": (transform_delta_prev, inverse_delta_prev),
    "xor-prev": (transform_xor_prev, inverse_xor_prev),
    "delta-avg2": (transform_delta_avg2, inverse_delta_avg2),
}


def build_candidates(limit: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    for compressor in COMPRESSORS:
        candidates.append(Candidate(f"identity+{compressor}", "identity", compressor, {}))
    for transform in ("delta-prev", "xor-prev"):
        for offset in OFFSETS:
            for compressor in COMPRESSORS:
                candidates.append(
                    Candidate(
                        f"{transform}-{offset}+{compressor}",
                        transform,
                        compressor,
                        {"offset": offset},
                    )
                )
    for compressor in COMPRESSORS:
        candidates.append(Candidate(f"delta-avg2+{compressor}", "delta-avg2", compressor, {}))
    return candidates[:limit]


def sample_bytes(data: bytes, size: int = 4096) -> bytes:
    return data[:size] if len(data) > size else data


def fast_compressed_size(data: bytes) -> int:
    return len(zlib.compress(data, level=1))


def candidate_prior(candidate: Candidate, corpus: list[dict[str, Any]]) -> float:
    if candidate.transform == "identity":
        return -5.0
    scores: list[float] = []
    transform, _inverse = TRANSFORMS[candidate.transform]
    for row in corpus[:16]:
        data = sample_bytes(bytes(row["data"]))
        if not data:
            continue
        baseline = fast_compressed_size(data)
        transformed = transform(data, candidate.params)
        transformed_size = fast_compressed_size(transformed)
        scores.append(saved_pct(transformed_size, baseline))
    if not scores:
        return 0.0
    compressor_bias = {
        "zlib": 0.4,
        "gzip": 0.2,
        "bz2": -0.2,
        "lzma": -0.8,
    }
    return statistics.mean(scores) + compressor_bias.get(candidate.compressor, 0.0)


def stat_average(stats: dict[str, dict[str, float]], key: str) -> float:
    row = stats.get(key)
    if not row or row["count"] <= 0:
        return 0.0
    return row["score"] / row["count"]


def stat_count(stats: dict[str, dict[str, float]], key: str) -> float:
    row = stats.get(key)
    return float(row["count"]) if row else 0.0


def candidate_stat_keys(candidate: Candidate) -> list[str]:
    keys = [
        f"transform:{candidate.transform}",
        f"compressor:{candidate.compressor}",
        f"pair:{candidate.transform}+{candidate.compressor}",
    ]
    if "offset" in candidate.params:
        keys.append(f"offset:{candidate.transform}:{candidate.params['offset']}")
    return keys


def model_score_candidate(
    candidate: Candidate,
    corpus: list[dict[str, Any]],
    stats: dict[str, dict[str, float]],
    evaluated_count: int,
) -> float:
    prior = candidate_prior(candidate, corpus)
    learned = sum(stat_average(stats, key) for key in candidate_stat_keys(candidate))
    visits = sum(stat_count(stats, key) for key in candidate_stat_keys(candidate))
    exploration = 2.0 * ((1.0 + evaluated_count) ** 0.5 / (1.0 + visits))
    return prior + learned + exploration


def choose_candidate(
    pending: list[Candidate],
    corpus: list[dict[str, Any]],
    stats: dict[str, dict[str, float]],
    evaluated_count: int,
    search_mode: str,
) -> tuple[Candidate, float]:
    if search_mode == "exhaustive":
        return pending.pop(0), 0.0
    scored = [
        (
            model_score_candidate(candidate, corpus, stats, evaluated_count),
            index,
            candidate,
        )
        for index, candidate in enumerate(pending)
    ]
    score, index, candidate = max(scored, key=lambda item: (item[0], -item[1]))
    del pending[index]
    return candidate, round(score, 3)


def update_learning_stats(
    stats: dict[str, dict[str, float]],
    candidate: Candidate,
    result: dict[str, Any],
) -> None:
    reward = float(result["aggregateImprovementPct"])
    reward -= max(0.0, float(result["worstRegressionPct"])) * 0.5
    reward -= float(result["roundTripFailures"]) * 100.0
    reward -= float(result["encodeDecodeMedianMs"]) * 0.05
    if result["decision"] == "control-baseline":
        reward *= 0.25
    for key in candidate_stat_keys(candidate):
        row = stats.setdefault(key, {"count": 0.0, "score": 0.0})
        row["count"] += 1.0
        row["score"] += reward


def encode_candidate(data: bytes, candidate: Candidate) -> bytes:
    transform, _inverse = TRANSFORMS[candidate.transform]
    transformed = transform(data, candidate.params)
    payload = compress_payload(transformed, candidate.compressor)
    header = {
        "schemaVersion": 1,
        "kind": "star-light-predictor-search",
        "candidate": candidate.name,
        "transform": candidate.transform,
        "compressor": candidate.compressor,
        "params": candidate.params,
        "rawBytes": len(data),
        "transformedBytes": len(transformed),
        "payloadBytes": len(payload),
        "inputDigest": sha256_digest(data),
        "payloadDigest": sha256_digest(payload),
    }
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return SEARCH_MAGIC + struct.pack("<IQ", len(header_bytes), len(payload)) + header_bytes + payload


def decode_candidate(artifact: bytes) -> bytes:
    if len(artifact) < 16 or artifact[:4] != SEARCH_MAGIC:
        raise ValueError("Predictor artifact magic mismatch.")
    header_len, payload_len = struct.unpack("<IQ", artifact[4:16])
    expected_len = 16 + header_len + payload_len
    if expected_len != len(artifact):
        raise ValueError("Predictor artifact length mismatch.")
    header = json.loads(artifact[16 : 16 + header_len].decode("utf-8"))
    payload = artifact[16 + header_len :]
    if sha256_digest(payload) != header.get("payloadDigest"):
        raise ValueError("Predictor artifact payload digest mismatch.")
    transform_name = str(header["transform"])
    compressor = str(header["compressor"])
    params = {str(key): int(value) for key, value in dict(header.get("params", {})).items()}
    transformed = decompress_payload(payload, compressor)
    _transform, inverse = TRANSFORMS[transform_name]
    data = inverse(transformed, params)
    if len(data) != int(header["rawBytes"]):
        raise ValueError("Predictor artifact raw size mismatch.")
    if sha256_digest(data) != header.get("inputDigest"):
        raise ValueError("Predictor artifact input digest mismatch.")
    return data


def median(values: list[float]) -> float:
    return round(statistics.median(values), 3) if values else 0.0


def classify_decision(
    candidate: Candidate,
    aggregate_improvement_pct: float,
    worst_regression_pct: float,
    round_trip_failures: int,
    complete: bool,
    options: SearchOptions,
) -> str:
    if candidate.transform == "identity":
        return "control-baseline"
    if not complete:
        return "incomplete"
    if round_trip_failures:
        return "reject-roundtrip"
    if aggregate_improvement_pct >= options.min_improvement_pct and worst_regression_pct <= options.max_worst_regression_pct:
        return "promote-candidate"
    if aggregate_improvement_pct > 0:
        return "watch-candidate"
    return "reject-no-gain"


def discover_search_files(options: SearchOptions) -> tuple[list[Path], list[dict[str, Any]]]:
    real_options = RealDataOptions(
        paths=options.paths,
        label_root=options.label_root,
        recursive=options.recursive,
        include_hidden=options.include_hidden,
        max_file_bytes=options.max_file_bytes,
        limit=options.file_limit,
        exclude_globs=options.exclude_globs,
        repetitions=0,
        verify=False,
    )
    return discover_files(real_options)


def load_corpus(files: list[Path], options: SearchOptions) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in files:
        data = path.read_bytes()
        baseline = encode_slb1(
            data,
            max_passes=options.max_passes,
            model="auto",
            planner="stdlib-auto",
        )
        rows.append(
            {
                "path": path_label(path, options.label_root),
                "extension": path.suffix.lower(),
                "data": data,
                "rawBytes": len(data),
                "baselineBytes": len(baseline.artifact),
                "baselinePlanner": baseline.metadata["selectedPlanner"],
                "baselineModel": baseline.metadata["selectedModel"],
            }
        )
    return rows


def evaluate_candidate(
    candidate: Candidate,
    corpus: list[dict[str, Any]],
    deadline: float,
    options: SearchOptions,
) -> tuple[dict[str, Any], bool]:
    artifact_total = 0
    baseline_total = 0
    worst_regression = float("-inf")
    best_file_gain = float("-inf")
    timings: list[float] = []
    round_trip_failures = 0
    evaluated_files = 0
    file_results: list[dict[str, Any]] = []
    complete = True

    for row in corpus:
        if time.monotonic() >= deadline:
            complete = False
            break
        data = bytes(row["data"])
        start = time.perf_counter_ns()
        try:
            artifact = encode_candidate(data, candidate)
            decoded = decode_candidate(artifact)
            if decoded != data:
                round_trip_failures += 1
        except Exception:
            round_trip_failures += 1
            artifact = data
        end = time.perf_counter_ns()
        timings.append((end - start) / 1_000_000)
        artifact_bytes = len(artifact)
        baseline_bytes = int(row["baselineBytes"])
        artifact_total += artifact_bytes
        baseline_total += baseline_bytes
        evaluated_files += 1
        delta = size_delta_pct(artifact_bytes, baseline_bytes)
        gain = saved_pct(artifact_bytes, baseline_bytes)
        worst_regression = max(worst_regression, delta)
        best_file_gain = max(best_file_gain, gain)
        if options.include_file_results:
            file_results.append(
                {
                    "path": row["path"],
                    "extension": row["extension"],
                    "baselineBytes": baseline_bytes,
                    "candidateBytes": artifact_bytes,
                    "candidateVsBaselinePct": delta,
                }
            )

    aggregate_improvement = saved_pct(artifact_total, baseline_total)
    if worst_regression == float("-inf"):
        worst_regression = 0.0
    if best_file_gain == float("-inf"):
        best_file_gain = 0.0
    result = {
        "candidate": candidate.name,
        "transform": candidate.transform,
        "compressor": candidate.compressor,
        "params": candidate.params,
        "evaluatedFiles": evaluated_files,
        "complete": complete,
        "baselineBytes": baseline_total,
        "candidateBytes": artifact_total,
        "aggregateImprovementPct": aggregate_improvement,
        "worstRegressionPct": round(worst_regression, 2),
        "bestFileGainPct": round(best_file_gain, 2),
        "encodeDecodeMedianMs": median(timings),
        "roundTripFailures": round_trip_failures,
        "decision": classify_decision(
            candidate,
            aggregate_improvement,
            round(worst_regression, 2),
            round_trip_failures,
            complete,
            options,
        ),
    }
    if options.include_file_results:
        result["files"] = file_results
    stopped = time.monotonic() >= deadline
    return result, stopped


def build_results(options: SearchOptions) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + options.time_limit_seconds
    files, skipped = discover_search_files(options)
    corpus = load_corpus(files, options)
    candidates = build_candidates(options.candidate_limit)
    pending = list(candidates)
    stats: dict[str, dict[str, float]] = {}
    selection_trace: list[dict[str, Any]] = []
    candidate_results: list[dict[str, Any]] = []
    stopped_reason = "complete"
    while pending:
        if time.monotonic() >= deadline:
            stopped_reason = "time-limit"
            break
        candidate, selection_score = choose_candidate(
            pending,
            corpus,
            stats,
            len(candidate_results),
            options.search_mode,
        )
        result, stopped = evaluate_candidate(candidate, corpus, deadline, options)
        result["selectionScore"] = selection_score
        candidate_results.append(result)
        update_learning_stats(stats, candidate, result)
        selection_trace.append(
            {
                "candidate": candidate.name,
                "selectionScore": selection_score,
                "decision": result["decision"],
                "aggregateImprovementPct": result["aggregateImprovementPct"],
            }
        )
        if stopped:
            stopped_reason = "time-limit"
            break
    if len(candidate_results) < len(candidates) and stopped_reason == "complete":
        stopped_reason = "candidate-limit"
    elapsed = round(time.monotonic() - started, 3)
    ranked = sorted(
        candidate_results,
        key=lambda row: (
            str(row["decision"]) != "promote-candidate",
            str(row["decision"]) == "control-baseline",
            -float(row["aggregateImprovementPct"]),
            float(row["worstRegressionPct"]),
        ),
    )
    return {
        "schemaVersion": 1,
        "benchmark": "star-light-codec-predictor-search",
        "resultLicense": "CC0-1.0",
        "scriptLicense": "Apache-2.0",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "timeLimitSeconds": options.time_limit_seconds,
        "elapsedSeconds": elapsed,
        "stoppedReason": stopped_reason,
        "searchMode": options.search_mode,
        "maxPasses": options.max_passes,
        "candidateLimit": options.candidate_limit,
        "candidateCount": len(candidates),
        "evaluatedCandidateCount": len(candidate_results),
        "fileCount": len(corpus),
        "skipped": skipped,
        "thresholds": {
            "minImprovementPct": options.min_improvement_pct,
            "maxWorstRegressionPct": options.max_worst_regression_pct,
        },
        "baseline": "SLB1 --planner stdlib-auto --model auto",
        "modelState": {
            key: {
                "count": int(value["count"]),
                "averageReward": round(value["score"] / value["count"], 3) if value["count"] else 0.0,
            }
            for key, value in sorted(stats.items())
        },
        "selectionTrace": selection_trace,
        "note": "Experimental local predictor search. No raw file contents are embedded in this result.",
        "candidates": ranked,
    }


def markdown_table(results: dict[str, Any]) -> str:
    lines = [
        "Files: {files}  Candidates: {done}/{total}  Elapsed: {elapsed:.3f}s  Stop: {stop}".format(
            files=results["fileCount"],
            done=results["evaluatedCandidateCount"],
            total=results["candidateCount"],
            elapsed=float(results["elapsedSeconds"]),
            stop=results["stoppedReason"],
        ),
        f"Mode: {results['searchMode']}",
        "",
        "| candidate | score | bytes | improvement | worst regression | best file gain | failures | median ms | decision |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in results["candidates"]:
        lines.append(
            "| {candidate} | {score:+.3f} | {bytes} | {improvement:+.2f}% | {regression:+.2f}% | {gain:+.2f}% | {failures} | {ms:.3f} | {decision} |".format(
                candidate=escape_md(str(row["candidate"])),
                score=float(row["selectionScore"]),
                bytes=format_bytes(int(row["candidateBytes"])),
                improvement=float(row["aggregateImprovementPct"]),
                regression=float(row["worstRegressionPct"]),
                gain=float(row["bestFileGainPct"]),
                failures=int(row["roundTripFailures"]),
                ms=float(row["encodeDecodeMedianMs"]),
                decision=escape_md(str(row["decision"])),
            )
        )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search deterministic predictor candidates for Star Light Codec.")
    parser.add_argument("paths", nargs="+", help="files or directories to search against")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--output", help="write output to a file instead of stdout")
    parser.add_argument("--label-root", help="base directory for relative path labels")
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--include-hidden", action="store_true")
    parser.add_argument("--max-file-bytes", type=positive_int, default=1024 * 1024)
    parser.add_argument("--file-limit", type=positive_int, default=64)
    parser.add_argument("--candidate-limit", type=positive_int, default=64)
    parser.add_argument("--time-limit-seconds", type=positive_float, default=30.0)
    parser.add_argument("--max-passes", type=positive_int, default=2)
    parser.add_argument("--search-mode", choices=["adaptive", "exhaustive"], default="adaptive")
    parser.add_argument("--min-improvement-pct", type=non_negative_float, default=1.0)
    parser.add_argument("--max-worst-regression-pct", type=non_negative_float, default=2.0)
    parser.add_argument("--include-file-results", action="store_true")
    parser.add_argument(
        "--exclude-glob",
        action="append",
        dest="exclude_globs",
        help="additional path label glob to exclude; can be repeated",
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="include generated/cache files that are excluded by default",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exclude_globs = [] if args.no_default_excludes else list(DEFAULT_EXCLUDE_GLOBS)
    exclude_globs.extend(args.exclude_globs or [])
    options = SearchOptions(
        paths=[Path(path) for path in args.paths],
        label_root=Path(args.label_root) if args.label_root else None,
        recursive=not args.no_recursive,
        include_hidden=args.include_hidden,
        max_file_bytes=args.max_file_bytes,
        file_limit=args.file_limit,
        exclude_globs=exclude_globs,
        candidate_limit=args.candidate_limit,
        time_limit_seconds=args.time_limit_seconds,
        max_passes=args.max_passes,
        search_mode=args.search_mode,
        min_improvement_pct=args.min_improvement_pct,
        max_worst_regression_pct=args.max_worst_regression_pct,
        include_file_results=args.include_file_results,
    )
    results = build_results(options)
    if args.format == "json":
        output = json.dumps(results, indent=2, sort_keys=True) + "\n"
    else:
        output = markdown_table(results) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
