from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from starlight_codec.codec import (
    MAGIC_SLB1,
    StarLightCodecError,
    decode_file,
    decode_slb1,
    encode_file,
    encode_slb1,
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
