from __future__ import annotations

import copy
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

import starlight_codec.cdf_oracle as cdf_oracle
from starlight_codec.cdf_oracle import (
    CDF_PACK_KIND,
    CDF_PACK_MAX_RAW_BYTES,
    PPM_PROFILE_ID,
    CdfOracleError,
    open_cdf_oracle_pack,
    pack_cdf_oracle,
    sha256_digest,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _compact_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _restabilize_pack_metadata(metadata: dict[str, object]) -> None:
    for _ in range(12):
        metadata_bytes = len(_compact_json_bytes(metadata))
        whole_package_bytes = metadata["payloadBytes"] + metadata_bytes
        if (
            metadata["metadataBytes"] == metadata_bytes
            and metadata["wholePackageBytes"] == whole_package_bytes
        ):
            return
        metadata["metadataBytes"] = metadata_bytes
        metadata["wholePackageBytes"] = whole_package_bytes
    raise AssertionError("test metadata size did not stabilize")


def _recompute_candidate_summary_gates(metadata: dict[str, object]) -> None:
    raw_bytes = metadata["rawBytes"]
    min_saving_bytes = metadata["minSavingBytes"]
    for summary in metadata["candidateSummaries"]:
        whole_bytes = summary["estimatedWholeBytes"]
        saving = raw_bytes - whole_bytes
        summary["savingBytesVsRaw"] = saving
        summary["passesBenefitGate"] = summary["codec"] != "stored" and (
            saving >= min_saving_bytes
        )


def _sync_summary_to_top_level(metadata: dict[str, object], codec: str) -> None:
    for _ in range(12):
        summary = _summary_for(metadata, codec)
        summary["estimatedMetadataBytes"] = metadata["metadataBytes"]
        summary["estimatedWholeBytes"] = metadata["wholePackageBytes"]
        _recompute_candidate_summary_gates(metadata)
        _restabilize_pack_metadata(metadata)
        if (
            summary["estimatedMetadataBytes"] == metadata["metadataBytes"]
            and summary["estimatedWholeBytes"] == metadata["wholePackageBytes"]
        ):
            return
    raise AssertionError("test selected summary size did not stabilize")


def _locally_predictive_bytes(size: int = 10_240) -> bytes:
    rng = random.Random(123)
    data = bytearray()
    for _ in range(size // 2):
        key = rng.randrange(256)
        data.append(key)
        data.append(key ^ 0xA5)
    return bytes(data)


def _random_bytes(size: int = 1024) -> bytes:
    rng = random.Random(123)
    return bytes(rng.randrange(256) for _ in range(size))


def _repeated_code_fixture(repetitions: int = 32) -> bytes:
    return b"def score(value):\n    return (value * 17) % 251\n\n" * repetitions


def _summary_for(metadata: dict[str, object], codec: str) -> dict[str, object]:
    for summary in metadata["candidateSummaries"]:
        if summary["codec"] == codec:
            return summary
    raise AssertionError(f"missing candidate summary for {codec}")


def test_pack_selects_ppm_for_locally_predictive_bytes_and_round_trips() -> None:
    data = _locally_predictive_bytes()

    packed = pack_cdf_oracle(data, profiles=(PPM_PROFILE_ID,))

    assert packed.metadata["packageKind"] == CDF_PACK_KIND
    assert packed.metadata["selectedCodec"] == "cdf-oracle"
    assert packed.metadata["selectedProfileId"] == PPM_PROFILE_ID
    assert packed.metadata["recommendedForStorage"] is True
    assert packed.metadata["wholePackageBytes"] <= len(data) - 1
    assert _summary_for(packed.metadata, "zlib")["codec"] == "zlib"
    assert open_cdf_oracle_pack(packed.payload, packed.metadata) == data


def test_pack_selects_zlib_when_it_beats_ppm_whole_package() -> None:
    data = _repeated_code_fixture()

    packed = pack_cdf_oracle(data, profiles=(PPM_PROFILE_ID,))

    assert packed.metadata["selectedCodec"] == "zlib"
    assert packed.metadata["recommendedForStorage"] is True
    assert _summary_for(packed.metadata, "zlib")["estimatedWholeBytes"] < _summary_for(
        packed.metadata, "cdf-oracle"
    )["estimatedWholeBytes"]
    assert open_cdf_oracle_pack(packed.payload, packed.metadata) == data


def test_pack_uses_stored_fallback_for_random_bytes() -> None:
    data = _random_bytes()

    packed = pack_cdf_oracle(data, profiles=(PPM_PROFILE_ID,))

    assert packed.metadata["selectedCodec"] == "stored"
    assert packed.metadata["recommendedForStorage"] is False
    assert "no candidate saved" in packed.metadata["fallbackReason"]
    assert _summary_for(packed.metadata, "cdf-oracle")["passesBenefitGate"] is False
    assert open_cdf_oracle_pack(packed.payload, packed.metadata) == data


def test_pack_rejects_oversized_input_before_candidate_work(monkeypatch) -> None:
    def fail_compress(data: bytes, level: int) -> bytes:
        raise AssertionError("zlib compression should not start")

    def fail_encode(data: bytes, *, profile_id: str) -> object:
        raise AssertionError("CDF oracle encoding should not start")

    monkeypatch.setattr(cdf_oracle.zlib, "compress", fail_compress)
    monkeypatch.setattr(cdf_oracle, "encode_cdf_oracle", fail_encode)

    with pytest.raises(CdfOracleError, match="rawBytes exceeds resource limit"):
        pack_cdf_oracle(b"x" * (CDF_PACK_MAX_RAW_BYTES + 1), profiles=(PPM_PROFILE_ID,))


def test_pack_tampered_payload_digest_fails_closed() -> None:
    packed = pack_cdf_oracle(_repeated_code_fixture(), profiles=(PPM_PROFILE_ID,))
    tampered = copy.deepcopy(packed.metadata)
    tampered["payloadDigest"] = sha256_digest(b"not the selected payload")

    with pytest.raises(CdfOracleError):
        open_cdf_oracle_pack(packed.payload, tampered)


def test_pack_tampered_selected_codec_fails_closed() -> None:
    packed = pack_cdf_oracle(_repeated_code_fixture(), profiles=(PPM_PROFILE_ID,))
    tampered = copy.deepcopy(packed.metadata)
    tampered["selectedCodec"] = "stored"

    with pytest.raises(CdfOracleError):
        open_cdf_oracle_pack(packed.payload, tampered)


def test_pack_tampered_candidate_whole_size_fails_closed() -> None:
    packed = pack_cdf_oracle(_repeated_code_fixture(), profiles=(PPM_PROFILE_ID,))
    tampered = copy.deepcopy(packed.metadata)
    _summary_for(tampered, "zlib")["estimatedWholeBytes"] += 1
    _restabilize_pack_metadata(tampered)

    with pytest.raises(CdfOracleError, match="whole size summary mismatch"):
        open_cdf_oracle_pack(packed.payload, tampered)


def test_pack_tampered_selected_candidate_package_sizes_fail_closed() -> None:
    packed = pack_cdf_oracle(_repeated_code_fixture(), profiles=(PPM_PROFILE_ID,))
    tampered = copy.deepcopy(packed.metadata)
    summary = _summary_for(tampered, "zlib")
    assert summary["estimatedMetadataBytes"] == tampered["metadataBytes"]
    assert summary["estimatedWholeBytes"] == tampered["wholePackageBytes"]

    summary["estimatedMetadataBytes"] += 10
    summary["estimatedWholeBytes"] += 10
    _recompute_candidate_summary_gates(tampered)
    _restabilize_pack_metadata(tampered)

    with pytest.raises(CdfOracleError, match="selected .* size summary mismatch"):
        open_cdf_oracle_pack(packed.payload, tampered)


def test_pack_tampered_candidate_saving_fails_closed() -> None:
    packed = pack_cdf_oracle(_repeated_code_fixture(), profiles=(PPM_PROFILE_ID,))
    tampered = copy.deepcopy(packed.metadata)
    _summary_for(tampered, "zlib")["savingBytesVsRaw"] += 1
    _restabilize_pack_metadata(tampered)

    with pytest.raises(CdfOracleError, match="saving summary mismatch"):
        open_cdf_oracle_pack(packed.payload, tampered)


def test_pack_tampered_candidate_benefit_gate_fails_closed() -> None:
    packed = pack_cdf_oracle(_repeated_code_fixture(), profiles=(PPM_PROFILE_ID,))
    tampered = copy.deepcopy(packed.metadata)
    _summary_for(tampered, "zlib")["passesBenefitGate"] = False
    _restabilize_pack_metadata(tampered)

    with pytest.raises(CdfOracleError, match="benefit gate summary mismatch"):
        open_cdf_oracle_pack(packed.payload, tampered)


def test_pack_tampered_selected_recommendation_fails_closed() -> None:
    packed = pack_cdf_oracle(_repeated_code_fixture(), profiles=(PPM_PROFILE_ID,))
    tampered = copy.deepcopy(packed.metadata)
    tampered["recommendedForStorage"] = False
    _restabilize_pack_metadata(tampered)
    _sync_summary_to_top_level(tampered, "zlib")

    with pytest.raises(CdfOracleError, match="contradicts recommendation"):
        open_cdf_oracle_pack(packed.payload, tampered)


def test_pack_rejects_huge_raw_bytes_before_zlib_decompress(monkeypatch) -> None:
    packed = pack_cdf_oracle(_repeated_code_fixture(), profiles=(PPM_PROFILE_ID,))
    tampered = copy.deepcopy(packed.metadata)
    tampered["rawBytes"] = CDF_PACK_MAX_RAW_BYTES + 1
    tampered["inputDigest"] = sha256_digest(b"tampered oversized package")
    _restabilize_pack_metadata(tampered)

    def fail_if_called(payload: bytes, expected_raw_bytes: int) -> bytes:
        raise AssertionError("zlib decompression should not start")

    monkeypatch.setattr(cdf_oracle, "_decompress_zlib_bounded", fail_if_called)

    with pytest.raises(CdfOracleError, match="rawBytes exceeds resource limit"):
        open_cdf_oracle_pack(packed.payload, tampered)


def test_pack_bounds_zlib_decompression_to_declared_raw_bytes() -> None:
    packed = pack_cdf_oracle(_repeated_code_fixture(), profiles=(PPM_PROFILE_ID,))
    tampered = copy.deepcopy(packed.metadata)
    tampered["rawBytes"] = 1
    tampered["recommendedForStorage"] = False
    tampered["inputDigest"] = sha256_digest(b"not the full decompressed payload")
    _recompute_candidate_summary_gates(tampered)
    _restabilize_pack_metadata(tampered)
    _sync_summary_to_top_level(tampered, "zlib")

    with pytest.raises(CdfOracleError, match="exceeds declared rawBytes"):
        open_cdf_oracle_pack(packed.payload, tampered)


def test_pack_rejects_huge_oracle_input_bytes_before_decode(monkeypatch) -> None:
    packed = pack_cdf_oracle(_locally_predictive_bytes(), profiles=(PPM_PROFILE_ID,))
    assert packed.metadata["selectedCodec"] == "cdf-oracle"
    tampered = copy.deepcopy(packed.metadata)
    tampered["oracle"]["inputBytes"] = CDF_PACK_MAX_RAW_BYTES + 1
    _restabilize_pack_metadata(tampered)

    def fail_if_called(payload: bytes, metadata: dict[str, object]) -> bytes:
        raise AssertionError("CDF oracle decode should not start")

    monkeypatch.setattr(cdf_oracle, "decode_cdf_oracle", fail_if_called)

    with pytest.raises(CdfOracleError, match="inputBytes exceeds resource limit"):
        open_cdf_oracle_pack(packed.payload, tampered)


def test_cli_cdf_pack_open_round_trips_and_reports_selected_codec(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    payload = tmp_path / "source.cdf-pack"
    metadata = tmp_path / "source.cdf-pack.json"
    output = tmp_path / "output.py"
    source.write_bytes(_repeated_code_fixture())

    pack_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "cdf",
            "pack",
            str(source),
            str(payload),
            str(metadata),
            "--profile",
            PPM_PROFILE_ID,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert pack_result.returncode == 0, pack_result.stdout + pack_result.stderr
    pack_summary = json.loads(pack_result.stdout)
    assert pack_summary["ok"] is True
    assert pack_summary["selectedCodec"] == "zlib"

    open_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "cdf",
            "open",
            str(payload),
            str(metadata),
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert open_result.returncode == 0, open_result.stdout + open_result.stderr
    open_summary = json.loads(open_result.stdout)
    assert open_summary["ok"] is True
    assert open_summary["selectedCodec"] == "zlib"
    assert output.read_bytes() == source.read_bytes()
