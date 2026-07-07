from __future__ import annotations

import base64
import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest


def load_search_predictors():
    module_path = Path(__file__).resolve().parents[1] / "benchmarks" / "search_predictors.py"
    benchmark_dir = str(module_path.parent)
    if benchmark_dir not in sys.path:
        sys.path.insert(0, benchmark_dir)
    spec = importlib.util.spec_from_file_location("search_predictors", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def predictor_header(artifact: bytes) -> tuple[dict, int]:
    header_len, payload_len = struct.unpack("<IQ", artifact[4:16])
    return json.loads(artifact[16 : 16 + header_len].decode("utf-8")), payload_len


def replace_predictor_header(artifact: bytes, header: dict) -> bytes:
    header_len, payload_len = struct.unpack("<IQ", artifact[4:16])
    payload = artifact[16 + header_len :]
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return artifact[:4] + struct.pack("<IQ", len(header_bytes), payload_len) + header_bytes + payload


def segmented_stream_artifact(search, block_size: int = 64) -> tuple[bytes, dict]:
    data = (bytes(range(block_size)) * 2) + (b"\x00" * (block_size * 2)) + bytes(
        (index * 17) % 256 for index in range(block_size * 2)
    )
    candidate = search.Candidate(
        f"segmented-stream-oracle-{block_size}+zlib",
        "segmented-stream-oracle",
        "zlib",
        {"blockSize": block_size},
    )
    artifact = search.encode_candidate(data, candidate)
    header, _payload_len = predictor_header(artifact)
    return artifact, header


def segmented_stream_var_data() -> bytes:
    return (
        b"Star Light Codec variable segmentation\n" * 37
        + bytes((index * 11) % 256 for index in range(2111))
        + b"\x00" * 1739
    )


def segmented_stream_var_artifact(search) -> tuple[bytes, dict]:
    data = segmented_stream_var_data()
    candidate = search.Candidate(
        "segmented-stream-var-oracle-512-4096+zlib",
        "segmented-stream-var-oracle",
        "zlib",
        {"minSegmentBytes": 512, "maxSegmentBytes": 4096},
    )
    artifact = search.encode_candidate(data, candidate)
    header, _payload_len = predictor_header(artifact)
    return artifact, header


def segmented_stream_boundary_data() -> bytes:
    return (
        b"def alpha(value):\n"
        b"    if value:\n"
        b"        return {'kind': 'alpha', 'value': value}\n\n"
        b"items = [alpha(index) for index in range(64)]\n"
    ) * 48 + bytes((index * 13) % 256 for index in range(777))


def segmented_stream_boundary_artifact(search) -> tuple[bytes, dict]:
    data = segmented_stream_boundary_data()
    candidate = search.Candidate(
        "segmented-stream-boundary-oracle-512-4096+zlib",
        "segmented-stream-boundary-oracle",
        "zlib",
        {"minSegmentBytes": 512, "maxSegmentBytes": 4096},
    )
    artifact = search.encode_candidate(data, candidate)
    header, _payload_len = predictor_header(artifact)
    return artifact, header


def decoded_segment_lengths(header: dict) -> list[int]:
    codebook = list(header["segmentLengthCodebook"])
    codes = base64.b64decode(header["segmentLengthCodes"].encode("ascii"))
    return [codebook[code] for code in codes]


def decoded_boundary_segment_lengths(header: dict) -> list[int]:
    codes = base64.b64decode(header["segmentLengthCodes"].encode("ascii"))
    return [struct.unpack("<H", codes[index : index + 2])[0] for index in range(0, len(codes), 2)]


def test_candidate_artifact_round_trips() -> None:
    search = load_search_predictors()
    data = bytes((index * 3) % 256 for index in range(4096))
    candidate = search.Candidate("delta-prev-1+zlib", "delta-prev", "zlib", {"offset": 1})
    artifact = search.encode_candidate(data, candidate)

    assert artifact.startswith(search.SEARCH_MAGIC)
    assert search.decode_candidate(artifact) == data


def test_future_candidates_are_generated_from_corpus_patterns() -> None:
    search = load_search_predictors()
    data = bytes((index % 3) * 17 for index in range(4096))
    candidates = search.build_candidates(56, [{"data": data}], {})
    generated = [candidate for candidate in candidates if candidate.name.startswith("future-")]

    assert generated
    assert any(candidate.params == {"offset": 3} for candidate in generated)
    assert len(candidates) == 56


def test_default_candidates_preserve_future_coverage_and_include_segmented_oracles() -> None:
    search = load_search_predictors()
    stats = {"offset:delta-prev:16": {"count": 1.0, "score": 12.0}}

    candidates = search.build_candidates(64, [], stats)
    generated = [candidate for candidate in candidates if candidate.name.startswith("future-")]

    assert len(generated) == 16
    assert any(candidate.name == "segmented-oracle-1024+zlib" for candidate in candidates)
    assert any(candidate.name == "segmented-stream-oracle-1024+zlib" for candidate in candidates)
    assert any(
        candidate.name == "segmented-stream-oracle-4096-project-text-gated+zlib" for candidate in candidates
    )
    assert any(
        candidate.name == "segmented-stream-oracle-1024-4096-project-text-gated+zlib" for candidate in candidates
    )
    assert any(
        candidate.name == "segmented-stream-oracle-1024-4096-project-text-benefit-gated+zlib"
        for candidate in candidates
    )
    assert any(
        candidate.name == "segmented-stream-oracle-1024-4096-project-text-long-token-intern-benefit-gated+zlib"
        for candidate in candidates
    )
    assert any(candidate.name == "segmented-stream-var-oracle-512-4096+zlib" for candidate in candidates)
    assert any(candidate.name == "segmented-stream-boundary-oracle-512-4096+zlib" for candidate in candidates)
    # Default-or-larger limits are soft for benchmark-only research probes so
    # the existing 64-candidate future-search coverage stays intact.
    assert len(candidates) == 75


def test_long_token_intern_candidate_exact_filter_reaches_it() -> None:
    search = load_search_predictors()
    exact_name = "segmented-stream-oracle-1024-4096-project-text-long-token-intern-benefit-gated+zlib"

    candidates = search.filter_candidates(search.build_candidates(64, [], {}), [exact_name])

    assert [candidate.name for candidate in candidates] == [exact_name]


def test_fixed_segmented_stream_oracle_sweep_is_limited_and_unique() -> None:
    search = load_search_predictors()

    candidates = search.build_candidates(64, [], {})
    fixed_stream = [
        candidate
        for candidate in candidates
        if candidate.transform == search.SEGMENTED_STREAM_ORACLE_TRANSFORM
        and candidate.name != search.SEGMENTED_STREAM_4096_PROJECT_TEXT_GATED_CANDIDATE
        and candidate.name != search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_GATED_CANDIDATE
        and candidate.name != search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_BENEFIT_GATED_CANDIDATE
        and candidate.name != search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE
    ]
    gated = [
        candidate
        for candidate in candidates
        if candidate.name == search.SEGMENTED_STREAM_4096_PROJECT_TEXT_GATED_CANDIDATE
    ]
    selector = [
        candidate
        for candidate in candidates
        if candidate.name == search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_GATED_CANDIDATE
    ]
    benefit_selector = [
        candidate
        for candidate in candidates
        if candidate.name == search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_BENEFIT_GATED_CANDIDATE
    ]
    long_token_selector = [
        candidate
        for candidate in candidates
        if candidate.name == search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE
    ]
    per_block = [candidate for candidate in candidates if candidate.transform == "segmented-oracle"]
    variable = [
        candidate
        for candidate in candidates
        if candidate.transform == search.SEGMENTED_STREAM_VAR_ORACLE_TRANSFORM
    ]
    boundary = [
        candidate
        for candidate in candidates
        if candidate.transform == search.SEGMENTED_STREAM_BOUNDARY_ORACLE_TRANSFORM
    ]

    assert [candidate.params["blockSize"] for candidate in fixed_stream] == [512, 1024, 2048, 4096]
    assert [candidate.name for candidate in fixed_stream] == [
        "segmented-stream-oracle-512+zlib",
        "segmented-stream-oracle-1024+zlib",
        "segmented-stream-oracle-2048+zlib",
        "segmented-stream-oracle-4096+zlib",
    ]
    assert [candidate.params["blockSize"] for candidate in gated] == [4096]
    assert search.candidate_gate(gated[0]) == {"id": "project-text", "name": "project-text-code-ish-v1"}
    assert [candidate.params for candidate in selector] == [{"minBlockSize": 1024, "maxBlockSize": 4096}]
    assert search.candidate_gate(selector[0]) == {"id": "project-text", "name": "project-text-code-ish-v1"}
    assert [candidate.params for candidate in benefit_selector] == [{"minBlockSize": 1024, "maxBlockSize": 4096}]
    assert search.candidate_gate(benefit_selector[0]) == {"id": "project-text", "name": "project-text-code-ish-v1"}
    assert [candidate.params for candidate in long_token_selector] == [{"minBlockSize": 1024, "maxBlockSize": 4096}]
    assert search.candidate_gate(long_token_selector[0]) == {"id": "project-text", "name": "project-text-code-ish-v1"}
    assert [candidate.name for candidate in per_block] == ["segmented-oracle-1024+zlib"]
    assert [candidate.name for candidate in variable] == ["segmented-stream-var-oracle-512-4096+zlib"]
    assert [candidate.name for candidate in boundary] == ["segmented-stream-boundary-oracle-512-4096+zlib"]
    assert len({candidate.name for candidate in candidates}) == len(candidates)


def test_project_text_gate_applies_to_project_text_paths_only() -> None:
    search = load_search_predictors()

    assert search.project_text_gate_applies({"path": "README.md", "extension": ".md"})
    assert search.project_text_gate_applies({"path": "BENCHMARKS.md", "extension": ".md"})
    assert search.project_text_gate_applies({"path": "CHANGELOG.md", "extension": ".md"})
    assert search.project_text_gate_applies({"path": "SECURITY.md", "extension": ".md"})
    assert search.project_text_gate_applies({"path": "LICENSING.md", "extension": ".md"})
    assert search.project_text_gate_applies({"path": "docs/guide.rst", "extension": ".rst"})
    assert search.project_text_gate_applies({"path": "src/starlight_codec/codec.py", "extension": ".py"})
    assert search.project_text_gate_applies({"path": "tests/test_codec.py", "extension": ".py"})
    assert not search.project_text_gate_applies({"path": "README.ja.md", "extension": ".md"})
    assert not search.project_text_gate_applies({"path": "benchmarks/search_predictors.py", "extension": ".py"})
    assert not search.project_text_gate_applies({"path": "docs/__pycache__/guide.pyc", "extension": ".pyc"})
    assert not search.project_text_gate_applies({"path": "src/generated/state.json", "extension": ".json"})
    assert not search.project_text_gate_applies({"path": "docs/logo.png", "extension": ".png"})


def test_generated_future_candidate_round_trips() -> None:
    search = load_search_predictors()
    data = bytes((index % 3) * 17 for index in range(4096))
    candidate = next(
        candidate
        for candidate in search.build_candidates(56, [{"data": data}], {})
        if candidate.name.startswith("future-") and candidate.params == {"offset": 3}
    )

    artifact = search.encode_candidate(data, candidate)

    assert search.decode_candidate(artifact) == data


def test_segmented_oracle_candidate_round_trips_and_records_block_choices() -> None:
    search = load_search_predictors()
    data = (bytes(range(64)) * 2) + (b"\x00" * 128) + bytes((index * 17) % 256 for index in range(128))
    candidate = search.Candidate("segmented-oracle-64+zlib", "segmented-oracle", "zlib", {"blockSize": 64})

    artifact = search.encode_candidate(data, candidate)
    header_len, payload_len = struct.unpack("<IQ", artifact[4:16])
    header = json.loads(artifact[16 : 16 + header_len].decode("utf-8"))

    assert search.decode_candidate(artifact) == data
    assert header["candidate"] == "segmented-oracle-64+zlib"
    assert header["transform"] == "segmented-oracle"
    assert payload_len == sum(block["payloadBytes"] for block in header["blocks"])
    assert {block["transform"] for block in header["blocks"]} <= {"identity", "delta-prev", "xor-prev"}
    assert all(block["params"] == {} or block["params"] == {"offset": 1} for block in header["blocks"])


def test_segmented_stream_oracle_round_trips_and_records_compact_choices() -> None:
    search = load_search_predictors()
    data = (bytes(range(64)) * 2) + (b"\x00" * 128) + bytes((index * 17) % 256 for index in range(128))
    candidate = search.Candidate(
        "segmented-stream-oracle-64+zlib",
        "segmented-stream-oracle",
        "zlib",
        {"blockSize": 64},
    )

    artifact = search.encode_candidate(data, candidate)
    header, payload_len = predictor_header(artifact)
    codes = base64.b64decode(header["blockTransformCodes"].encode("ascii"))

    assert search.decode_candidate(artifact) == data
    assert header["candidate"] == "segmented-stream-oracle-64+zlib"
    assert header["transform"] == "segmented-stream-oracle"
    assert header["blockSize"] == 64
    assert header["blockCount"] == 6
    assert header["blockChoiceMethod"] == "local-compressed-size-plus-code-byte"
    assert len(codes) == header["blockCount"]
    assert max(codes) < len(header["blockTransformCodebook"])
    assert "blocks" not in header
    assert payload_len == header["payloadBytes"]


def test_segmented_stream_oracle_rejects_non_canonical_block_count() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_artifact(search)
    header["blockCount"] -= 1

    with pytest.raises(ValueError, match="non-canonical block count"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_oracle_rejects_non_length_preserving_stream_size() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_artifact(search)
    header["rawBytes"] -= 1

    with pytest.raises(ValueError, match="length-preserving stream size"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_oracle_rejects_invalid_base64_codes() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_artifact(search)
    header["blockTransformCodes"] = "!!!!"

    with pytest.raises(ValueError, match="valid base64"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_oracle_rejects_transform_code_count_mismatch() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_artifact(search)
    header["blockTransformCodes"] = base64.b64encode(b"\x00" * (header["blockCount"] - 1)).decode("ascii")

    with pytest.raises(ValueError, match="transform code count mismatch"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_oracle_rejects_transform_code_outside_codebook() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_artifact(search)
    codes = bytearray(base64.b64decode(header["blockTransformCodes"].encode("ascii")))
    codes[0] = len(header["blockTransformCodebook"])
    header["blockTransformCodes"] = base64.b64encode(bytes(codes)).decode("ascii")

    with pytest.raises(ValueError, match="transform code mismatch"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_oracle_rejects_malformed_or_unknown_codebook_row() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_artifact(search)
    malformed = dict(header)
    malformed["blockTransformCodebook"] = list(header["blockTransformCodebook"])
    malformed["blockTransformCodebook"][0] = {"p": {}}

    with pytest.raises(ValueError, match="malformed transform codebook row"):
        search.decode_candidate(replace_predictor_header(artifact, malformed))

    unknown = dict(header)
    unknown["blockTransformCodebook"] = list(header["blockTransformCodebook"])
    unknown["blockTransformCodebook"][0] = {"t": "unknown-transform", "p": {}}

    with pytest.raises(ValueError, match="unknown transform codebook row"):
        search.decode_candidate(replace_predictor_header(artifact, unknown))


def test_segmented_stream_var_oracle_round_trips_and_records_compact_metadata() -> None:
    search = load_search_predictors()
    data = segmented_stream_var_data()
    artifact, header = segmented_stream_var_artifact(search)
    payload_len = predictor_header(artifact)[1]
    length_codes = base64.b64decode(header["segmentLengthCodes"].encode("ascii"))
    transform_codes = base64.b64decode(header["segmentTransformCodes"].encode("ascii"))
    lengths = decoded_segment_lengths(header)

    assert search.decode_candidate(artifact) == data
    assert header["candidate"] == "segmented-stream-var-oracle-512-4096+zlib"
    assert header["transform"] == "segmented-stream-var-oracle"
    assert header["params"] == {"maxSegmentBytes": 4096, "minSegmentBytes": 512}
    assert header["allowedSegmentLengths"] == [512, 1024, 2048, 4096]
    assert header["segmentChoiceMethod"] == "dynamic-programming-local-compressed-size-plus-code-bytes"
    assert header["segmentCount"] == len(length_codes) == len(transform_codes)
    assert max(length_codes, default=0) < len(header["segmentLengthCodebook"])
    assert max(transform_codes, default=0) < len(header["segmentTransformCodebook"])
    assert sum(lengths) == header["rawBytes"]
    assert lengths[-1] not in header["allowedSegmentLengths"]
    assert "blocks" not in header
    assert payload_len == header["payloadBytes"]


def test_segmented_stream_boundary_oracle_round_trips_and_records_compact_metadata() -> None:
    search = load_search_predictors()
    data = segmented_stream_boundary_data()
    artifact, header = segmented_stream_boundary_artifact(search)
    payload_len = predictor_header(artifact)[1]
    length_codes = base64.b64decode(header["segmentLengthCodes"].encode("ascii"))
    transform_codes = base64.b64decode(header["segmentTransformCodes"].encode("ascii"))
    lengths = decoded_boundary_segment_lengths(header)
    planner = header["boundaryPlanner"]

    assert search.decode_candidate(artifact) == data
    assert header["candidate"] == "segmented-stream-boundary-oracle-512-4096+zlib"
    assert header["transform"] == "segmented-stream-boundary-oracle"
    assert header["params"] == {"maxSegmentBytes": 4096, "minSegmentBytes": 512}
    assert header["minSegmentBytes"] == 512
    assert header["maxSegmentBytes"] == 4096
    assert header["segmentChoiceMethod"] == "structural-boundary/local-heuristic-compressed-size-plus-code-bytes"
    assert header["segmentLengthCodeFormat"] == "uint16-le"
    assert header["segmentCount"] == len(length_codes) // 2 == len(transform_codes)
    assert max(transform_codes, default=0) < len(header["segmentTransformCodebook"])
    assert sum(lengths) == header["rawBytes"]
    assert all(512 <= length <= 4096 for length in lengths[:-1])
    assert "blocks" not in header
    assert "segmentLengthCodebook" not in header
    assert planner["boundaryCandidateCount"] < header["rawBytes"]
    assert planner["boundaryStructuralSelectedCount"] > 0
    assert planner["boundaryFallbackCount"] > 0
    assert payload_len == header["payloadBytes"]


def test_segmented_stream_boundary_planner_uses_bounded_offsets() -> None:
    search = load_search_predictors()
    data = segmented_stream_boundary_data()

    boundaries, stats = search.segmented_stream_boundary_offsets(data, 512, 4096)

    assert boundaries[0] == 0
    assert boundaries[-1] == len(data)
    assert stats["boundaryCandidateCount"] == len(boundaries)
    assert stats["boundaryHintCount"] > stats["boundaryCandidateCount"]
    assert stats["boundaryCandidateCount"] <= (len(data) // 512 + 1) * 5


def test_segmented_stream_boundary_oracle_rejects_invalid_base64_length_codes() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_boundary_artifact(search)
    header["segmentLengthCodes"] = "!!!!"

    with pytest.raises(ValueError, match="length codes must be valid base64"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_boundary_oracle_rejects_length_code_count_mismatch() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_boundary_artifact(search)
    codes = base64.b64decode(header["segmentLengthCodes"].encode("ascii"))
    header["segmentLengthCodes"] = base64.b64encode(codes[:-1]).decode("ascii")

    with pytest.raises(ValueError, match="length code count mismatch"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_boundary_oracle_rejects_invalid_base64_transform_codes() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_boundary_artifact(search)
    header["segmentTransformCodes"] = "!!!!"

    with pytest.raises(ValueError, match="transform codes must be valid base64"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_boundary_oracle_rejects_transform_code_count_mismatch() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_boundary_artifact(search)
    codes = base64.b64decode(header["segmentTransformCodes"].encode("ascii"))
    header["segmentTransformCodes"] = base64.b64encode(codes[:-1]).decode("ascii")

    with pytest.raises(ValueError, match="transform code count mismatch"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_boundary_oracle_rejects_segment_lengths_not_summing_to_raw_bytes() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_boundary_artifact(search)
    lengths = decoded_boundary_segment_lengths(header)
    lengths[-1] -= 1
    header["segmentLengthCodes"] = base64.b64encode(b"".join(struct.pack("<H", length) for length in lengths)).decode("ascii")

    with pytest.raises(ValueError, match="segment lengths must sum to rawBytes"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_boundary_oracle_rejects_non_final_segment_below_minimum() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_boundary_artifact(search)
    lengths = decoded_boundary_segment_lengths(header)
    lengths[0] = 1
    lengths[-1] += header["rawBytes"] - sum(lengths)
    header["segmentLengthCodes"] = base64.b64encode(b"".join(struct.pack("<H", length) for length in lengths)).decode("ascii")

    with pytest.raises(ValueError, match="non-final segment length below minimum"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_boundary_oracle_rejects_transform_code_outside_codebook() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_boundary_artifact(search)
    codes = bytearray(base64.b64decode(header["segmentTransformCodes"].encode("ascii")))
    codes[0] = len(header["segmentTransformCodebook"])
    header["segmentTransformCodes"] = base64.b64encode(bytes(codes)).decode("ascii")

    with pytest.raises(ValueError, match="transform code out of codebook"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_boundary_oracle_rejects_malformed_or_unknown_transform_codebook_row() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_boundary_artifact(search)
    used_code = base64.b64decode(header["segmentTransformCodes"].encode("ascii"))[0]
    malformed = dict(header)
    malformed["segmentTransformCodebook"] = list(header["segmentTransformCodebook"])
    malformed["segmentTransformCodebook"][used_code] = {"p": {}}

    with pytest.raises(ValueError, match="malformed transform codebook row"):
        search.decode_candidate(replace_predictor_header(artifact, malformed))

    unknown = dict(header)
    unknown["segmentTransformCodebook"] = list(header["segmentTransformCodebook"])
    unknown["segmentTransformCodebook"][used_code] = {"t": "unknown-transform", "p": {}}

    with pytest.raises(ValueError, match="unknown transform codebook row"):
        search.decode_candidate(replace_predictor_header(artifact, unknown))


def test_segmented_stream_boundary_oracle_rejects_malformed_segment_limits() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_boundary_artifact(search)
    header["minSegmentBytes"] = 0

    with pytest.raises(ValueError, match="malformed segment limits"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_boundary_oracle_rejects_tampered_transformed_bytes() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_boundary_artifact(search)
    header["transformedBytes"] += 1

    with pytest.raises(ValueError, match="transformed size mismatch"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_var_chooser_propagates_later_better_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    search = load_search_predictors()
    costs = {
        (0, 1): 1,
        (1, 2): 1,
        (3, 1): 1,
        (4, 1): 1,
        (0, 4): 100,
        (1, 4): 100,
    }

    def choose_block(block: bytes, _compressor: str) -> tuple[int, bytes]:
        start = block[0]
        length = len(block)
        return 0, b"x" * costs.get((start, length), 80)

    def no_compression(data: bytes, _compressor: str) -> bytes:
        return data

    monkeypatch.setattr(search, "choose_segmented_stream_block_candidate", choose_block)
    monkeypatch.setattr(search, "compress_payload", no_compression)

    blocks = search.choose_segmented_stream_var_blocks(bytes(range(5)), "zlib", (1, 2, 4))

    assert [length for length, _code, _transformed in blocks] == [1, 2, 1, 1]
    assert sum(len(transformed) + search.SEGMENTED_STREAM_VAR_SEGMENT_METADATA_BYTES for _length, _code, transformed in blocks) == 12


def test_segmented_stream_var_oracle_rejects_invalid_base64_length_codes() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_var_artifact(search)
    header["segmentLengthCodes"] = "!!!!"

    with pytest.raises(ValueError, match="length codes must be valid base64"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_var_oracle_rejects_invalid_base64_transform_codes() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_var_artifact(search)
    header["segmentTransformCodes"] = "!!!!"

    with pytest.raises(ValueError, match="transform codes must be valid base64"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_var_oracle_rejects_length_code_outside_codebook() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_var_artifact(search)
    codes = bytearray(base64.b64decode(header["segmentLengthCodes"].encode("ascii")))
    codes[0] = len(header["segmentLengthCodebook"])
    header["segmentLengthCodes"] = base64.b64encode(bytes(codes)).decode("ascii")

    with pytest.raises(ValueError, match="length code out of codebook"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_var_oracle_rejects_segment_lengths_not_summing_to_raw_bytes() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_var_artifact(search)
    codes = bytearray(base64.b64decode(header["segmentLengthCodes"].encode("ascii")))
    shorter_code = next(
        index
        for index, length in enumerate(header["segmentLengthCodebook"])
        if length < header["segmentLengthCodebook"][codes[-1]]
    )
    codes[-1] = shorter_code
    header["segmentLengthCodes"] = base64.b64encode(bytes(codes)).decode("ascii")

    with pytest.raises(ValueError, match="segment lengths must sum to rawBytes"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_var_oracle_rejects_non_final_length_not_allowed() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_var_artifact(search)
    codes = base64.b64decode(header["segmentLengthCodes"].encode("ascii"))
    header["segmentLengthCodebook"] = list(header["segmentLengthCodebook"])
    header["segmentLengthCodebook"][codes[0]] = 513

    with pytest.raises(ValueError, match="non-final segment length must be allowed"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_var_oracle_rejects_transform_code_count_mismatch() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_var_artifact(search)
    codes = base64.b64decode(header["segmentTransformCodes"].encode("ascii"))
    header["segmentTransformCodes"] = base64.b64encode(codes[:-1]).decode("ascii")

    with pytest.raises(ValueError, match="transform code count mismatch"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_var_oracle_rejects_transform_code_outside_codebook() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_var_artifact(search)
    codes = bytearray(base64.b64decode(header["segmentTransformCodes"].encode("ascii")))
    codes[0] = len(header["segmentTransformCodebook"])
    header["segmentTransformCodes"] = base64.b64encode(bytes(codes)).decode("ascii")

    with pytest.raises(ValueError, match="transform code out of codebook"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_stream_var_oracle_rejects_malformed_or_unknown_transform_codebook_row() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_var_artifact(search)
    used_code = base64.b64decode(header["segmentTransformCodes"].encode("ascii"))[0]
    malformed = dict(header)
    malformed["segmentTransformCodebook"] = list(header["segmentTransformCodebook"])
    malformed["segmentTransformCodebook"][used_code] = {"p": {}}

    with pytest.raises(ValueError, match="malformed transform codebook row"):
        search.decode_candidate(replace_predictor_header(artifact, malformed))

    unknown = dict(header)
    unknown["segmentTransformCodebook"] = list(header["segmentTransformCodebook"])
    unknown["segmentTransformCodebook"][used_code] = {"t": "unknown-transform", "p": {}}

    with pytest.raises(ValueError, match="unknown transform codebook row"):
        search.decode_candidate(replace_predictor_header(artifact, unknown))


def test_segmented_stream_var_oracle_rejects_tampered_transformed_bytes() -> None:
    search = load_search_predictors()
    artifact, header = segmented_stream_var_artifact(search)
    header["transformedBytes"] += 1

    with pytest.raises(ValueError, match="transformed size mismatch"):
        search.decode_candidate(replace_predictor_header(artifact, header))


def test_segmented_oracle_prefers_identity_when_payload_sizes_tie(monkeypatch: pytest.MonkeyPatch) -> None:
    search = load_search_predictors()

    def same_payload(_data: bytes, _compressor: str) -> bytes:
        return b"same-compressed-size"

    monkeypatch.setattr(search, "compress_payload", same_payload)

    best = search.choose_segmented_block_candidate(b"metadata tie breaker", "zlib")
    identity_choice = {"transform": "identity", "params": {}, "payload": b"same-compressed-size"}
    delta_choice = {"transform": "delta-prev", "params": {"offset": 1}, "payload": b"same-compressed-size"}

    assert best["transform"] == "identity"
    assert search.segmented_choice_metadata_cost(identity_choice) < search.segmented_choice_metadata_cost(delta_choice)


def test_segmented_oracle_candidate_bytes_include_metadata_cost() -> None:
    search = load_search_predictors()
    data = bytes((index % 5) * 31 for index in range(256))
    candidate = search.Candidate("segmented-oracle-64+zlib", "segmented-oracle", "zlib", {"blockSize": 64})
    artifact = search.encode_candidate(data, candidate)
    header_len, payload_len = struct.unpack("<IQ", artifact[4:16])
    corpus = [
        {
            "path": "mixed.bin",
            "extension": ".bin",
            "data": data,
            "baselineBytes": len(artifact) + 100,
        }
    ]

    result, stopped = search.evaluate_candidate(
        candidate,
        corpus,
        search.time.monotonic() + 5.0,
        search.SearchOptions(paths=[], include_file_results=True),
    )

    assert stopped is False
    assert len(artifact) == 16 + header_len + payload_len
    assert result["candidateBytes"] == len(artifact)
    assert result["files"][0]["candidateBytes"] == len(artifact)
    assert result["candidateBytes"] > payload_len


def test_segmented_stream_oracle_metadata_is_compact_against_segmented_blocks() -> None:
    search = load_search_predictors()
    data = bytes((index % 5) * 31 for index in range(512))
    segmented = search.Candidate("segmented-oracle-64+zlib", "segmented-oracle", "zlib", {"blockSize": 64})
    streamed = search.Candidate(
        "segmented-stream-oracle-64+zlib",
        "segmented-stream-oracle",
        "zlib",
        {"blockSize": 64},
    )

    segmented_header, _segmented_payload_len = predictor_header(search.encode_candidate(data, segmented))
    streamed_header, _streamed_payload_len = predictor_header(search.encode_candidate(data, streamed))

    assert "blocks" in segmented_header
    assert "blocks" not in streamed_header
    assert len(segmented_header["blocks"]) == streamed_header["blockCount"]
    assert len(base64.b64decode(streamed_header["blockTransformCodes"].encode("ascii"))) == streamed_header["blockCount"]
    assert len(json.dumps(streamed_header, separators=(",", ":"), sort_keys=True)) < len(
        json.dumps(segmented_header, separators=(",", ":"), sort_keys=True)
    )


def test_segmented_oracle_candidate_is_constructed_and_evaluated_on_mixed_fixture() -> None:
    search = load_search_predictors()
    data = (
        b"Star Light Codec\n" * 32
        + bytes((index * 7) % 256 for index in range(512))
        + bytes(index % 2 for index in range(512))
    )
    candidates = search.build_candidates(64, [{"data": data}], {})
    candidate = next(candidate for candidate in candidates if candidate.name.startswith("segmented-oracle-"))
    corpus = [
        {
            "path": "mixed.fixture",
            "extension": ".fixture",
            "data": data,
            "baselineBytes": len(data) + 1024,
        }
    ]

    result, stopped = search.evaluate_candidate(
        candidate,
        corpus,
        search.time.monotonic() + 5.0,
        search.SearchOptions(paths=[]),
    )

    assert stopped is False
    assert result["candidate"] == candidate.name
    assert result["evaluatedFiles"] == 1
    assert result["roundTripFailures"] == 0
    assert result["candidateBytes"] > 0


def test_segmented_stream_oracle_candidate_is_constructed_and_evaluated_on_mixed_fixture() -> None:
    search = load_search_predictors()
    data = (
        b"Star Light Codec\n" * 32
        + bytes((index * 7) % 256 for index in range(512))
        + bytes(index % 2 for index in range(512))
    )
    candidates = search.build_candidates(64, [{"data": data}], {})
    candidate = next(candidate for candidate in candidates if candidate.name.startswith("segmented-stream-oracle-"))
    corpus = [
        {
            "path": "mixed.fixture",
            "extension": ".fixture",
            "data": data,
            "baselineBytes": len(data) + 1024,
        }
    ]

    result, stopped = search.evaluate_candidate(
        candidate,
        corpus,
        search.time.monotonic() + 5.0,
        search.SearchOptions(paths=[]),
    )

    assert stopped is False
    assert result["candidate"] == candidate.name
    assert result["evaluatedFiles"] == 1
    assert result["roundTripFailures"] == 0
    assert result["candidateBytes"] > 0


def test_segmented_stream_oracle_2048_candidate_round_trips_and_evaluates_on_mixed_fixture() -> None:
    search = load_search_predictors()
    data = (
        b"Star Light Codec\n" * 96
        + bytes((index * 7) % 256 for index in range(4096))
        + bytes(index % 2 for index in range(2048))
    )
    candidates = search.build_candidates(64, [{"data": data}], {})
    candidate = next(candidate for candidate in candidates if candidate.name == "segmented-stream-oracle-2048+zlib")
    corpus = [
        {
            "path": "mixed.fixture",
            "extension": ".fixture",
            "data": data,
            "baselineBytes": len(data) + 2048,
        }
    ]

    artifact = search.encode_candidate(data, candidate)
    header, _payload_len = predictor_header(artifact)
    result, stopped = search.evaluate_candidate(
        candidate,
        corpus,
        search.time.monotonic() + 5.0,
        search.SearchOptions(paths=[]),
    )

    assert search.decode_candidate(artifact) == data
    assert header["candidate"] == "segmented-stream-oracle-2048+zlib"
    assert header["blockSize"] == 2048
    assert stopped is False
    assert result["candidate"] == candidate.name
    assert result["evaluatedFiles"] == 1
    assert result["roundTripFailures"] == 0
    assert result["candidateBytes"] > 0


def test_segmented_stream_var_oracle_candidate_is_constructed_and_evaluated_on_mixed_fixture() -> None:
    search = load_search_predictors()
    data = (
        b"Star Light Codec\n" * 97
        + bytes((index * 7) % 256 for index in range(2305))
        + bytes(index % 2 for index in range(773))
    )
    candidates = search.build_candidates(64, [{"data": data}], {})
    candidate = next(candidate for candidate in candidates if candidate.name.startswith("segmented-stream-var-oracle-"))
    corpus = [
        {
            "path": "mixed.fixture",
            "extension": ".fixture",
            "data": data,
            "baselineBytes": len(data) + 2048,
        }
    ]

    result, stopped = search.evaluate_candidate(
        candidate,
        corpus,
        search.time.monotonic() + 5.0,
        search.SearchOptions(paths=[]),
    )

    assert stopped is False
    assert result["candidate"] == candidate.name
    assert result["evaluatedFiles"] == 1
    assert result["roundTripFailures"] == 0
    assert result["candidateBytes"] > 0


def test_segmented_stream_boundary_oracle_candidate_is_constructed_and_evaluated_on_mixed_fixture() -> None:
    search = load_search_predictors()
    data = segmented_stream_boundary_data()
    candidates = search.build_candidates(64, [{"data": data}], {})
    candidate = next(candidate for candidate in candidates if candidate.name.startswith("segmented-stream-boundary-oracle-"))
    corpus = [
        {
            "path": "mixed.pyfixture",
            "extension": ".pyfixture",
            "data": data,
            "baselineBytes": len(data) + 2048,
        }
    ]

    result, stopped = search.evaluate_candidate(
        candidate,
        corpus,
        search.time.monotonic() + 5.0,
        search.SearchOptions(paths=[]),
    )
    artifact, header = segmented_stream_boundary_artifact(search)

    assert stopped is False
    assert result["candidate"] == candidate.name
    assert result["evaluatedFiles"] == 1
    assert result["roundTripFailures"] == 0
    assert result["candidateBytes"] > 0
    assert len(artifact) > header["payloadBytes"]
    assert header["boundaryPlanner"]["boundaryCandidateCount"] > header["segmentCount"]


def test_learning_state_influences_future_generation() -> None:
    search = load_search_predictors()
    stats = {
        "offset:delta-prev:16": {"count": 1.0, "score": 12.0},
        "compressor:zlib": {"count": 1.0, "score": 5.0},
    }
    candidates = search.build_candidates(52, [], stats)
    generated = [candidate for candidate in candidates if candidate.name.startswith("future-")]

    assert generated
    assert generated[0].name == "future-delta-prev-15+zlib"
    assert generated[0].params == {"offset": 15}


def test_predictor_search_is_bounded_and_uses_relative_labels(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "ramp.bin").write_bytes(bytes((index * 3) % 256 for index in range(4096)))
    (tmp_path / "sample.txt").write_text("Star Light Codec\n" * 256, encoding="utf-8")

    options = search.SearchOptions(
        paths=[tmp_path],
        label_root=tmp_path,
        file_limit=2,
        candidate_limit=8,
        time_limit_seconds=5.0,
        include_file_results=True,
    )
    results = search.build_results(options)

    assert results["fileCount"] == 2
    assert results["searchMode"] == "adaptive"
    assert results["evaluatedCandidateCount"] <= 8
    assert results["candidateLimitMode"] == "hard"
    assert results["researchProbeCount"] == 0
    assert results["stoppedReason"] in {"complete", "time-limit", "candidate-limit"}
    assert results["candidates"]
    assert results["selectionTrace"]
    assert results["modelState"]
    assert all("decision" in candidate for candidate in results["candidates"])
    assert all("selectionScore" in candidate for candidate in results["candidates"])
    for candidate in results["candidates"]:
        for row in candidate.get("files", []):
            assert not Path(row["path"]).is_absolute()


def test_predictor_search_reports_soft_limit_research_probes(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "pattern.bin").write_bytes(bytes((index % 3) * 17 for index in range(4096)))

    results = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=64,
            file_limit=1,
            time_limit_seconds=10.0,
        )
    )

    assert results["candidateLimit"] == 64
    assert results["candidateLimitMode"] == "soft-research-probes"
    assert results["researchProbeCount"] == 11
    assert results["candidateFilters"] == []
    assert results["candidateFilterMatchCount"] == results["candidateCount"]
    assert results["candidateCount"] == 75
    assert results["evaluatedCandidateCount"] == 75


def test_candidate_filter_exact_4096_reaches_soft_probe(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "pattern.bin").write_bytes(bytes((index % 5) * 31 for index in range(8192)))

    results = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=64,
            candidate_filters=["segmented-stream-oracle-4096+zlib"],
            file_limit=1,
            time_limit_seconds=10.0,
            search_mode="exhaustive",
        )
    )

    assert results["candidateFilters"] == ["segmented-stream-oracle-4096+zlib"]
    assert results["candidateFilterMatchCount"] == 1
    assert results["candidateCount"] == 1
    assert results["evaluatedCandidateCount"] == 1
    assert [row["candidate"] for row in results["selectionTrace"]] == ["segmented-stream-oracle-4096+zlib"]


def test_candidate_filter_exact_gated_4096_reaches_soft_probe(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "README.md").write_text("Star Light Codec\n" * 256, encoding="utf-8")

    results = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=64,
            candidate_filters=["segmented-stream-oracle-4096-project-text-gated+zlib"],
            file_limit=1,
            time_limit_seconds=10.0,
            search_mode="exhaustive",
        )
    )

    assert results["candidateFilters"] == ["segmented-stream-oracle-4096-project-text-gated+zlib"]
    assert results["candidateFilterMatchCount"] == 1
    assert results["candidateCount"] == 1
    assert results["evaluatedCandidateCount"] == 1
    assert [row["candidate"] for row in results["selectionTrace"]] == [
        "segmented-stream-oracle-4096-project-text-gated+zlib"
    ]


def test_candidate_filter_exact_gated_block_selector_reaches_soft_probe(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "README.md").write_text("Star Light Codec\n" * 256, encoding="utf-8")

    results = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=64,
            candidate_filters=["segmented-stream-oracle-1024-4096-project-text-gated+zlib"],
            file_limit=1,
            time_limit_seconds=10.0,
            search_mode="exhaustive",
        )
    )

    assert results["candidateFilters"] == ["segmented-stream-oracle-1024-4096-project-text-gated+zlib"]
    assert results["candidateFilterMatchCount"] == 1
    assert results["candidateCount"] == 1
    assert results["evaluatedCandidateCount"] == 1
    assert [row["candidate"] for row in results["selectionTrace"]] == [
        "segmented-stream-oracle-1024-4096-project-text-gated+zlib"
    ]


def test_candidate_filter_exact_benefit_gated_block_selector_reaches_soft_probe(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "README.md").write_text("Star Light Codec\n" * 256, encoding="utf-8")

    results = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=64,
            candidate_filters=["segmented-stream-oracle-1024-4096-project-text-benefit-gated+zlib"],
            file_limit=1,
            time_limit_seconds=10.0,
            search_mode="exhaustive",
        )
    )

    assert results["candidateFilters"] == ["segmented-stream-oracle-1024-4096-project-text-benefit-gated+zlib"]
    assert results["candidateFilterMatchCount"] == 1
    assert results["candidateCount"] == 1
    assert results["evaluatedCandidateCount"] == 1
    assert [row["candidate"] for row in results["selectionTrace"]] == [
        "segmented-stream-oracle-1024-4096-project-text-benefit-gated+zlib"
    ]


def test_candidate_filter_wildcard_selects_fixed_stream_sweep_and_gated_probe(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "pattern.bin").write_bytes(bytes((index * 7) % 256 for index in range(8192)))

    results = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=64,
            candidate_filters=["segmented-stream-oracle-*+zlib"],
            file_limit=1,
            time_limit_seconds=10.0,
            search_mode="exhaustive",
        )
    )

    expected = [
        "segmented-stream-oracle-512+zlib",
        "segmented-stream-oracle-1024+zlib",
        "segmented-stream-oracle-2048+zlib",
        "segmented-stream-oracle-4096+zlib",
        "segmented-stream-oracle-4096-project-text-gated+zlib",
        "segmented-stream-oracle-1024-4096-project-text-gated+zlib",
        "segmented-stream-oracle-1024-4096-project-text-benefit-gated+zlib",
        "segmented-stream-oracle-1024-4096-project-text-long-token-intern-benefit-gated+zlib",
    ]
    assert results["candidateFilters"] == ["segmented-stream-oracle-*+zlib"]
    assert results["candidateFilterMatchCount"] == 8
    assert results["candidateCount"] == 8
    assert [row["candidate"] for row in results["selectionTrace"]] == expected


def test_gated_4096_candidate_applies_to_project_text_and_skips_benchmarks(tmp_path: Path) -> None:
    search = load_search_predictors()
    docs_path = tmp_path / "docs" / "guide.md"
    benchmark_path = tmp_path / "benchmarks" / "search_predictors.py"
    docs_path.parent.mkdir()
    benchmark_path.parent.mkdir()
    docs_path.write_text("# Guide\n\nStar Light Codec project text.\n" * 192, encoding="utf-8")
    benchmark_path.write_text("print('benchmark helper')\n" * 192, encoding="utf-8")

    results = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=64,
            candidate_filters=["segmented-stream-oracle-4096-project-text-gated+zlib"],
            file_limit=2,
            time_limit_seconds=10.0,
            search_mode="exhaustive",
            include_file_results=True,
        )
    )

    candidate = results["candidates"][0]
    files = {row["path"]: row for row in candidate["files"]}

    assert candidate["gate"] == {"id": "project-text", "name": "project-text-code-ish-v1"}
    assert candidate["gateAppliedFiles"] == 1
    assert candidate["gateSkippedFiles"] == 1
    assert files["docs/guide.md"]["gateApplied"] is True
    assert files["benchmarks/search_predictors.py"]["gateApplied"] is False
    assert files["benchmarks/search_predictors.py"]["candidateBytes"] == files["benchmarks/search_predictors.py"][
        "baselineBytes"
    ]
    assert files["benchmarks/search_predictors.py"]["candidateVsBaselinePct"] == 0.0


def test_gated_block_selector_records_selected_sizes_and_skips_benchmarks(tmp_path: Path) -> None:
    search = load_search_predictors()
    docs_path = tmp_path / "docs" / "guide.md"
    src_path = tmp_path / "src" / "starlight_codec" / "codec.py"
    benchmark_path = tmp_path / "benchmarks" / "search_predictors.py"
    docs_path.parent.mkdir(parents=True)
    src_path.parent.mkdir(parents=True)
    benchmark_path.parent.mkdir(parents=True)
    docs_path.write_text("# Guide\n\nStar Light Codec project text.\n" * 192, encoding="utf-8")
    src_path.write_text("def codec(value):\n    return value + 1\n\n" * 192, encoding="utf-8")
    benchmark_path.write_text("print('benchmark helper')\n" * 192, encoding="utf-8")

    results = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=64,
            candidate_filters=["segmented-stream-oracle-1024-4096-project-text-gated+zlib"],
            file_limit=3,
            time_limit_seconds=10.0,
            search_mode="exhaustive",
            include_file_results=True,
        )
    )

    candidate = results["candidates"][0]
    files = {row["path"]: row for row in candidate["files"]}
    applied_files = [row for row in files.values() if row["gateApplied"]]
    skipped_file = files["benchmarks/search_predictors.py"]

    assert candidate["candidate"] == "segmented-stream-oracle-1024-4096-project-text-gated+zlib"
    assert candidate["gate"] == {"id": "project-text", "name": "project-text-code-ish-v1"}
    assert candidate["gateAppliedFiles"] == 2
    assert candidate["gateSkippedFiles"] == 1
    assert candidate["roundTripFailures"] == 0
    assert sum(candidate["selectedBlockSizes"].values()) == 2
    assert set(candidate["selectedBlockSizes"]) <= {"1024", "2048", "4096"}
    assert {row["selectedBlockSize"] for row in applied_files} <= {1024, 2048, 4096}
    assert all(row["selectedBlockSize"] is not None for row in applied_files)
    assert skipped_file["gateApplied"] is False
    assert skipped_file["selectedBlockSize"] is None
    assert skipped_file["candidateBytes"] == skipped_file["baselineBytes"]
    assert skipped_file["candidateVsBaselinePct"] == 0.0


def test_benefit_gated_block_selector_noops_non_beneficial_and_gate_skipped_files() -> None:
    search = load_search_predictors()
    candidate = next(
        candidate
        for candidate in search.build_candidates(64, [], {})
        if candidate.name == search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_BENEFIT_GATED_CANDIDATE
    )
    benefit_data = (
        b"def encode_value(value):\n"
        b"    return 'Star Light Codec project text ' + str(value)\n\n"
    ) * 192
    losing_data = bytes((index * 37 + 11) % 256 for index in range(4096))
    skipped_data = b"print('benchmark helper')\n" * 64
    benefit_bytes, benefit_block_size, benefit_failures = search.evaluate_segmented_stream_block_selector(
        benefit_data,
        candidate,
    )
    losing_bytes, losing_block_size, losing_failures = search.evaluate_segmented_stream_block_selector(
        losing_data,
        candidate,
    )
    assert benefit_block_size is not None
    assert losing_block_size is not None
    assert benefit_failures == 0
    assert losing_failures == 0
    benefit_baseline_bytes = benefit_bytes + 128
    losing_baseline_bytes = losing_bytes
    skipped_baseline_bytes = len(skipped_data) + 17
    corpus = [
        {
            "path": "src/starlight_codec/benefit.py",
            "extension": ".py",
            "data": benefit_data,
            "baselineBytes": benefit_baseline_bytes,
        },
        {
            "path": "tests/test_losing_fixture.py",
            "extension": ".py",
            "data": losing_data,
            "baselineBytes": losing_baseline_bytes,
        },
        {
            "path": "benchmarks/search_predictors.py",
            "extension": ".py",
            "data": skipped_data,
            "baselineBytes": skipped_baseline_bytes,
        },
    ]

    result, stopped = search.evaluate_candidate(
        candidate,
        corpus,
        search.time.monotonic() + 5.0,
        search.SearchOptions(paths=[], include_file_results=True),
    )

    files = {row["path"]: row for row in result["files"]}
    benefit_file = files["src/starlight_codec/benefit.py"]
    losing_file = files["tests/test_losing_fixture.py"]
    skipped_file = files["benchmarks/search_predictors.py"]

    assert stopped is False
    assert result["candidate"] == "segmented-stream-oracle-1024-4096-project-text-benefit-gated+zlib"
    assert result["gate"] == {"id": "project-text", "name": "project-text-code-ish-v1"}
    assert result["gateAppliedFiles"] == 2
    assert result["gateSkippedFiles"] == 1
    assert result["benefitAppliedFiles"] == 1
    assert result["benefitSkippedFiles"] == 1
    assert result["selectedBlockSizes"] == {str(benefit_block_size): 1}
    assert result["roundTripFailures"] == 0
    assert result["candidateBytes"] == benefit_bytes + losing_baseline_bytes + skipped_baseline_bytes

    assert benefit_file["gateApplied"] is True
    assert benefit_file["benefitApplied"] is True
    assert benefit_file["selectedBlockSize"] == benefit_block_size
    assert benefit_file["candidateBytes"] == benefit_bytes
    assert benefit_file["candidateBytes"] < benefit_file["baselineBytes"]

    assert losing_file["gateApplied"] is True
    assert losing_file["benefitApplied"] is False
    assert losing_file["selectedBlockSize"] is None
    assert losing_file["candidateBytes"] == losing_file["baselineBytes"]
    assert losing_file["candidateVsBaselinePct"] == 0.0

    assert skipped_file["gateApplied"] is False
    assert skipped_file["benefitApplied"] is False
    assert skipped_file["selectedBlockSize"] is None
    assert skipped_file["candidateBytes"] == skipped_file["baselineBytes"]
    assert skipped_file["candidateVsBaselinePct"] == 0.0


def test_long_token_intern_transform_round_trips_and_records_counts() -> None:
    search = load_search_predictors()
    token = b"src/starlight_codec/very-long-token-alpha-001"
    data = (
        b"\x00\xffprefix\n"
        + token
        + "\nutf8:\u30b9\u30bf\u30fc\u30e9\u30a4\u30c8\n".encode("utf-8")
        + b"path="
        + token
        + b"\r\n"
        + bytes(range(32))
    )

    transformed, metadata = search.transform_long_token_intern(data)

    assert transformed.startswith(search.LONG_TOKEN_INTERN_MAGIC)
    assert search.inverse_long_token_intern(transformed) == data
    assert metadata["internedTokenCount"] == 1
    assert metadata["internedOccurrenceCount"] == 2
    assert metadata["internedTokenBytes"] == len(token)


def test_long_token_intern_candidate_noops_when_not_beneficial() -> None:
    search = load_search_predictors()
    candidate = next(
        candidate
        for candidate in search.build_candidates(64, [], {})
        if candidate.name == search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE
    )
    token = b"tests/fixtures/not-quite-beneficial-long-token-0001"
    data = (b"def value():\n    return b'" + token + b"'\n\n") * 8
    candidate_bytes, selected_block_size, failures, metadata = search.evaluate_long_token_intern_block_selector(
        data,
        candidate,
    )
    assert selected_block_size is not None
    assert failures == 0
    assert metadata["internedTokenCount"] >= 1
    corpus = [
        {
            "path": "tests/test_losing_fixture.py",
            "extension": ".py",
            "data": data,
            "baselineBytes": candidate_bytes,
        }
    ]

    result, stopped = search.evaluate_candidate(
        candidate,
        corpus,
        search.time.monotonic() + 5.0,
        search.SearchOptions(paths=[], include_file_results=True),
    )

    file_row = result["files"][0]
    assert stopped is False
    assert result["benefitAppliedFiles"] == 0
    assert result["benefitSkippedFiles"] == 1
    assert result["transformAppliedFiles"] == 0
    assert result["internedTokenCount"] == 0
    assert result["internedOccurrenceCount"] == 0
    assert result["selectedBlockSizes"] == {}
    assert file_row["gateApplied"] is True
    assert file_row["benefitApplied"] is False
    assert file_row["transform"] == "long-token-intern"
    assert file_row["transformApplied"] is False
    assert file_row["internedTokenCount"] == 0
    assert file_row["internedOccurrenceCount"] == 0
    assert file_row["selectedBlockSize"] is None
    assert file_row["candidateBytes"] == file_row["baselineBytes"] == candidate_bytes
    assert file_row["candidateVsBaselinePct"] == 0.0


def test_long_token_intern_candidate_applies_when_beneficial_and_records_accounting() -> None:
    search = load_search_predictors()
    candidate = next(
        candidate
        for candidate in search.build_candidates(64, [], {})
        if candidate.name == search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE
    )
    token = b"src/starlight_codec/repeated/meaning-adjacent-token-alpha-0001"
    data = (
        b"def encode_path(value):\n"
        b"    return b'"
        + token
        + b"' + value\n\n"
    ) * 96
    candidate_bytes, selected_block_size, failures, metadata = search.evaluate_long_token_intern_block_selector(
        data,
        candidate,
    )
    assert selected_block_size is not None
    assert failures == 0
    assert metadata["internedTokenCount"] == 1
    assert metadata["internedOccurrenceCount"] == 96
    corpus = [
        {
            "path": "src/starlight_codec/benefit.py",
            "extension": ".py",
            "data": data,
            "baselineBytes": candidate_bytes + 128,
        }
    ]

    result, stopped = search.evaluate_candidate(
        candidate,
        corpus,
        search.time.monotonic() + 5.0,
        search.SearchOptions(paths=[], include_file_results=True),
    )

    file_row = result["files"][0]
    assert stopped is False
    assert result["candidate"] == search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE
    assert result["preTransform"] == "long-token-intern"
    assert result["benefitAppliedFiles"] == 1
    assert result["benefitSkippedFiles"] == 0
    assert result["transformAppliedFiles"] == 1
    assert result["internedTokenCount"] == metadata["internedTokenCount"]
    assert result["internedOccurrenceCount"] == metadata["internedOccurrenceCount"]
    assert result["selectedBlockSizes"] == {str(selected_block_size): 1}
    assert result["roundTripFailures"] == 0
    assert result["candidateBytes"] == candidate_bytes

    assert file_row["gateApplied"] is True
    assert file_row["benefitApplied"] is True
    assert file_row["transform"] == "long-token-intern"
    assert file_row["transformApplied"] is True
    assert file_row["internedTokenCount"] == metadata["internedTokenCount"]
    assert file_row["internedOccurrenceCount"] == metadata["internedOccurrenceCount"]
    assert file_row["selectedBlockSize"] == selected_block_size
    assert file_row["candidateBytes"] == candidate_bytes
    assert file_row["candidateBytes"] < file_row["baselineBytes"]


def test_long_token_intern_gate_skipped_files_do_not_count_as_benefit_skipped() -> None:
    search = load_search_predictors()
    candidate = next(
        candidate
        for candidate in search.build_candidates(64, [], {})
        if candidate.name == search.SEGMENTED_STREAM_1024_4096_PROJECT_TEXT_LONG_TOKEN_INTERN_BENEFIT_GATED_CANDIDATE
    )
    token = b"benchmarks/search_predictors/repeated-token-that-would-have-qualified"
    data = (b"print('" + token + b"')\n") * 32
    corpus = [
        {
            "path": "benchmarks/search_predictors.py",
            "extension": ".py",
            "data": data,
            "baselineBytes": len(data) + 17,
        }
    ]

    result, stopped = search.evaluate_candidate(
        candidate,
        corpus,
        search.time.monotonic() + 5.0,
        search.SearchOptions(paths=[], include_file_results=True),
    )

    file_row = result["files"][0]
    assert stopped is False
    assert result["gateAppliedFiles"] == 0
    assert result["gateSkippedFiles"] == 1
    assert result["benefitAppliedFiles"] == 0
    assert result["benefitSkippedFiles"] == 0
    assert result["transformAppliedFiles"] == 0
    assert result["internedTokenCount"] == 0
    assert result["internedOccurrenceCount"] == 0
    assert result["selectedBlockSizes"] == {}
    assert file_row["gateApplied"] is False
    assert file_row["benefitApplied"] is False
    assert file_row["transformApplied"] is False
    assert file_row["internedTokenCount"] == 0
    assert file_row["internedOccurrenceCount"] == 0
    assert file_row["selectedBlockSize"] is None
    assert file_row["candidateBytes"] == file_row["baselineBytes"]
    assert file_row["candidateVsBaselinePct"] == 0.0


def test_multiple_candidate_filters_match_or_semantics(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "pattern.bin").write_bytes(bytes((index * 9) % 256 for index in range(8192)))

    filters = [
        "segmented-stream-oracle-4096+zlib",
        "segmented-stream-oracle-1024+zlib",
    ]
    results = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=64,
            candidate_filters=filters,
            file_limit=1,
            time_limit_seconds=10.0,
            search_mode="exhaustive",
        )
    )

    expected = [
        "segmented-stream-oracle-1024+zlib",
        "segmented-stream-oracle-4096+zlib",
    ]
    assert results["candidateFilters"] == filters
    assert results["candidateFilterMatchCount"] == len(expected)
    assert results["candidateCount"] == len(expected)
    assert results["evaluatedCandidateCount"] == len(expected)
    assert [row["candidate"] for row in results["selectionTrace"]] == expected
    assert {row["candidate"] for row in results["candidates"]} == set(expected)


def test_candidate_filter_no_match_returns_clear_cli_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    search = load_search_predictors()
    (tmp_path / "sample.txt").write_text("Star Light Codec\n" * 64, encoding="utf-8")

    exit_code = search.main(
        [
            str(tmp_path),
            "--candidate-filter",
            "missing-candidate-*",
            "--time-limit-seconds",
            "5",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "--candidate-filter matched no candidates" in captured.err
    assert "missing-candidate-*" in captured.err


def test_time_limit_must_be_positive() -> None:
    search = load_search_predictors()

    with pytest.raises(SystemExit):
        search.parse_args(["README.md", "--time-limit-seconds", "0"])


def test_exhaustive_mode_keeps_declared_order(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "sample.txt").write_text("Star Light Codec\n" * 128, encoding="utf-8")

    options = search.SearchOptions(
        paths=[tmp_path],
        label_root=tmp_path,
        candidate_limit=4,
        time_limit_seconds=5.0,
        search_mode="exhaustive",
    )
    results = search.build_results(options)

    assert results["searchMode"] == "exhaustive"
    assert [row["candidate"] for row in results["selectionTrace"]] == [
        "identity+gzip",
        "identity+zlib",
        "identity+bz2",
        "identity+lzma",
    ]


def test_predictor_search_state_round_trips_between_runs(tmp_path: Path) -> None:
    search = load_search_predictors()
    state_path = tmp_path / "predictor-state.json"
    (tmp_path / "sample.txt").write_text("Star Light Codec\n" * 256, encoding="utf-8")

    first = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=4,
            time_limit_seconds=5.0,
            state_output=state_path,
        )
    )
    state_doc = json.loads(state_path.read_text(encoding="utf-8"))

    assert first["stateOutput"]["written"] is True
    assert state_doc["kind"] == search.STATE_KIND
    assert state_doc["runCount"] == 1
    assert state_doc["modelState"]

    second = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=4,
            time_limit_seconds=5.0,
            state_input=state_path,
            state_output=state_path,
        )
    )
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))

    assert second["inputState"]["loaded"] is True
    assert second["inputState"]["runCount"] == 1
    assert updated_state["runCount"] == 2
    assert updated_state["modelState"]
