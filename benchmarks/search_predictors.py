from __future__ import annotations

# SPDX-License-Identifier: Apache-2.0

import argparse
import base64
import binascii
import bz2
import fnmatch
import gzip
import json
import lzma
import platform
import re
import statistics
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
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
STATE_KIND = "star-light-predictor-search-state"
COMPRESSORS = ("gzip", "zlib", "bz2", "lzma")
OFFSETS = (1, 2, 4, 8, 16)
FUTURE_OFFSET_LIMIT = 64
SEGMENTED_ORACLE_BLOCK_SIZES = (1024,)
SEGMENTED_STREAM_ORACLE_BLOCK_SIZES = (512, 1024, 2048, 4096)
SEGMENTED_ORACLE_TRANSFORMS: tuple[tuple[str, dict[str, int]], ...] = (
    ("identity", {}),
    ("delta-prev", {"offset": 1}),
    ("xor-prev", {"offset": 1}),
)
SEGMENTED_STREAM_ORACLE_TRANSFORM = "segmented-stream-oracle"
SEGMENTED_STREAM_VAR_ORACLE_TRANSFORM = "segmented-stream-var-oracle"
SEGMENTED_STREAM_BOUNDARY_ORACLE_TRANSFORM = "segmented-stream-boundary-oracle"
SEGMENTED_STREAM_4096_PROJECT_TEXT_GATED_CANDIDATE = "segmented-stream-oracle-4096-project-text-gated+zlib"
SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_GATED_CANDIDATE = (
    "segmented-stream-oracle-1024-4096-project-text-gated+zlib"
)
SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_BENEFIT_GATED_CANDIDATE = (
    "segmented-stream-oracle-1024-4096-project-text-benefit-gated+zlib"
)
SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE = (
    "segmented-stream-oracle-1024-4096-project-text-long-token-intern-benefit-gated+zlib"
)
PROJECT_TEXT_GATE_ID = "project-text"
PROJECT_TEXT_GATE_NAME = "project-text-code-ish-v1"
SEGMENTED_STREAM_CHOICE_METADATA_BYTES = 1
SEGMENTED_STREAM_VAR_SEGMENT_METADATA_BYTES = 2
SEGMENTED_STREAM_BOUNDARY_SEGMENT_METADATA_BYTES = 3
SEGMENTED_STREAM_SELECTOR_BLOCK_SIZES = (1024, 2048, 4096)
SEGMENTED_STREAM_VAR_LENGTHS = (512, 1024, 2048, 4096)
SEGMENTED_STREAM_BOUNDARY_SEARCH_RADIUS = 128
PROJECT_TEXT_GATE_ROOTS = frozenset({"docs", "src", "tests"})
PROJECT_TEXT_GATE_TOP_LEVEL_NAMES = frozenset({"benchmarks", "changelog", "licensing", "readme", "security"})
PROJECT_TEXT_GATE_EXTENSIONS = frozenset(
    {
        "",
        ".cfg",
        ".css",
        ".csv",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".ps1",
        ".py",
        ".rst",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
PROJECT_TEXT_GATE_EXCLUDED_COMPONENTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        ".cache",
        "benchmarks",
        "build",
        "cache",
        "generated",
        "dist",
        "htmlcov",
        "node_modules",
        "temp",
        "tmp",
        "venv",
    }
)
PROJECT_TEXT_GATE_EXCLUDED_EXTENSIONS = frozenset(
    {
        ".7z",
        ".bin",
        ".bmp",
        ".bz2",
        ".dll",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jpg",
        ".jpeg",
        ".lzma",
        ".pdf",
        ".png",
        ".pyc",
        ".pyd",
        ".pyo",
        ".so",
        ".webp",
        ".xz",
        ".zip",
    }
)
LONG_TOKEN_INTERN_MAGIC = b"SLTI1"
LONG_TOKEN_INTERN_MIN_BYTES = 24
LONG_TOKEN_INTERN_MAX_TOKENS = 1024
LONG_TOKEN_INTERN_TOKEN_RE = re.compile(rb"[A-Za-z0-9_./:\-]{24,}")


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
    candidate_filters: list[str] | None = None
    state_input: Path | None = None
    state_output: Path | None = None


class CandidateFilterError(ValueError):
    pass


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


def long_token_intern_candidates(data: bytes) -> tuple[list[bytes], dict[bytes, int]]:
    counts: dict[bytes, int] = {}
    first_offsets: dict[bytes, int] = {}
    for match in LONG_TOKEN_INTERN_TOKEN_RE.finditer(data):
        token = match.group(0)
        counts[token] = counts.get(token, 0) + 1
        first_offsets.setdefault(token, match.start())
    candidates: list[bytes] = []
    for token, count in counts.items():
        if count < 2 or len(token) > 0xFFFF:
            continue
        table_cost = 2 + len(token)
        reference_cost = count * 3
        raw_cost = count * len(token)
        if raw_cost <= table_cost + reference_cost + 8:
            continue
        candidates.append(token)
    candidates.sort(key=lambda token: (first_offsets[token], token))
    return candidates[:LONG_TOKEN_INTERN_MAX_TOKENS], counts


def append_long_token_literal(output: bytearray, literal: bytes) -> None:
    cursor = 0
    while cursor < len(literal):
        chunk = literal[cursor : cursor + 0xFFFF]
        output.append(0)
        output.extend(struct.pack("<H", len(chunk)))
        output.extend(chunk)
        cursor += len(chunk)


def transform_long_token_intern(data: bytes) -> tuple[bytes, dict[str, int]]:
    tokens, counts = long_token_intern_candidates(data)
    token_indexes = {token: index for index, token in enumerate(tokens)}
    output = bytearray(LONG_TOKEN_INTERN_MAGIC)
    output.extend(struct.pack("<H", len(tokens)))
    for token in tokens:
        output.extend(struct.pack("<H", len(token)))
        output.extend(token)

    cursor = 0
    interned_occurrences = 0
    for match in LONG_TOKEN_INTERN_TOKEN_RE.finditer(data):
        token = match.group(0)
        token_index = token_indexes.get(token)
        if token_index is None:
            continue
        append_long_token_literal(output, data[cursor : match.start()])
        output.append(1)
        output.extend(struct.pack("<H", token_index))
        cursor = match.end()
        interned_occurrences += 1
    append_long_token_literal(output, data[cursor:])
    metadata = {
        "internedTokenCount": len(tokens),
        "internedOccurrenceCount": interned_occurrences,
        "internedTokenBytes": sum(len(token) for token in tokens),
        "candidateTokenCount": sum(1 for token, count in counts.items() if count >= 2),
    }
    return bytes(output), metadata


def inverse_long_token_intern(transformed: bytes) -> bytes:
    if not transformed.startswith(LONG_TOKEN_INTERN_MAGIC):
        raise ValueError("Long-token intern transform magic mismatch.")
    cursor = len(LONG_TOKEN_INTERN_MAGIC)
    if cursor + 2 > len(transformed):
        raise ValueError("Long-token intern transform token count missing.")
    token_count = struct.unpack("<H", transformed[cursor : cursor + 2])[0]
    cursor += 2
    tokens: list[bytes] = []
    for _index in range(token_count):
        if cursor + 2 > len(transformed):
            raise ValueError("Long-token intern transform token length missing.")
        token_len = struct.unpack("<H", transformed[cursor : cursor + 2])[0]
        cursor += 2
        token = transformed[cursor : cursor + token_len]
        if len(token) != token_len:
            raise ValueError("Long-token intern transform token length mismatch.")
        tokens.append(token)
        cursor += token_len

    output = bytearray()
    while cursor < len(transformed):
        tag = transformed[cursor]
        cursor += 1
        if tag == 0:
            if cursor + 2 > len(transformed):
                raise ValueError("Long-token intern transform literal length missing.")
            literal_len = struct.unpack("<H", transformed[cursor : cursor + 2])[0]
            cursor += 2
            literal = transformed[cursor : cursor + literal_len]
            if len(literal) != literal_len:
                raise ValueError("Long-token intern transform literal length mismatch.")
            output.extend(literal)
            cursor += literal_len
        elif tag == 1:
            if cursor + 2 > len(transformed):
                raise ValueError("Long-token intern transform reference missing.")
            token_index = struct.unpack("<H", transformed[cursor : cursor + 2])[0]
            cursor += 2
            if token_index >= len(tokens):
                raise ValueError("Long-token intern transform reference out of range.")
            output.extend(tokens[token_index])
        else:
            raise ValueError("Long-token intern transform unknown chunk tag.")
    return bytes(output)


TRANSFORMS: dict[str, tuple[Callable[[bytes, dict[str, int]], bytes], Callable[[bytes, dict[str, int]], bytes]]] = {
    "identity": (transform_identity, inverse_identity),
    "delta-prev": (transform_delta_prev, inverse_delta_prev),
    "xor-prev": (transform_xor_prev, inverse_xor_prev),
    "delta-avg2": (transform_delta_avg2, inverse_delta_avg2),
}


def compressor_order(stats: dict[str, dict[str, float]]) -> list[str]:
    return sorted(COMPRESSORS, key=lambda item: (-stat_average(stats, f"compressor:{item}"), item))


def future_offset_scores(corpus: list[dict[str, Any]]) -> list[tuple[float, int]]:
    scores: list[tuple[float, int]] = []
    for offset in range(1, FUTURE_OFFSET_LIMIT + 1):
        if offset in OFFSETS:
            continue
        residual_total = 0
        compared = 0
        for row in corpus[:16]:
            data = sample_bytes(bytes(row["data"]))
            if len(data) <= offset:
                continue
            for index in range(offset, len(data)):
                residual_total += abs(data[index] - data[index - offset])
            compared += len(data) - offset
        if compared:
            scores.append((residual_total / compared, offset))
    return sorted(scores, key=lambda item: (item[0], item[1]))


def learned_future_offsets(stats: dict[str, dict[str, float]]) -> list[int]:
    offsets: set[int] = set()
    for transform in ("delta-prev", "xor-prev"):
        for offset in range(1, FUTURE_OFFSET_LIMIT + 1):
            if stat_average(stats, f"offset:{transform}:{offset}") <= 0:
                continue
            for candidate in (offset - 1, offset + 1, offset * 2):
                if 1 <= candidate <= FUTURE_OFFSET_LIMIT and candidate not in OFFSETS:
                    offsets.add(candidate)
    return sorted(offsets)


def future_candidates(
    limit: int,
    corpus: list[dict[str, Any]] | None,
    stats: dict[str, dict[str, float]] | None,
) -> list[Candidate]:
    if limit <= 0:
        return []
    model_stats = stats or {}
    inferred_offsets = [offset for _score, offset in future_offset_scores(corpus or [])[:8]]
    offsets = list(dict.fromkeys(learned_future_offsets(model_stats) + inferred_offsets))
    candidates: list[Candidate] = []
    for offset in offsets:
        for transform in ("delta-prev", "xor-prev"):
            for compressor in compressor_order(model_stats):
                candidates.append(
                    Candidate(
                        f"future-{transform}-{offset}+{compressor}",
                        transform,
                        compressor,
                        {"offset": offset},
                    )
                )
                if len(candidates) >= limit:
                    return candidates
    return candidates


def build_candidates(
    limit: int,
    corpus: list[dict[str, Any]] | None = None,
    stats: dict[str, dict[str, float]] | None = None,
) -> list[Candidate]:
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
    if len(candidates) < limit:
        candidates.extend(future_candidates(limit - len(candidates), corpus, stats))
    for block_size in SEGMENTED_ORACLE_BLOCK_SIZES:
        if len(candidates) >= limit and limit < 64:
            break
        # Keep the original 64-candidate future-search surface intact, then add
        # this research probe as a soft-limit extra at default-or-larger limits.
        candidates.append(
            Candidate(
                f"segmented-oracle-{block_size}+zlib",
                "segmented-oracle",
                "zlib",
                {"blockSize": block_size},
            )
        )
    for block_size in SEGMENTED_STREAM_ORACLE_BLOCK_SIZES:
        if len(candidates) >= limit and limit < 64:
            break
        candidates.append(
            Candidate(
                f"segmented-stream-oracle-{block_size}+zlib",
                SEGMENTED_STREAM_ORACLE_TRANSFORM,
                "zlib",
                {"blockSize": block_size},
            )
        )
    candidates.append(
        Candidate(
            SEGMENTED_STREAM_4096_PROJECT_TEXT_GATED_CANDIDATE,
            SEGMENTED_STREAM_ORACLE_TRANSFORM,
            "zlib",
            {"blockSize": 4096},
        )
    )
    candidates.append(
        Candidate(
            SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_GATED_CANDIDATE,
            SEGMENTED_STREAM_ORACLE_TRANSFORM,
            "zlib",
            {"minBlockSize": SEGMENTED_STREAM_SELECTOR_BLOCK_SIZES[0], "maxBlockSize": SEGMENTED_STREAM_SELECTOR_BLOCK_SIZES[-1]},
        )
    )
    candidates.append(
        Candidate(
            SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_BENEFIT_GATED_CANDIDATE,
            SEGMENTED_STREAM_ORACLE_TRANSFORM,
            "zlib",
            {"minBlockSize": SEGMENTED_STREAM_SELECTOR_BLOCK_SIZES[0], "maxBlockSize": SEGMENTED_STREAM_SELECTOR_BLOCK_SIZES[-1]},
        )
    )
    candidates.append(
        Candidate(
            SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE,
            SEGMENTED_STREAM_ORACLE_TRANSFORM,
            "zlib",
            {"minBlockSize": SEGMENTED_STREAM_SELECTOR_BLOCK_SIZES[0], "maxBlockSize": SEGMENTED_STREAM_SELECTOR_BLOCK_SIZES[-1]},
        )
    )
    candidates.append(
        Candidate(
            "segmented-stream-var-oracle-512-4096+zlib",
            SEGMENTED_STREAM_VAR_ORACLE_TRANSFORM,
            "zlib",
            {"minSegmentBytes": SEGMENTED_STREAM_VAR_LENGTHS[0], "maxSegmentBytes": SEGMENTED_STREAM_VAR_LENGTHS[-1]},
        )
    )
    candidates.append(
        Candidate(
            "segmented-stream-boundary-oracle-512-4096+zlib",
            SEGMENTED_STREAM_BOUNDARY_ORACLE_TRANSFORM,
            "zlib",
            {"minSegmentBytes": SEGMENTED_STREAM_VAR_LENGTHS[0], "maxSegmentBytes": SEGMENTED_STREAM_VAR_LENGTHS[-1]},
        )
    )
    if limit >= 64:
        return candidates
    return candidates[:limit]


def filter_candidates(candidates: list[Candidate], patterns: list[str] | None) -> list[Candidate]:
    if not patterns:
        return candidates
    filtered = [
        candidate
        for candidate in candidates
        if any(fnmatch.fnmatchcase(candidate.name, pattern) for pattern in patterns)
    ]
    if not filtered:
        joined = ", ".join(patterns)
        raise CandidateFilterError(f"--candidate-filter matched no candidates: {joined}")
    return filtered


def project_text_gate_applies(row: dict[str, Any]) -> bool:
    path_text = str(row.get("path", "")).replace("\\", "/").strip("/")
    if not path_text:
        return False
    parts = [part for part in path_text.split("/") if part]
    lower_parts = [part.lower() for part in parts]
    if any(part in PROJECT_TEXT_GATE_EXCLUDED_COMPONENTS for part in lower_parts):
        return False

    extension = str(row.get("extension") or Path(path_text).suffix).lower()
    if extension in PROJECT_TEXT_GATE_EXCLUDED_EXTENSIONS:
        return False
    if extension not in PROJECT_TEXT_GATE_EXTENSIONS:
        return False

    top_level = lower_parts[0]
    if top_level in PROJECT_TEXT_GATE_ROOTS:
        return True
    if len(lower_parts) == 1:
        stem = lower_parts[0].rsplit(".", 1)[0]
        return stem in PROJECT_TEXT_GATE_TOP_LEVEL_NAMES
    return False


def candidate_gate(candidate: Candidate) -> dict[str, str] | None:
    if candidate.name not in {
        SEGMENTED_STREAM_4096_PROJECT_TEXT_GATED_CANDIDATE,
        SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_GATED_CANDIDATE,
        SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_BENEFIT_GATED_CANDIDATE,
        SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE,
    }:
        return None
    return {"id": PROJECT_TEXT_GATE_ID, "name": PROJECT_TEXT_GATE_NAME}


def sample_bytes(data: bytes, size: int = 4096) -> bytes:
    return data[:size] if len(data) > size else data


def fast_compressed_size(data: bytes) -> int:
    return len(zlib.compress(data, level=1))


def candidate_prior(candidate: Candidate, corpus: list[dict[str, Any]]) -> float:
    if candidate.transform == "identity":
        return -5.0
    if candidate.transform in {
        "segmented-oracle",
        SEGMENTED_STREAM_ORACLE_TRANSFORM,
        SEGMENTED_STREAM_VAR_ORACLE_TRANSFORM,
        SEGMENTED_STREAM_BOUNDARY_ORACLE_TRANSFORM,
    }:
        return 1.0
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


def normalize_stats(raw: Any) -> dict[str, dict[str, float]]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, float]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        try:
            count = float(value.get("count", 0.0))
            score = float(value.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        normalized[key] = {"count": count, "score": score}
    return normalized


def summarize_stats(stats: dict[str, dict[str, float]]) -> dict[str, dict[str, float | int]]:
    return {
        key: {
            "count": int(value["count"]),
            "score": round(float(value["score"]), 6),
            "averageReward": round(value["score"] / value["count"], 3) if value["count"] else 0.0,
        }
        for key, value in sorted(stats.items())
    }


def load_state(path: Path | None) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    if path is None:
        return {}, {"loaded": False, "reason": "not-requested"}
    state_path = path.resolve()
    state_doc = json.loads(state_path.read_text(encoding="utf-8"))
    if state_doc.get("kind") != STATE_KIND:
        raise ValueError("Unsupported predictor search state file.")
    stats = normalize_stats(state_doc.get("modelState", {}))
    return stats, {
        "loaded": True,
        "keys": len(stats),
        "runCount": int(state_doc.get("runCount", 0)),
    }


def write_state(
    path: Path,
    stats: dict[str, dict[str, float]],
    results: dict[str, Any],
    input_summary: dict[str, Any],
) -> None:
    previous_run_count = int(input_summary.get("runCount", 0)) if input_summary.get("loaded") else 0
    state_doc = {
        "schemaVersion": 1,
        "kind": STATE_KIND,
        "updatedAtUtc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "runCount": previous_run_count + 1,
        "modelState": summarize_stats(stats),
        "lastRun": {
            "elapsedSeconds": results["elapsedSeconds"],
            "searchMode": results["searchMode"],
            "stoppedReason": results["stoppedReason"],
            "fileCount": results["fileCount"],
            "evaluatedCandidateCount": results["evaluatedCandidateCount"],
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    if candidate.transform == "segmented-oracle":
        return encode_segmented_oracle_candidate(data, candidate)
    if candidate.transform == SEGMENTED_STREAM_ORACLE_TRANSFORM:
        return encode_segmented_stream_oracle_candidate(data, candidate)
    if candidate.transform == SEGMENTED_STREAM_VAR_ORACLE_TRANSFORM:
        return encode_segmented_stream_var_oracle_candidate(data, candidate)
    if candidate.transform == SEGMENTED_STREAM_BOUNDARY_ORACLE_TRANSFORM:
        return encode_segmented_stream_boundary_oracle_candidate(data, candidate)
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


def segmented_block_candidates(block: bytes, compressor: str) -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = []
    for transform_name, params in SEGMENTED_ORACLE_TRANSFORMS:
        transform, _inverse = TRANSFORMS[transform_name]
        transformed = transform(block, params)
        payload = compress_payload(transformed, compressor)
        choices.append(
            {
                "transform": transform_name,
                "params": params,
                "transformed": transformed,
                "payload": payload,
            }
        )
    return choices


def segmented_choice_metadata_cost(choice: dict[str, Any]) -> int:
    metadata = {
        "transform": str(choice["transform"]),
        "params": dict(choice["params"]),
    }
    return len(json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def choose_segmented_block_candidate(block: bytes, compressor: str) -> dict[str, Any]:
    choices = segmented_block_candidates(block, compressor)
    return min(
        choices,
        key=lambda choice: (
            len(bytes(choice["payload"])) + segmented_choice_metadata_cost(choice),
            len(bytes(choice["payload"])),
            segmented_choice_metadata_cost(choice),
            str(choice["transform"]),
            json.dumps(choice["params"], sort_keys=True),
        ),
    )


def segmented_stream_codebook() -> list[dict[str, Any]]:
    return [{"t": transform_name, "p": params} for transform_name, params in SEGMENTED_ORACLE_TRANSFORMS]


def choose_segmented_stream_block_candidate(block: bytes, compressor: str) -> tuple[int, bytes]:
    choices = segmented_block_candidates(block, compressor)
    best = min(
        enumerate(choices),
        key=lambda item: (
            len(bytes(item[1]["payload"])) + SEGMENTED_STREAM_CHOICE_METADATA_BYTES,
            len(bytes(item[1]["payload"])),
            item[0],
        ),
    )
    return best[0], bytes(best[1]["transformed"])


def segmented_stream_var_allowed_lengths(candidate: Candidate) -> tuple[int, ...]:
    minimum = int(candidate.params.get("minSegmentBytes", SEGMENTED_STREAM_VAR_LENGTHS[0]))
    maximum = int(candidate.params.get("maxSegmentBytes", SEGMENTED_STREAM_VAR_LENGTHS[-1]))
    lengths = tuple(length for length in SEGMENTED_STREAM_VAR_LENGTHS if minimum <= length <= maximum)
    if not lengths:
        raise ValueError("Segmented stream variable oracle must have at least one allowed segment length.")
    return lengths


def segmented_stream_var_possible_lengths(remaining: int, allowed_lengths: tuple[int, ...]) -> list[int]:
    lengths = [length for length in allowed_lengths if length <= remaining]
    maximum = allowed_lengths[-1]
    if 0 < remaining <= maximum and remaining not in lengths:
        lengths.append(remaining)
    return sorted(lengths)


def choose_segmented_stream_var_blocks(
    data: bytes,
    compressor: str,
    allowed_lengths: tuple[int, ...],
) -> list[tuple[int, int, bytes]]:
    raw_bytes = len(data)
    if raw_bytes == 0:
        return []
    scores: dict[int, tuple[int, int, int, int]] = {0: (0, 0, 0, 0)}
    previous: dict[int, tuple[int, int, int, bytes]] = {}
    for start in range(raw_bytes):
        if start not in scores:
            continue
        score = scores[start]
        remaining = raw_bytes - start
        for length in segmented_stream_var_possible_lengths(remaining, allowed_lengths):
            end = start + length
            code, transformed = choose_segmented_stream_block_candidate(data[start:end], compressor)
            local_cost = len(compress_payload(transformed, compressor)) + SEGMENTED_STREAM_VAR_SEGMENT_METADATA_BYTES
            candidate_score = (score[0] + local_cost, score[1] + 1, length, code)
            if end not in scores or candidate_score < scores[end]:
                scores[end] = candidate_score
                previous[end] = (start, length, code, transformed)
    if raw_bytes not in previous:
        raise ValueError("Segmented stream variable oracle could not cover input.")
    blocks: list[tuple[int, int, bytes]] = []
    cursor = raw_bytes
    while cursor:
        start, length, code, transformed = previous[cursor]
        blocks.append((length, code, transformed))
        cursor = start
    blocks.reverse()
    return blocks


def segmented_stream_boundary_limits(candidate: Candidate) -> tuple[int, int]:
    minimum = int(candidate.params.get("minSegmentBytes", SEGMENTED_STREAM_VAR_LENGTHS[0]))
    maximum = int(candidate.params.get("maxSegmentBytes", SEGMENTED_STREAM_VAR_LENGTHS[-1]))
    if minimum <= 0 or maximum < minimum:
        raise ValueError("Segmented stream boundary oracle has malformed segment limits.")
    return minimum, maximum


def segmented_stream_boundary_hint_scores(data: bytes) -> tuple[dict[int, int], dict[str, int]]:
    scores: dict[int, int] = {}
    counts = {"newline": 0, "blankLine": 0, "punctuation": 0, "indentTransition": 0}

    def add(offset: int, score: int, kind: str) -> None:
        if 0 < offset < len(data):
            scores[offset] = max(scores.get(offset, 0), score)
            counts[kind] += 1

    punctuation = set(b"{}[](),:")
    for index, byte in enumerate(data):
        offset = index + 1
        if byte == 0x0A:
            add(offset, 2, "newline")
            if index > 0 and data[index - 1] == 0x0A:
                add(offset, 5, "blankLine")
        elif byte in punctuation:
            add(offset, 1, "punctuation")

    previous_indent: int | None = None
    line_start = 0
    while line_start < len(data):
        line_end = data.find(b"\n", line_start)
        if line_end < 0:
            line_end = len(data)
            next_line_start = len(data)
        else:
            next_line_start = line_end + 1
        content_end = line_end - 1 if line_end > line_start and data[line_end - 1] == 0x0D else line_end
        cursor = line_start
        while cursor < content_end and data[cursor] in (0x20, 0x09):
            cursor += 1
        if cursor < content_end:
            indent = cursor - line_start
            if previous_indent is not None and indent != previous_indent:
                add(line_start, 4, "indentTransition")
            previous_indent = indent
        line_start = next_line_start

    return scores, counts


def segmented_stream_boundary_offsets(
    data: bytes,
    minimum: int = SEGMENTED_STREAM_VAR_LENGTHS[0],
    maximum: int = SEGMENTED_STREAM_VAR_LENGTHS[-1],
) -> tuple[list[int], dict[str, Any]]:
    raw_bytes = len(data)
    if raw_bytes == 0:
        return [0], {
            "boundaryCandidateCount": 1,
            "boundaryHintCount": 0,
            "boundaryStructuralSelectedCount": 0,
            "boundaryFallbackCount": 0,
            "boundarySearchRadiusBytes": SEGMENTED_STREAM_BOUNDARY_SEARCH_RADIUS,
            "boundaryHintKinds": {"newline": 0, "blankLine": 0, "punctuation": 0, "indentTransition": 0},
        }
    hint_scores, hint_counts = segmented_stream_boundary_hint_scores(data)
    offsets = {0, raw_bytes}
    fallback_offsets = set()
    for step in SEGMENTED_STREAM_VAR_LENGTHS:
        if step < minimum or step > maximum:
            continue
        for offset in range(step, raw_bytes, step):
            fallback_offsets.add(offset)
    offsets.update(fallback_offsets)

    selected_by_target: dict[int, tuple[int, int, int]] = {}
    for offset, score in hint_scores.items():
        lower_target = (offset // minimum) * minimum
        upper_target = lower_target + minimum
        for target in {lower_target, upper_target}:
            if target <= 0 or target >= raw_bytes:
                continue
            distance = abs(offset - target)
            if distance > SEGMENTED_STREAM_BOUNDARY_SEARCH_RADIUS:
                continue
            selected = (score, -distance, -offset)
            if target not in selected_by_target or selected > selected_by_target[target]:
                selected_by_target[target] = selected
    structural_offsets = {-selected[2] for selected in selected_by_target.values()}
    offsets.update(structural_offsets)

    boundaries = sorted(offsets)
    stats = {
        "boundaryCandidateCount": len(boundaries),
        "boundaryHintCount": len(hint_scores),
        "boundaryStructuralSelectedCount": len(structural_offsets),
        "boundaryFallbackCount": len(fallback_offsets),
        "boundarySearchRadiusBytes": SEGMENTED_STREAM_BOUNDARY_SEARCH_RADIUS,
        "boundaryHintKinds": hint_counts,
    }
    return boundaries, stats


def choose_segmented_stream_boundary_blocks(
    data: bytes,
    compressor: str,
    minimum: int,
    maximum: int,
) -> tuple[list[tuple[int, int, bytes]], dict[str, Any]]:
    raw_bytes = len(data)
    if raw_bytes == 0:
        boundaries, stats = segmented_stream_boundary_offsets(data, minimum, maximum)
        return [], stats | {"boundaryReachableCount": len(boundaries)}
    boundaries, stats = segmented_stream_boundary_offsets(data, minimum, maximum)
    scores: dict[int, tuple[int, int, int, int]] = {0: (0, 0, 0, 0)}
    previous: dict[int, tuple[int, int, int, bytes]] = {}
    for start_index, start in enumerate(boundaries):
        if start not in scores:
            continue
        score = scores[start]
        for end in boundaries[start_index + 1 :]:
            length = end - start
            if length > maximum:
                break
            if end != raw_bytes and length < minimum:
                continue
            code, transformed = choose_segmented_stream_block_candidate(data[start:end], compressor)
            local_cost = len(compress_payload(transformed, compressor)) + SEGMENTED_STREAM_BOUNDARY_SEGMENT_METADATA_BYTES
            candidate_score = (score[0] + local_cost, score[1] + 1, length, code)
            if end not in scores or candidate_score < scores[end]:
                scores[end] = candidate_score
                previous[end] = (start, length, code, transformed)
    if raw_bytes not in previous:
        raise ValueError("Segmented stream boundary oracle could not cover input.")
    blocks: list[tuple[int, int, bytes]] = []
    cursor = raw_bytes
    while cursor:
        start, length, code, transformed = previous[cursor]
        blocks.append((length, code, transformed))
        cursor = start
    blocks.reverse()
    stats["boundaryReachableCount"] = len(scores)
    return blocks, stats


def encode_segmented_stream_boundary_oracle_candidate(data: bytes, candidate: Candidate) -> bytes:
    minimum, maximum = segmented_stream_boundary_limits(candidate)
    blocks, boundary_stats = choose_segmented_stream_boundary_blocks(data, candidate.compressor, minimum, maximum)
    selected_lengths = [length for length, _code, _transformed in blocks]
    if any(length > 0xFFFF for length in selected_lengths):
        raise ValueError("Segmented stream boundary oracle segment length is too large.")
    length_codes = b"".join(struct.pack("<H", length) for length in selected_lengths)
    transform_codes = bytes(code for _length, code, _transformed in blocks)
    transformed_stream = b"".join(transformed for _length, _code, transformed in blocks)
    payload = compress_payload(transformed_stream, candidate.compressor)
    header = {
        "schemaVersion": 1,
        "kind": "star-light-predictor-search",
        "candidate": candidate.name,
        "transform": candidate.transform,
        "compressor": candidate.compressor,
        "params": candidate.params,
        "rawBytes": len(data),
        "transformedBytes": len(transformed_stream),
        "payloadBytes": len(payload),
        "inputDigest": sha256_digest(data),
        "payloadDigest": sha256_digest(payload),
        "minSegmentBytes": minimum,
        "maxSegmentBytes": maximum,
        "segmentCount": len(blocks),
        "segmentChoiceMethod": "structural-boundary/local-heuristic-compressed-size-plus-code-bytes",
        "segmentLengthCodeFormat": "uint16-le",
        "segmentLengthCodes": base64.b64encode(length_codes).decode("ascii"),
        "segmentTransformCodebook": segmented_stream_codebook(),
        "segmentTransformCodes": base64.b64encode(transform_codes).decode("ascii"),
        "boundaryPlanner": boundary_stats,
    }
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return SEARCH_MAGIC + struct.pack("<IQ", len(header_bytes), len(payload)) + header_bytes + payload


def encode_segmented_stream_var_oracle_candidate(data: bytes, candidate: Candidate) -> bytes:
    allowed_lengths = segmented_stream_var_allowed_lengths(candidate)
    blocks = choose_segmented_stream_var_blocks(data, candidate.compressor, allowed_lengths)
    selected_lengths = [length for length, _code, _transformed in blocks]
    length_codebook = sorted(set(selected_lengths))
    if len(length_codebook) > 256:
        raise ValueError("Segmented stream variable oracle length codebook is too large.")
    length_indexes = {length: index for index, length in enumerate(length_codebook)}
    length_codes = bytes(length_indexes[length] for length in selected_lengths)
    transform_codes = bytes(code for _length, code, _transformed in blocks)
    transformed_stream = b"".join(transformed for _length, _code, transformed in blocks)
    payload = compress_payload(transformed_stream, candidate.compressor)
    header = {
        "schemaVersion": 1,
        "kind": "star-light-predictor-search",
        "candidate": candidate.name,
        "transform": candidate.transform,
        "compressor": candidate.compressor,
        "params": candidate.params,
        "rawBytes": len(data),
        "transformedBytes": len(transformed_stream),
        "payloadBytes": len(payload),
        "inputDigest": sha256_digest(data),
        "payloadDigest": sha256_digest(payload),
        "allowedSegmentLengths": list(allowed_lengths),
        "segmentCount": len(blocks),
        "segmentChoiceMethod": "dynamic-programming-local-compressed-size-plus-code-bytes",
        "segmentLengthCodebook": length_codebook,
        "segmentLengthCodes": base64.b64encode(length_codes).decode("ascii"),
        "segmentTransformCodebook": segmented_stream_codebook(),
        "segmentTransformCodes": base64.b64encode(transform_codes).decode("ascii"),
    }
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return SEARCH_MAGIC + struct.pack("<IQ", len(header_bytes), len(payload)) + header_bytes + payload


def encode_segmented_stream_oracle_candidate(data: bytes, candidate: Candidate) -> bytes:
    block_size = int(candidate.params["blockSize"])
    if block_size <= 0:
        raise ValueError("Segmented stream oracle block size must be positive.")
    transformed_parts: list[bytes] = []
    transform_codes = bytearray()
    for offset in range(0, len(data), block_size):
        block = data[offset : offset + block_size]
        code, transformed = choose_segmented_stream_block_candidate(block, candidate.compressor)
        transform_codes.append(code)
        transformed_parts.append(transformed)
    transformed_stream = b"".join(transformed_parts)
    payload = compress_payload(transformed_stream, candidate.compressor)
    header = {
        "schemaVersion": 1,
        "kind": "star-light-predictor-search",
        "candidate": candidate.name,
        "transform": candidate.transform,
        "compressor": candidate.compressor,
        "params": candidate.params,
        "rawBytes": len(data),
        "transformedBytes": len(transformed_stream),
        "payloadBytes": len(payload),
        "inputDigest": sha256_digest(data),
        "payloadDigest": sha256_digest(payload),
        "blockSize": block_size,
        "blockCount": len(transform_codes),
        "blockChoiceMethod": "local-compressed-size-plus-code-byte",
        "blockTransformCodebook": segmented_stream_codebook(),
        "blockTransformCodes": base64.b64encode(bytes(transform_codes)).decode("ascii"),
    }
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return SEARCH_MAGIC + struct.pack("<IQ", len(header_bytes), len(payload)) + header_bytes + payload


def encode_segmented_oracle_candidate(data: bytes, candidate: Candidate) -> bytes:
    block_size = int(candidate.params["blockSize"])
    if block_size <= 0:
        raise ValueError("Segmented oracle block size must be positive.")
    blocks: list[dict[str, Any]] = []
    payload_parts: list[bytes] = []
    transformed_total = 0
    for offset in range(0, len(data), block_size):
        block = data[offset : offset + block_size]
        best = choose_segmented_block_candidate(block, candidate.compressor)
        payload = bytes(best["payload"])
        transformed = bytes(best["transformed"])
        transformed_total += len(transformed)
        payload_parts.append(payload)
        blocks.append(
            {
                "transform": str(best["transform"]),
                "params": dict(best["params"]),
                "rawBytes": len(block),
                "transformedBytes": len(transformed),
                "payloadBytes": len(payload),
                "payloadDigest": sha256_digest(payload),
            }
        )
    payload = b"".join(payload_parts)
    header = {
        "schemaVersion": 1,
        "kind": "star-light-predictor-search",
        "candidate": candidate.name,
        "transform": candidate.transform,
        "compressor": candidate.compressor,
        "params": candidate.params,
        "rawBytes": len(data),
        "transformedBytes": transformed_total,
        "payloadBytes": len(payload),
        "inputDigest": sha256_digest(data),
        "payloadDigest": sha256_digest(payload),
        "blocks": blocks,
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
    if transform_name == "segmented-oracle":
        data = decode_segmented_oracle_payload(payload, compressor, header)
        if len(data) != int(header["rawBytes"]):
            raise ValueError("Predictor artifact raw size mismatch.")
        if sha256_digest(data) != header.get("inputDigest"):
            raise ValueError("Predictor artifact input digest mismatch.")
        return data
    if transform_name == SEGMENTED_STREAM_ORACLE_TRANSFORM:
        data = decode_segmented_stream_oracle_payload(payload, compressor, header)
        if len(data) != int(header["rawBytes"]):
            raise ValueError("Predictor artifact raw size mismatch.")
        if sha256_digest(data) != header.get("inputDigest"):
            raise ValueError("Predictor artifact input digest mismatch.")
        return data
    if transform_name == SEGMENTED_STREAM_VAR_ORACLE_TRANSFORM:
        data = decode_segmented_stream_var_oracle_payload(payload, compressor, header)
        if len(data) != int(header["rawBytes"]):
            raise ValueError("Predictor artifact raw size mismatch.")
        if sha256_digest(data) != header.get("inputDigest"):
            raise ValueError("Predictor artifact input digest mismatch.")
        return data
    if transform_name == SEGMENTED_STREAM_BOUNDARY_ORACLE_TRANSFORM:
        data = decode_segmented_stream_boundary_oracle_payload(payload, compressor, header)
        if len(data) != int(header["rawBytes"]):
            raise ValueError("Predictor artifact raw size mismatch.")
        if sha256_digest(data) != header.get("inputDigest"):
            raise ValueError("Predictor artifact input digest mismatch.")
        return data
    transformed = decompress_payload(payload, compressor)
    _transform, inverse = TRANSFORMS[transform_name]
    data = inverse(transformed, params)
    if len(data) != int(header["rawBytes"]):
        raise ValueError("Predictor artifact raw size mismatch.")
    if sha256_digest(data) != header.get("inputDigest"):
        raise ValueError("Predictor artifact input digest mismatch.")
    return data


def decode_segmented_oracle_payload(payload: bytes, compressor: str, header: dict[str, Any]) -> bytes:
    output = bytearray()
    cursor = 0
    for block in header.get("blocks", []):
        payload_bytes = int(block["payloadBytes"])
        raw_bytes = int(block["rawBytes"])
        transformed_bytes = int(block["transformedBytes"])
        block_payload = payload[cursor : cursor + payload_bytes]
        if len(block_payload) != payload_bytes:
            raise ValueError("Segmented oracle block payload length mismatch.")
        if sha256_digest(block_payload) != block.get("payloadDigest"):
            raise ValueError("Segmented oracle block payload digest mismatch.")
        cursor += payload_bytes
        transform_name = str(block["transform"])
        params = {str(key): int(value) for key, value in dict(block.get("params", {})).items()}
        transformed = decompress_payload(block_payload, compressor)
        if len(transformed) != transformed_bytes:
            raise ValueError("Segmented oracle block transformed size mismatch.")
        _transform, inverse = TRANSFORMS[transform_name]
        restored = inverse(transformed, params)
        if len(restored) != raw_bytes:
            raise ValueError("Segmented oracle block raw size mismatch.")
        output.extend(restored)
    if cursor != len(payload):
        raise ValueError("Segmented oracle payload length mismatch.")
    return bytes(output)


def decode_segmented_stream_oracle_payload(payload: bytes, compressor: str, header: dict[str, Any]) -> bytes:
    transformed_stream = decompress_payload(payload, compressor)
    if len(transformed_stream) != int(header["transformedBytes"]):
        raise ValueError("Segmented stream oracle transformed size mismatch.")
    raw_bytes = int(header["rawBytes"])
    if len(transformed_stream) != raw_bytes:
        raise ValueError("Segmented stream oracle length-preserving stream size mismatch.")
    block_size = int(header["blockSize"])
    if block_size <= 0:
        raise ValueError("Segmented stream oracle block size must be positive.")
    block_count = int(header["blockCount"])
    expected_block_count = (raw_bytes + block_size - 1) // block_size
    if block_count != expected_block_count:
        raise ValueError("Segmented stream oracle non-canonical block count.")
    try:
        transform_codes = base64.b64decode(str(header["blockTransformCodes"]).encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError("Segmented stream oracle transform codes must be valid base64.") from exc
    if len(transform_codes) != block_count:
        raise ValueError("Segmented stream oracle transform code count mismatch.")
    codebook = list(header["blockTransformCodebook"])
    output = bytearray()
    cursor = 0
    for block_index, transform_code in enumerate(transform_codes):
        remaining = raw_bytes - (block_index * block_size)
        block_bytes = min(block_size, remaining)
        transformed_block = transformed_stream[cursor : cursor + block_bytes]
        if len(transformed_block) != block_bytes:
            raise ValueError("Segmented stream oracle block length mismatch.")
        cursor += block_bytes
        if transform_code >= len(codebook):
            raise ValueError("Segmented stream oracle transform code mismatch.")
        if not isinstance(codebook[transform_code], dict):
            raise ValueError("Segmented stream oracle malformed transform codebook row.")
        transform_row = dict(codebook[transform_code])
        if "t" not in transform_row:
            raise ValueError("Segmented stream oracle malformed transform codebook row.")
        transform_name = str(transform_row["t"])
        if transform_name not in TRANSFORMS:
            raise ValueError("Segmented stream oracle unknown transform codebook row.")
        params_obj = transform_row.get("p", {})
        if not isinstance(params_obj, dict):
            raise ValueError("Segmented stream oracle malformed transform codebook row.")
        params = {str(key): int(value) for key, value in params_obj.items()}
        _transform, inverse = TRANSFORMS[transform_name]
        restored = inverse(transformed_block, params)
        if len(restored) != block_bytes:
            raise ValueError("Segmented stream oracle raw block size mismatch.")
        output.extend(restored)
    if cursor != len(transformed_stream):
        raise ValueError("Segmented stream oracle payload length mismatch.")
    return bytes(output)


def decode_segmented_stream_var_oracle_payload(payload: bytes, compressor: str, header: dict[str, Any]) -> bytes:
    transformed_stream = decompress_payload(payload, compressor)
    if len(transformed_stream) != int(header["transformedBytes"]):
        raise ValueError("Segmented stream variable oracle transformed size mismatch.")
    raw_bytes = int(header["rawBytes"])
    if len(transformed_stream) != raw_bytes:
        raise ValueError("Segmented stream variable oracle length-preserving stream size mismatch.")
    allowed_lengths = tuple(int(length) for length in list(header["allowedSegmentLengths"]))
    if not allowed_lengths or any(length <= 0 for length in allowed_lengths) or tuple(sorted(set(allowed_lengths))) != allowed_lengths:
        raise ValueError("Segmented stream variable oracle malformed allowed segment lengths.")
    maximum_length = allowed_lengths[-1]
    segment_count = int(header["segmentCount"])
    try:
        length_codes = base64.b64decode(str(header["segmentLengthCodes"]).encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError("Segmented stream variable oracle length codes must be valid base64.") from exc
    try:
        transform_codes = base64.b64decode(str(header["segmentTransformCodes"]).encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError("Segmented stream variable oracle transform codes must be valid base64.") from exc
    if len(length_codes) != segment_count:
        raise ValueError("Segmented stream variable oracle length code count mismatch.")
    if len(transform_codes) != segment_count:
        raise ValueError("Segmented stream variable oracle transform code count mismatch.")
    length_codebook = [int(length) for length in list(header["segmentLengthCodebook"])]
    if any(length <= 0 or length > maximum_length for length in length_codebook):
        raise ValueError("Segmented stream variable oracle malformed length codebook.")
    transform_codebook = list(header["segmentTransformCodebook"])
    output = bytearray()
    cursor = 0
    for index, (length_code, transform_code) in enumerate(zip(length_codes, transform_codes)):
        if length_code >= len(length_codebook):
            raise ValueError("Segmented stream variable oracle length code out of codebook.")
        segment_bytes = length_codebook[length_code]
        segment_end = cursor + segment_bytes
        if segment_end > raw_bytes:
            raise ValueError("Segmented stream variable oracle segment lengths must sum to rawBytes.")
        if segment_end < raw_bytes and segment_bytes not in allowed_lengths:
            raise ValueError("Segmented stream variable oracle non-final segment length must be allowed.")
        transformed_block = transformed_stream[cursor:segment_end]
        if len(transformed_block) != segment_bytes:
            raise ValueError("Segmented stream variable oracle segment length mismatch.")
        cursor = segment_end
        if transform_code >= len(transform_codebook):
            raise ValueError("Segmented stream variable oracle transform code out of codebook.")
        if not isinstance(transform_codebook[transform_code], dict):
            raise ValueError("Segmented stream variable oracle malformed transform codebook row.")
        transform_row = dict(transform_codebook[transform_code])
        if "t" not in transform_row:
            raise ValueError("Segmented stream variable oracle malformed transform codebook row.")
        transform_name = str(transform_row["t"])
        if transform_name not in TRANSFORMS:
            raise ValueError("Segmented stream variable oracle unknown transform codebook row.")
        params_obj = transform_row.get("p", {})
        if not isinstance(params_obj, dict):
            raise ValueError("Segmented stream variable oracle malformed transform codebook row.")
        params = {str(key): int(value) for key, value in params_obj.items()}
        _transform, inverse = TRANSFORMS[transform_name]
        restored = inverse(transformed_block, params)
        if len(restored) != segment_bytes:
            raise ValueError("Segmented stream variable oracle raw segment size mismatch.")
        output.extend(restored)
    if cursor != raw_bytes:
        raise ValueError("Segmented stream variable oracle segment lengths must sum to rawBytes.")
    return bytes(output)


def decode_segmented_stream_boundary_oracle_payload(payload: bytes, compressor: str, header: dict[str, Any]) -> bytes:
    transformed_stream = decompress_payload(payload, compressor)
    if len(transformed_stream) != int(header["transformedBytes"]):
        raise ValueError("Segmented stream boundary oracle transformed size mismatch.")
    raw_bytes = int(header["rawBytes"])
    if len(transformed_stream) != raw_bytes:
        raise ValueError("Segmented stream boundary oracle length-preserving stream size mismatch.")
    minimum = int(header["minSegmentBytes"])
    maximum = int(header["maxSegmentBytes"])
    if minimum <= 0 or maximum < minimum or maximum > 0xFFFF:
        raise ValueError("Segmented stream boundary oracle malformed segment limits.")
    if str(header.get("segmentLengthCodeFormat")) != "uint16-le":
        raise ValueError("Segmented stream boundary oracle malformed length code format.")
    segment_count = int(header["segmentCount"])
    try:
        length_codes = base64.b64decode(str(header["segmentLengthCodes"]).encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError("Segmented stream boundary oracle length codes must be valid base64.") from exc
    try:
        transform_codes = base64.b64decode(str(header["segmentTransformCodes"]).encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError("Segmented stream boundary oracle transform codes must be valid base64.") from exc
    if len(length_codes) != segment_count * 2:
        raise ValueError("Segmented stream boundary oracle length code count mismatch.")
    if len(transform_codes) != segment_count:
        raise ValueError("Segmented stream boundary oracle transform code count mismatch.")
    segment_lengths = [struct.unpack("<H", length_codes[index : index + 2])[0] for index in range(0, len(length_codes), 2)]
    transform_codebook = list(header["segmentTransformCodebook"])
    output = bytearray()
    cursor = 0
    for segment_bytes, transform_code in zip(segment_lengths, transform_codes):
        if segment_bytes <= 0 or segment_bytes > maximum:
            raise ValueError("Segmented stream boundary oracle malformed segment length.")
        segment_end = cursor + segment_bytes
        if segment_end > raw_bytes:
            raise ValueError("Segmented stream boundary oracle segment lengths must sum to rawBytes.")
        if segment_end < raw_bytes and segment_bytes < minimum:
            raise ValueError("Segmented stream boundary oracle non-final segment length below minimum.")
        transformed_block = transformed_stream[cursor:segment_end]
        if len(transformed_block) != segment_bytes:
            raise ValueError("Segmented stream boundary oracle segment length mismatch.")
        cursor = segment_end
        if transform_code >= len(transform_codebook):
            raise ValueError("Segmented stream boundary oracle transform code out of codebook.")
        if not isinstance(transform_codebook[transform_code], dict):
            raise ValueError("Segmented stream boundary oracle malformed transform codebook row.")
        transform_row = dict(transform_codebook[transform_code])
        if "t" not in transform_row:
            raise ValueError("Segmented stream boundary oracle malformed transform codebook row.")
        transform_name = str(transform_row["t"])
        if transform_name not in TRANSFORMS:
            raise ValueError("Segmented stream boundary oracle unknown transform codebook row.")
        params_obj = transform_row.get("p", {})
        if not isinstance(params_obj, dict):
            raise ValueError("Segmented stream boundary oracle malformed transform codebook row.")
        params = {str(key): int(value) for key, value in params_obj.items()}
        _transform, inverse = TRANSFORMS[transform_name]
        restored = inverse(transformed_block, params)
        if len(restored) != segment_bytes:
            raise ValueError("Segmented stream boundary oracle raw segment size mismatch.")
        output.extend(restored)
    if cursor != raw_bytes:
        raise ValueError("Segmented stream boundary oracle segment lengths must sum to rawBytes.")
    return bytes(output)


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


def is_segmented_stream_block_selector(candidate: Candidate) -> bool:
    return candidate.name in {
        SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_GATED_CANDIDATE,
        SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_BENEFIT_GATED_CANDIDATE,
        SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE,
    }


def is_segmented_stream_benefit_gated_selector(candidate: Candidate) -> bool:
    return candidate.name in {
        SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_BENEFIT_GATED_CANDIDATE,
        SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE,
    }


def is_long_token_intern_selector(candidate: Candidate) -> bool:
    return candidate.name == SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE


def evaluate_segmented_stream_block_selector(data: bytes, candidate: Candidate) -> tuple[int, int | None, int]:
    choices: list[tuple[int, int]] = []
    round_trip_failures = 0
    for block_size in SEGMENTED_STREAM_SELECTOR_BLOCK_SIZES:
        block_candidate = Candidate(
            f"segmented-stream-oracle-{block_size}+{candidate.compressor}",
            SEGMENTED_STREAM_ORACLE_TRANSFORM,
            candidate.compressor,
            {"blockSize": block_size},
        )
        try:
            artifact = encode_candidate(data, block_candidate)
            if decode_candidate(artifact) != data:
                round_trip_failures += 1
                continue
        except Exception:
            round_trip_failures += 1
            continue
        choices.append((len(artifact), block_size))
    if not choices:
        return len(data), None, max(1, round_trip_failures)
    artifact_bytes, selected_block_size = min(choices, key=lambda row: (row[0], row[1]))
    return artifact_bytes, selected_block_size, round_trip_failures


def evaluate_long_token_intern_block_selector(
    data: bytes,
    candidate: Candidate,
) -> tuple[int, int | None, int, dict[str, int]]:
    transformed, metadata = transform_long_token_intern(data)
    artifact_bytes, selected_block_size, round_trip_failures = evaluate_segmented_stream_block_selector(
        transformed,
        candidate,
    )
    if selected_block_size is not None:
        block_candidate = Candidate(
            f"segmented-stream-oracle-{selected_block_size}+{candidate.compressor}",
            SEGMENTED_STREAM_ORACLE_TRANSFORM,
            candidate.compressor,
            {"blockSize": selected_block_size},
        )
        try:
            artifact = encode_candidate(transformed, block_candidate)
            decoded_transformed = decode_candidate(artifact)
            if inverse_long_token_intern(decoded_transformed) != data:
                round_trip_failures += 1
        except Exception:
            round_trip_failures += 1
    return artifact_bytes, selected_block_size, round_trip_failures, metadata


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
    gate = candidate_gate(candidate)
    gate_applied_files = 0
    gate_skipped_files = 0
    block_selector = is_segmented_stream_block_selector(candidate)
    benefit_gate = is_segmented_stream_benefit_gated_selector(candidate)
    long_token_intern_selector = is_long_token_intern_selector(candidate)
    benefit_applied_files = 0
    benefit_skipped_files = 0
    selected_block_sizes: dict[str, int] = {}
    transform_applied_files = 0
    interned_token_count = 0
    interned_occurrence_count = 0

    for row in corpus:
        if time.monotonic() >= deadline:
            complete = False
            break
        data = bytes(row["data"])
        baseline_bytes = int(row["baselineBytes"])
        gate_applied = True
        benefit_applied = False
        transform_applied = False
        transform_metadata: dict[str, int] = {}
        if gate is not None:
            gate_applied = project_text_gate_applies(row)
            if gate_applied:
                gate_applied_files += 1
            else:
                gate_skipped_files += 1
        start = time.perf_counter_ns()
        if gate is not None and not gate_applied:
            artifact_bytes = baseline_bytes
            selected_block_size = None
        elif block_selector:
            if long_token_intern_selector:
                artifact_bytes, selected_block_size, selector_failures, transform_metadata = (
                    evaluate_long_token_intern_block_selector(data, candidate)
                )
            else:
                artifact_bytes, selected_block_size, selector_failures = evaluate_segmented_stream_block_selector(
                    data,
                    candidate,
                )
            round_trip_failures += selector_failures
            if benefit_gate:
                benefit_applied = selected_block_size is not None and artifact_bytes < baseline_bytes
                if benefit_applied:
                    benefit_applied_files += 1
                    if long_token_intern_selector:
                        transform_applied = True
                        transform_applied_files += 1
                        interned_token_count += int(transform_metadata.get("internedTokenCount", 0))
                        interned_occurrence_count += int(transform_metadata.get("internedOccurrenceCount", 0))
                else:
                    benefit_skipped_files += 1
                    artifact_bytes = baseline_bytes
                    selected_block_size = None
                    if long_token_intern_selector:
                        transform_metadata = {}
            if selected_block_size is not None:
                block_key = str(selected_block_size)
                selected_block_sizes[block_key] = selected_block_sizes.get(block_key, 0) + 1
        else:
            selected_block_size = None
            try:
                artifact = encode_candidate(data, candidate)
                decoded = decode_candidate(artifact)
                if decoded != data:
                    round_trip_failures += 1
            except Exception:
                round_trip_failures += 1
                artifact = data
            artifact_bytes = len(artifact)
        end = time.perf_counter_ns()
        timings.append((end - start) / 1_000_000)
        artifact_total += artifact_bytes
        baseline_total += baseline_bytes
        evaluated_files += 1
        delta = size_delta_pct(artifact_bytes, baseline_bytes)
        gain = saved_pct(artifact_bytes, baseline_bytes)
        worst_regression = max(worst_regression, delta)
        best_file_gain = max(best_file_gain, gain)
        if options.include_file_results:
            file_row = {
                "path": row["path"],
                "extension": row["extension"],
                "baselineBytes": baseline_bytes,
                "candidateBytes": artifact_bytes,
                "candidateVsBaselinePct": delta,
            }
            if gate is not None:
                file_row["gateApplied"] = gate_applied
            if benefit_gate:
                file_row["benefitApplied"] = benefit_applied
            if long_token_intern_selector:
                file_row["transform"] = "long-token-intern"
                file_row["transformApplied"] = transform_applied
                file_row["internedTokenCount"] = int(transform_metadata.get("internedTokenCount", 0)) if transform_applied else 0
                file_row["internedOccurrenceCount"] = (
                    int(transform_metadata.get("internedOccurrenceCount", 0)) if transform_applied else 0
                )
            if block_selector:
                file_row["selectedBlockSize"] = selected_block_size
            file_results.append(file_row)

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
    if gate is not None:
        result["gate"] = gate
        result["gateAppliedFiles"] = gate_applied_files
        result["gateSkippedFiles"] = gate_skipped_files
    if benefit_gate:
        result["benefitAppliedFiles"] = benefit_applied_files
        result["benefitSkippedFiles"] = benefit_skipped_files
    if long_token_intern_selector:
        result["preTransform"] = "long-token-intern"
        result["transformAppliedFiles"] = transform_applied_files
        result["internedTokenCount"] = interned_token_count
        result["internedOccurrenceCount"] = interned_occurrence_count
    if block_selector:
        result["selectedBlockSizes"] = {key: selected_block_sizes[key] for key in sorted(selected_block_sizes)}
    if options.include_file_results:
        result["files"] = file_results
    stopped = time.monotonic() >= deadline
    return result, stopped


def build_results(options: SearchOptions) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + options.time_limit_seconds
    files, skipped = discover_search_files(options)
    corpus = load_corpus(files, options)
    stats, input_state_summary = load_state(options.state_input)
    candidates = build_candidates(options.candidate_limit, corpus, stats)
    candidate_filters = list(options.candidate_filters or [])
    candidates = filter_candidates(candidates, candidate_filters)
    pending = list(candidates)
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
    research_probe_count = max(0, len(candidates) - options.candidate_limit)
    results = {
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
        "candidateLimitMode": "soft-research-probes" if research_probe_count else "hard",
        "researchProbeCount": research_probe_count,
        "candidateFilters": candidate_filters,
        "candidateFilterMatchCount": len(candidates),
        "candidateCount": len(candidates),
        "evaluatedCandidateCount": len(candidate_results),
        "fileCount": len(corpus),
        "skipped": skipped,
        "thresholds": {
            "minImprovementPct": options.min_improvement_pct,
            "maxWorstRegressionPct": options.max_worst_regression_pct,
        },
        "baseline": "SLB1 --planner stdlib-auto --model auto",
        "inputState": input_state_summary,
        "stateOutputRequested": options.state_output is not None,
        "modelState": summarize_stats(stats),
        "selectionTrace": selection_trace,
        "note": "Experimental local predictor search. No raw file contents are embedded in this result.",
        "candidates": ranked,
    }
    if options.state_output is not None:
        write_state(options.state_output, stats, results, input_state_summary)
        results["stateOutput"] = {"written": True, "keys": len(stats)}
    else:
        results["stateOutput"] = {"written": False}
    return results


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
    parser.add_argument(
        "--candidate-filter",
        action="append",
        dest="candidate_filters",
        help="candidate name glob to include; can be repeated",
    )
    parser.add_argument("--time-limit-seconds", type=positive_float, default=30.0)
    parser.add_argument("--max-passes", type=positive_int, default=2)
    parser.add_argument("--search-mode", choices=["adaptive", "exhaustive"], default="adaptive")
    parser.add_argument("--min-improvement-pct", type=non_negative_float, default=1.0)
    parser.add_argument("--max-worst-regression-pct", type=non_negative_float, default=2.0)
    parser.add_argument("--include-file-results", action="store_true")
    parser.add_argument("--state-input", help="read persistent predictor model state from this JSON file")
    parser.add_argument("--state-output", help="write updated predictor model state to this JSON file")
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
        candidate_filters=args.candidate_filters,
        state_input=Path(args.state_input) if args.state_input else None,
        state_output=Path(args.state_output) if args.state_output else None,
    )
    try:
        results = build_results(options)
    except CandidateFilterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
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
