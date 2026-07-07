from __future__ import annotations

# SPDX-License-Identifier: Apache-2.0

import argparse
import base64
import gc
import gzip
import json
import platform
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from starlight_codec.codec import create_capsule, decode_slb1, encode_slb1  # noqa: E402


def gzip_bytes(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9, mtime=0)


def make_repeated_text() -> bytes:
    return (
        "Star Light Codec exact-byte artifact contract.\n"
        "Compressed bytes are opaque to the LLM; tools hydrate exact bytes.\n"
        "Encoder planning can improve while decoder behavior stays boring.\n"
        * 512
    ).encode("utf-8")


def make_json_logs() -> bytes:
    rows = []
    for index in range(768):
        rows.append(
            {
                "event": "codec.transport",
                "index": index,
                "level": "info" if index % 7 else "debug",
                "project": "star-light-codec",
                "tags": ["exact-roundtrip", "llm-transport", f"bucket-{index % 8}"],
            }
        )
    return ("\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n").encode("utf-8")


def make_random_bytes() -> bytes:
    return random.Random(12345).randbytes(64 * 1024)


def make_already_compressed() -> bytes:
    return gzip_bytes(make_random_bytes())


def make_ramp_bytes() -> bytes:
    return bytes((index * 3) % 256 for index in range(64 * 1024))


def format_bytes(value: int) -> str:
    return f"{value:,}"


def ratio(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def saved_pct(part: int, whole: int) -> float:
    return (1.0 - ratio(part, whole)) * 100.0 if whole else 0.0


def median_ms(func: Callable[[], Any], repetitions: int) -> float:
    timings: list[float] = []
    for _ in range(2):
        func()
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(repetitions):
            start = time.perf_counter_ns()
            func()
            end = time.perf_counter_ns()
            timings.append((end - start) / 1_000_000)
    finally:
        if gc_was_enabled:
            gc.enable()
    return statistics.median(timings)


def benchmark_fixture(name: str, data: bytes, max_passes: int, chunk_size: int, repetitions: int) -> dict[str, Any]:
    gz = gzip_bytes(data)
    gz_b64 = base64.b64encode(gz)
    encoded = encode_slb1(data, max_passes=max_passes)
    modeled = encode_slb1(data, max_passes=max_passes, model="auto")
    strong = encode_slb1(data, max_passes=max_passes, model="auto", planner="stdlib-auto")
    capsule = create_capsule(
        data,
        artifact_path=f"{name}.slb1",
        capsule_path=f"{name}.capsule.json",
        max_passes=max_passes,
        summary=f"Synthetic {name} fixture for Star Light Codec benchmarks.",
        tags=["benchmark", name],
        chunk_size=chunk_size,
    )
    capsule_json = json.dumps(capsule.capsule, sort_keys=True, separators=(",", ":")).encode("utf-8")
    first_chunk = capsule.capsule["chunkIndex"][0] if capsule.capsule["chunkIndex"] else {"start": 0, "end": 0}

    encode_ms = median_ms(lambda: encode_slb1(data, max_passes=max_passes), repetitions)
    decode_ms = median_ms(lambda: decode_slb1(encoded.artifact), repetitions)
    capsule_ms = median_ms(
        lambda: create_capsule(
            data,
            artifact_path=f"{name}.slb1",
            capsule_path=f"{name}.capsule.json",
            max_passes=max_passes,
            summary=f"Synthetic {name} fixture for Star Light Codec benchmarks.",
            tags=["benchmark", name],
            chunk_size=chunk_size,
        ),
        repetitions,
    )
    hydrate_chunk_ms = median_ms(
        lambda: decode_slb1(encoded.artifact).data[int(first_chunk["start"]) : int(first_chunk["end"])],
        repetitions,
    )

    return {
        "fixture": name,
        "rawBytes": len(data),
        "gzipBytes": len(gz),
        "gzipBase64Chars": len(gz_b64),
        "slb1Bytes": len(encoded.artifact),
        "modelSlb1Bytes": len(modeled.artifact),
        "selectedModel": modeled.metadata["selectedModel"],
        "strongSlb1Bytes": len(strong.artifact),
        "strongPlanner": strong.metadata["selectedPlanner"],
        "strongModel": strong.metadata["selectedModel"],
        "capsuleBytes": len(capsule_json),
        "chunkCount": len(capsule.capsule["chunkIndex"]),
        "firstChunkBytes": int(first_chunk["end"]) - int(first_chunk["start"]),
        "strategy": encoded.metadata["strategy"],
        "adoptionDecision": encoded.metadata["adoptionDecision"],
        "gzipSavedPct": round(saved_pct(len(gz), len(data)), 2),
        "gzipBase64VsRawPct": round(saved_pct(len(gz_b64), len(data)), 2),
        "slb1SavedPct": round(saved_pct(len(encoded.artifact), len(data)), 2),
        "modelSlb1SavedPct": round(saved_pct(len(modeled.artifact), len(data)), 2),
        "modelVsSlb1Pct": round(saved_pct(len(modeled.artifact), len(encoded.artifact)), 2),
        "strongSlb1SavedPct": round(saved_pct(len(strong.artifact), len(data)), 2),
        "strongVsSlb1Pct": round(saved_pct(len(strong.artifact), len(encoded.artifact)), 2),
        "capsulePromptVsRawPct": round(saved_pct(len(capsule_json), len(data)), 2),
        "encodeMedianMs": round(encode_ms, 3),
        "decodeMedianMs": round(decode_ms, 3),
        "capsuleMedianMs": round(capsule_ms, 3),
        "hydrateChunkMedianMs": round(hydrate_chunk_ms, 3),
    }


def build_results(max_passes: int, chunk_size: int, repetitions: int) -> dict[str, Any]:
    fixtures = {
        "repeated_text": make_repeated_text(),
        "json_logs": make_json_logs(),
        "ramp_bytes": make_ramp_bytes(),
        "random_bytes": make_random_bytes(),
        "already_compressed": make_already_compressed(),
    }
    return {
        "schemaVersion": 1,
        "benchmark": "star-light-codec-transport",
        "resultLicense": "CC0-1.0",
        "scriptLicense": "Apache-2.0",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "maxPasses": max_passes,
        "chunkSize": chunk_size,
        "repetitions": repetitions,
        "note": "Synthetic local benchmark. Results are not universal compression claims.",
        "fixtures": [
            benchmark_fixture(name, data, max_passes=max_passes, chunk_size=chunk_size, repetitions=repetitions)
            for name, data in fixtures.items()
        ],
    }


def markdown_table(results: dict[str, Any]) -> str:
    lines = [
        "| fixture | raw bytes | gzip bytes | gzip+b64 chars | SLB1 bytes | model SLB1 | strong SLB1 | planner | model | capsule bytes | SLB1 saved | strong saved | capsule vs raw | decision | enc ms | dec ms | cap ms | chunk ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in results["fixtures"]:
        lines.append(
            "| {fixture} | {raw} | {gzip} | {gzip_b64} | {slb1} | {model_slb1} | {strong_slb1} | "
            "{planner} | {model} | {capsule} | {slb1_saved:.2f}% | {strong_saved:.2f}% | "
            "{capsule_saved:.2f}% | {decision} | "
            "{enc:.3f} | {dec:.3f} | {cap:.3f} | {chunk:.3f} |".format(
                fixture=row["fixture"],
                raw=format_bytes(row["rawBytes"]),
                gzip=format_bytes(row["gzipBytes"]),
                gzip_b64=format_bytes(row["gzipBase64Chars"]),
                slb1=format_bytes(row["slb1Bytes"]),
                model_slb1=format_bytes(row["modelSlb1Bytes"]),
                strong_slb1=format_bytes(row["strongSlb1Bytes"]),
                planner=row["strongPlanner"],
                model=row["strongModel"],
                capsule=format_bytes(row["capsuleBytes"]),
                slb1_saved=row["slb1SavedPct"],
                strong_saved=row["strongSlb1SavedPct"],
                capsule_saved=row["capsulePromptVsRawPct"],
                decision=row["adoptionDecision"],
                enc=row["encodeMedianMs"],
                dec=row["decodeMedianMs"],
                cap=row["capsuleMedianMs"],
                chunk=row["hydrateChunkMedianMs"],
            )
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Star Light Codec synthetic transport benchmarks.")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--max-passes", type=int, default=2)
    parser.add_argument("--chunk-size", type=int, default=4096)
    parser.add_argument("--repetitions", type=int, default=9)
    parser.add_argument("--output", help="write benchmark output to a file instead of stdout")
    args = parser.parse_args(argv)

    results = build_results(max_passes=args.max_passes, chunk_size=args.chunk_size, repetitions=args.repetitions)
    if args.format == "json":
        output = json.dumps(results, indent=2, sort_keys=True) + "\n"
    else:
        output = markdown_table(results) + "\n"
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
