from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from starlight_codec.codec import (
    MAGIC_SLB1,
    StarLightCodecError,
    create_capsule_file,
    decode_file,
    decode_slb1,
    encode_file,
    encode_slb1,
    hydrate_file,
    inspect_slb1,
)


def test_repeated_text_round_trips_and_recommends_storage() -> None:
    data = ("Star Light Codec exact byte artifact. " * 512).encode("utf-8")
    encoded = encode_slb1(data, max_passes=2)

    assert encoded.artifact.startswith(MAGIC_SLB1)
    assert encoded.metadata["recommendedForStorage"] is True
    assert encoded.metadata["artifactBytes"] < len(data)

    decoded = decode_slb1(encoded.artifact)
    assert decoded.data == data
    assert decoded.metadata["digestMatch"] is True


def test_random_like_data_round_trips_but_keeps_original_for_storage() -> None:
    data = random.Random(12345).randbytes(4096)
    encoded = encode_slb1(data, max_passes=2)

    assert encoded.metadata["fallbackReason"] == "compression-not-beneficial"
    assert encoded.metadata["recommendedForStorage"] is False
    assert encoded.metadata["adoptionDecision"] == "keep-original-for-storage"
    assert decode_slb1(encoded.artifact).data == data


def test_empty_payload_round_trips_without_storage_claim() -> None:
    encoded = encode_slb1(b"", max_passes=2)

    assert encoded.metadata["rawBytes"] == 0
    assert encoded.metadata["recommendedForStorage"] is False
    assert encoded.metadata["adoptionReason"] == "empty-input-has-no-storage-savings"
    assert decode_slb1(encoded.artifact).data == b""


def test_tampered_payload_fails_closed() -> None:
    encoded = encode_slb1(b"hello hello hello" * 20, max_passes=2)
    tampered = bytearray(encoded.artifact)
    tampered[-1] ^= 0x01

    with pytest.raises(StarLightCodecError):
        decode_slb1(bytes(tampered))


def test_bad_magic_fails_closed() -> None:
    encoded = encode_slb1(b"hello", max_passes=1)

    with pytest.raises(StarLightCodecError):
        decode_slb1(b"NOPE" + encoded.artifact[4:])


def test_file_cli_helpers_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    artifact = tmp_path / "source.slb1"
    output = tmp_path / "output.bin"
    source.write_bytes(b"abc123" * 200)

    encode_meta = encode_file(source, artifact, max_passes=2)
    assert artifact.exists()
    assert "inputDigest" in encode_meta

    inspect_meta = inspect_slb1(artifact.read_bytes())
    assert inspect_meta["payloadDigestMatch"] is True

    decode_meta = decode_file(artifact, output)
    assert decode_meta["digestMatch"] is True
    assert output.read_bytes() == source.read_bytes()


def test_header_is_json_and_payload_is_raw() -> None:
    encoded = encode_slb1(b"abc" * 100, max_passes=2)
    header_len = int.from_bytes(encoded.artifact[4:8], "little")
    header = json.loads(encoded.artifact[16 : 16 + header_len].decode("utf-8"))

    assert header["container"] == "slb1"
    assert header["schemaVersion"] == 2
    assert header["packageKind"] == "starlight-byte-exact"
    assert header["codec"] == "starlight-byte-exact"
    assert header["layers"][0]["encoding"] == "raw"
    assert "data" not in header["layers"][0]


def test_container_field_is_optional_for_starlight_slb1_headers() -> None:
    encoded = encode_slb1(b"abc" * 100, max_passes=2)
    header_len = int.from_bytes(encoded.artifact[4:8], "little")
    payload_len = int.from_bytes(encoded.artifact[8:16], "little")
    header = json.loads(encoded.artifact[16 : 16 + header_len].decode("utf-8"))
    header.pop("container", None)
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload = encoded.artifact[16 + header_len :]
    starlight_like = (
        MAGIC_SLB1
        + len(header_bytes).to_bytes(4, "little")
        + payload_len.to_bytes(8, "little")
        + header_bytes
        + payload
    )

    assert decode_slb1(starlight_like).data == b"abc" * 100


def test_top_level_data_is_rejected_before_metadata_echo() -> None:
    encoded = encode_slb1(b"abc" * 100, max_passes=2)
    header_len = int.from_bytes(encoded.artifact[4:8], "little")
    payload_len = int.from_bytes(encoded.artifact[8:16], "little")
    header = json.loads(encoded.artifact[16 : 16 + header_len].decode("utf-8"))
    header["data"] = "must-not-be-echoed"
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload = encoded.artifact[16 + header_len :]
    tampered = (
        MAGIC_SLB1
        + len(header_bytes).to_bytes(4, "little")
        + payload_len.to_bytes(8, "little")
        + header_bytes
        + payload
    )

    with pytest.raises(StarLightCodecError):
        inspect_slb1(tampered)

    with pytest.raises(StarLightCodecError):
        decode_slb1(tampered)


def test_delta_prev_model_round_trips_and_records_model() -> None:
    data = bytes((index * 3) % 256 for index in range(8192))
    encoded = encode_slb1(data, max_passes=2, model="delta-prev-v1")

    assert encoded.metadata["selectedModel"] == "delta-prev-v1"
    assert encoded.metadata["predictionModel"]["modelId"] == "delta-prev-v1"
    assert encoded.metadata["strategy"] == "delta-prev-gzip-base64"
    assert encoded.metadata["transforms"] == ["delta-prev-v1", "gzip"]
    assert encoded.metadata["payloadBytes"] < len(data)
    assert decode_slb1(encoded.artifact).data == data


def test_auto_model_selects_delta_when_it_wins() -> None:
    data = bytes((index * 3) % 256 for index in range(8192))
    encoded = encode_slb1(data, max_passes=2, model="auto")

    assert encoded.metadata["selectedModel"] == "delta-prev-v1"
    assert decode_slb1(encoded.artifact).data == data


def test_auto_model_compares_whole_artifact_size() -> None:
    data = bytes((index * 3) % 256 for index in range(24))
    baseline = encode_slb1(data, max_passes=2, model="none")
    modeled = encode_slb1(data, max_passes=2, model="delta-prev-v1")
    encoded = encode_slb1(data, max_passes=2, model="auto")

    assert modeled.metadata["payloadBytes"] < baseline.metadata["payloadBytes"]
    assert len(modeled.artifact) > len(baseline.artifact)
    assert encoded.metadata["selectedModel"] == "none"
    assert encoded.artifact == baseline.artifact
    assert decode_slb1(encoded.artifact).data == data


def test_stdlib_auto_planner_selects_supported_transform() -> None:
    rows = [
        {
            "event": "codec.transport",
            "index": index,
            "level": "info" if index % 7 else "debug",
            "project": "star-light-codec",
            "tags": ["exact-roundtrip", "llm-transport", f"bucket-{index % 8}"],
        }
        for index in range(768)
    ]
    data = (
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows)
        + "\n"
    ).encode("utf-8")
    baseline = encode_slb1(data, max_passes=2)
    encoded = encode_slb1(data, max_passes=2, planner="stdlib-auto")

    assert encoded.metadata["selectedPlanner"] == "stdlib-auto"
    assert encoded.metadata["strategy"] in {
        "zlib-base64",
        "bz2-base64",
        "lzma-base64",
    }
    assert len(encoded.artifact) <= len(baseline.artifact)
    assert decode_slb1(encoded.artifact).data == data


def test_stdlib_auto_planner_works_with_model_auto() -> None:
    data = bytes((index * 3) % 256 for index in range(8192))
    encoded = encode_slb1(data, max_passes=2, model="auto", planner="stdlib-auto")

    assert encoded.metadata["selectedPlanner"] == "stdlib-auto"
    assert encoded.metadata["selectedModel"] in {"none", "delta-prev-v1"}
    assert decode_slb1(encoded.artifact).data == data


def test_unsupported_planner_is_rejected() -> None:
    with pytest.raises(StarLightCodecError):
        encode_slb1(b"abc", planner="quantum")


def test_strategy_must_match_transform_stack() -> None:
    encoded = encode_slb1(b"abc" * 100, max_passes=2, planner="stdlib-auto")
    header_len = int.from_bytes(encoded.artifact[4:8], "little")
    payload_len = int.from_bytes(encoded.artifact[8:16], "little")
    header = json.loads(encoded.artifact[16 : 16 + header_len].decode("utf-8"))
    header["strategy"] = "stored-base64"
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload = encoded.artifact[16 + header_len :]
    tampered = (
        MAGIC_SLB1
        + len(header_bytes).to_bytes(4, "little")
        + payload_len.to_bytes(8, "little")
        + header_bytes
        + payload
    )

    with pytest.raises(StarLightCodecError):
        decode_slb1(tampered)


def test_unsupported_model_is_rejected() -> None:
    with pytest.raises(StarLightCodecError):
        encode_slb1(b"abc", model="tiny-transformer")


def test_prediction_model_hash_mismatch_fails_closed() -> None:
    encoded = encode_slb1(
        bytes((index * 3) % 256 for index in range(1024)),
        max_passes=2,
        model="delta-prev-v1",
    )
    header_len = int.from_bytes(encoded.artifact[4:8], "little")
    payload_len = int.from_bytes(encoded.artifact[8:16], "little")
    header = json.loads(encoded.artifact[16 : 16 + header_len].decode("utf-8"))
    header["predictionModel"]["modelHash"] = "sha256:" + ("0" * 64)
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload = encoded.artifact[16 + header_len :]
    tampered = (
        MAGIC_SLB1
        + len(header_bytes).to_bytes(4, "little")
        + payload_len.to_bytes(8, "little")
        + header_bytes
        + payload
    )

    with pytest.raises(StarLightCodecError):
        decode_slb1(tampered)


def test_invalid_prediction_model_metadata_fails_closed() -> None:
    encoded = encode_slb1(
        bytes((index * 3) % 256 for index in range(1024)),
        max_passes=2,
        model="delta-prev-v1",
    )
    header_len = int.from_bytes(encoded.artifact[4:8], "little")
    payload_len = int.from_bytes(encoded.artifact[8:16], "little")
    header = json.loads(encoded.artifact[16 : 16 + header_len].decode("utf-8"))
    header["predictionModel"] = "delta-prev-v1"
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload = encoded.artifact[16 + header_len :]
    tampered = (
        MAGIC_SLB1
        + len(header_bytes).to_bytes(4, "little")
        + payload_len.to_bytes(8, "little")
        + header_bytes
        + payload
    )

    with pytest.raises(StarLightCodecError):
        decode_slb1(tampered)


def test_capsule_manifest_and_chunk_hydration(tmp_path: Path) -> None:
    data = b"alpha-" * 300 + b"beta-" * 300 + b"gamma-" * 300
    source = tmp_path / "source.bin"
    artifact = tmp_path / "source.slb1"
    capsule = tmp_path / "source.capsule.json"
    output = tmp_path / "chunk.bin"
    source.write_bytes(data)

    meta = create_capsule_file(
        source,
        artifact,
        capsule,
        max_passes=2,
        summary="Synthetic fixture for LLM transport.",
        tags=["transport", "codec-test", "transport"],
        chunk_size=512,
    )
    capsule_doc = json.loads(capsule.read_text(encoding="utf-8"))
    capsule_text = capsule.read_text(encoding="utf-8")

    assert meta["action"] == "capsule"
    assert meta["chunkCount"] > 1
    assert capsule_doc["kind"] == "slc-llm-transport"
    assert capsule_doc["artifactRef"] == "source.slb1"
    assert capsule_doc["semanticTags"] == ["codec-test", "transport"]
    assert "alpha-alpha-alpha" not in capsule_text

    chunk = capsule_doc["chunkIndex"][1]
    hydrate_meta = hydrate_file(capsule, output, chunk_id=chunk["chunkId"])

    assert hydrate_meta["hydrateMode"] == "chunk"
    assert output.read_bytes() == data[chunk["start"] : chunk["end"]]


def test_direct_range_hydration(tmp_path: Path) -> None:
    data = b"0123456789abcdef"
    source = tmp_path / "source.bin"
    artifact = tmp_path / "source.slb1"
    output = tmp_path / "range.bin"
    source.write_bytes(data)
    encode_file(source, artifact, max_passes=1)

    meta = hydrate_file(artifact, output, byte_range="4:10")

    assert meta["hydrateMode"] == "range"
    assert output.read_bytes() == b"456789"


def test_invalid_hydration_range_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    artifact = tmp_path / "source.slb1"
    output = tmp_path / "range.bin"
    source.write_bytes(b"small")
    encode_file(source, artifact, max_passes=1)

    with pytest.raises(StarLightCodecError):
        hydrate_file(artifact, output, byte_range="3:99")


def test_capsule_chunk_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    artifact = tmp_path / "source.slb1"
    capsule = tmp_path / "source.capsule.json"
    output = tmp_path / "chunk.bin"
    source.write_bytes(b"chunk-digest-fixture" * 200)
    create_capsule_file(source, artifact, capsule, max_passes=2, chunk_size=128)
    capsule_doc = json.loads(capsule.read_text(encoding="utf-8"))
    capsule_doc["chunkIndex"][0]["digest"] = "sha256:" + ("0" * 64)
    capsule.write_text(json.dumps(capsule_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(StarLightCodecError):
        hydrate_file(capsule, output, chunk_id="c0001")
