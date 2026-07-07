from __future__ import annotations

# SPDX-License-Identifier: Apache-2.0

import argparse
import bz2
import gc
import fnmatch
import gzip
import json
import lzma
import platform
import statistics
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

from starlight_codec.codec import decode_slb1, encode_slb1, sha256_digest  # noqa: E402


Compressor = tuple[str, Callable[[bytes], bytes]]
DEFAULT_EXCLUDE_GLOBS = [
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.egg-info/**",
]


@dataclass(frozen=True)
class RealDataOptions:
    paths: list[Path]
    label_root: Path | None = None
    recursive: bool = True
    include_hidden: bool = False
    include_digest: bool = False
    max_file_bytes: int = 8 * 1024 * 1024
    limit: int | None = None
    exclude_globs: list[str] | None = None
    max_passes: int = 2
    repetitions: int = 1
    verify: bool = True


def available_compressors() -> list[Compressor]:
    compressors: list[Compressor] = [
        ("gzip", lambda data: gzip.compress(data, compresslevel=9, mtime=0)),
        ("zlib", lambda data: zlib.compress(data, level=9)),
        ("bz2", lambda data: bz2.compress(data, compresslevel=9)),
        ("lzma", lambda data: lzma.compress(data, preset=9)),
    ]
    try:
        import brotli  # type: ignore

        compressors.append(("brotli", lambda data: brotli.compress(data, quality=11)))
    except Exception:
        pass
    try:
        import zstandard as zstd  # type: ignore

        compressors.append(("zstd", lambda data: zstd.ZstdCompressor(level=19).compress(data)))
    except Exception:
        pass
    return compressors


def format_bytes(value: int) -> str:
    return f"{value:,}"


def saved_pct(part: int, whole: int) -> float:
    return round((1.0 - (part / whole)) * 100.0, 2) if whole else 0.0


def size_delta_pct(part: int, baseline: int) -> float:
    return round(((part / baseline) - 1.0) * 100.0, 2) if baseline else 0.0


def median_ms(func: Callable[[], Any], repetitions: int) -> float | None:
    if repetitions <= 0:
        return None
    timings: list[float] = []
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
    return round(statistics.median(timings), 3)


def is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def path_label(path: Path, label_root: Path | None) -> str:
    resolved = path.resolve()
    if label_root is not None:
        try:
            return resolved.relative_to(label_root.resolve()).as_posix()
        except ValueError:
            pass
    return path.name


def is_excluded(label: str, patterns: list[str] | None) -> bool:
    return any(fnmatch.fnmatchcase(label, pattern) for pattern in patterns or [])


def discover_files(options: RealDataOptions) -> tuple[list[Path], list[dict[str, Any]]]:
    files: list[Path] = []
    skipped: list[dict[str, Any]] = []
    for input_path in options.paths:
        path = input_path.resolve()
        if not path.exists():
            skipped.append({"path": str(input_path), "reason": "missing"})
            continue
        candidates = [path]
        if path.is_dir():
            pattern = "**/*" if options.recursive else "*"
            candidates = list(path.glob(pattern))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            label = path_label(candidate, options.label_root)
            if not options.include_hidden and is_hidden(candidate.relative_to(candidate.anchor)):
                skipped.append({"path": label, "reason": "hidden"})
                continue
            if is_excluded(label, options.exclude_globs):
                skipped.append({"path": label, "reason": "excluded"})
                continue
            size = candidate.stat().st_size
            if size > options.max_file_bytes:
                skipped.append(
                    {
                        "path": label,
                        "reason": "too-large",
                        "bytes": size,
                    }
                )
                continue
            files.append(candidate)
    unique = sorted(set(files), key=lambda item: item.as_posix().lower())
    if options.limit is not None and len(unique) > options.limit:
        for candidate in unique[options.limit :]:
            skipped.append({"path": path_label(candidate, options.label_root), "reason": "limit"})
        unique = unique[: options.limit]
    return unique, skipped


def benchmark_file(path: Path, options: RealDataOptions, compressors: list[Compressor]) -> dict[str, Any]:
    data = path.read_bytes()
    raw_bytes = len(data)
    codec_rows: list[dict[str, Any]] = []
    for name, compressor in compressors:
        compressed = compressor(data)
        codec_rows.append(
            {
                "codec": name,
                "bytes": len(compressed),
                "savedPct": saved_pct(len(compressed), raw_bytes),
                "encodeMedianMs": median_ms(lambda compressor=compressor: compressor(data), options.repetitions),
            }
        )

    slb1 = encode_slb1(data, max_passes=options.max_passes, model="none")
    modeled = encode_slb1(data, max_passes=options.max_passes, model="auto")
    verification = "skipped"
    if options.verify:
        verification = "pass" if decode_slb1(modeled.artifact).data == data else "fail"

    best_general = min(codec_rows, key=lambda row: int(row["bytes"])) if codec_rows else None
    best_general_bytes = int(best_general["bytes"]) if best_general else raw_bytes
    digest = sha256_digest(data)
    row: dict[str, Any] = {
        "path": path_label(path, options.label_root),
        "extension": path.suffix.lower(),
        "rawBytes": raw_bytes,
        "generalCodecs": codec_rows,
        "bestGeneralCodec": best_general["codec"] if best_general else "none",
        "bestGeneralBytes": best_general_bytes,
        "bestGeneralSavedPct": saved_pct(best_general_bytes, raw_bytes),
        "slb1Bytes": len(slb1.artifact),
        "slb1SavedPct": saved_pct(len(slb1.artifact), raw_bytes),
        "modelSlb1Bytes": len(modeled.artifact),
        "modelSlb1SavedPct": saved_pct(len(modeled.artifact), raw_bytes),
        "selectedModel": modeled.metadata["selectedModel"],
        "modelVsBestGeneralPct": size_delta_pct(len(modeled.artifact), best_general_bytes),
        "verification": verification,
    }
    if options.include_digest:
        row["sha256"] = digest
    return row


def build_results(options: RealDataOptions) -> dict[str, Any]:
    files, skipped = discover_files(options)
    compressors = available_compressors()
    rows = [benchmark_file(path, options, compressors) for path in files]
    total_raw = sum(int(row["rawBytes"]) for row in rows)
    total_best_general = sum(int(row["bestGeneralBytes"]) for row in rows)
    total_slb1 = sum(int(row["slb1Bytes"]) for row in rows)
    total_model = sum(int(row["modelSlb1Bytes"]) for row in rows)
    return {
        "schemaVersion": 1,
        "benchmark": "star-light-codec-real-data",
        "resultLicense": "CC0-1.0",
        "scriptLicense": "Apache-2.0",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "maxPasses": options.max_passes,
        "maxFileBytes": options.max_file_bytes,
        "repetitions": options.repetitions,
        "verified": options.verify,
        "compressors": [name for name, _ in compressors],
        "note": "Local real-data benchmark. No raw file contents are embedded in this result.",
        "files": rows,
        "skipped": skipped,
        "summary": {
            "fileCount": len(rows),
            "skippedCount": len(skipped),
            "rawBytes": total_raw,
            "bestGeneralBytes": total_best_general,
            "bestGeneralSavedPct": saved_pct(total_best_general, total_raw),
            "slb1Bytes": total_slb1,
            "slb1SavedPct": saved_pct(total_slb1, total_raw),
            "modelSlb1Bytes": total_model,
            "modelSlb1SavedPct": saved_pct(total_model, total_raw),
            "modelVsBestGeneralPct": size_delta_pct(total_model, total_best_general),
        },
    }


def escape_md(value: str) -> str:
    return value.replace("|", "\\|")


def markdown_table(results: dict[str, Any]) -> str:
    summary = results["summary"]
    lines = [
        f"Files: {summary['fileCount']}  Skipped: {summary['skippedCount']}  "
        f"Raw: {format_bytes(summary['rawBytes'])} bytes",
        "",
        "| file | ext | raw | best general | best bytes | best saved | model SLB1 | model | model saved | model vs best | verify |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for row in results["files"]:
        lines.append(
            "| {path} | {ext} | {raw} | {best} | {best_bytes} | {best_saved:.2f}% | "
            "{model_bytes} | {model} | {model_saved:.2f}% | {model_vs_best:+.2f}% | {verify} |".format(
                path=escape_md(str(row["path"])),
                ext=escape_md(str(row["extension"] or "-")),
                raw=format_bytes(int(row["rawBytes"])),
                best=escape_md(str(row["bestGeneralCodec"])),
                best_bytes=format_bytes(int(row["bestGeneralBytes"])),
                best_saved=float(row["bestGeneralSavedPct"]),
                model_bytes=format_bytes(int(row["modelSlb1Bytes"])),
                model=escape_md(str(row["selectedModel"])),
                model_saved=float(row["modelSlb1SavedPct"]),
                model_vs_best=float(row["modelVsBestGeneralPct"]),
                verify=escape_md(str(row["verification"])),
            )
        )
    lines.extend(
        [
            "",
            "| aggregate | bytes | saved | model vs best general |",
            "| --- | ---: | ---: | ---: |",
            "| best general | {bytes} | {saved:.2f}% | - |".format(
                bytes=format_bytes(int(summary["bestGeneralBytes"])),
                saved=float(summary["bestGeneralSavedPct"]),
            ),
            "| SLB1 | {bytes} | {saved:.2f}% | {delta:+.2f}% |".format(
                bytes=format_bytes(int(summary["slb1Bytes"])),
                saved=float(summary["slb1SavedPct"]),
                delta=size_delta_pct(int(summary["slb1Bytes"]), int(summary["bestGeneralBytes"])),
            ),
            "| model SLB1 | {bytes} | {saved:.2f}% | {delta:+.2f}% |".format(
                bytes=format_bytes(int(summary["modelSlb1Bytes"])),
                saved=float(summary["modelSlb1SavedPct"]),
                delta=float(summary["modelVsBestGeneralPct"]),
            ),
        ]
    )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Star Light Codec real-data compression benchmarks.")
    parser.add_argument("paths", nargs="+", help="files or directories to benchmark")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--output", help="write benchmark output to a file instead of stdout")
    parser.add_argument("--label-root", help="base directory for relative path labels")
    parser.add_argument("--no-recursive", action="store_true", help="do not recurse into input directories")
    parser.add_argument("--include-hidden", action="store_true", help="include dotfiles and files in dot directories")
    parser.add_argument("--include-digest", action="store_true", help="include SHA-256 digests in JSON output")
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
    parser.add_argument("--max-file-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-passes", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=1, help="median timing repetitions; use 0 to disable")
    parser.add_argument("--skip-verify", action="store_true", help="skip SLB1 decode verification")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exclude_globs = [] if args.no_default_excludes else list(DEFAULT_EXCLUDE_GLOBS)
    exclude_globs.extend(args.exclude_globs or [])
    options = RealDataOptions(
        paths=[Path(path) for path in args.paths],
        label_root=Path(args.label_root) if args.label_root else None,
        recursive=not args.no_recursive,
        include_hidden=args.include_hidden,
        include_digest=args.include_digest,
        max_file_bytes=args.max_file_bytes,
        limit=args.limit,
        exclude_globs=exclude_globs,
        max_passes=args.max_passes,
        repetitions=args.repetitions,
        verify=not args.skip_verify,
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
