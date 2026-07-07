from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from starlight_codec.cdf_oracle import (
    CDF_TOTAL,
    PPM_BASE_FREQUENCY,
    PPM_CONTEXT_WINDOW,
    PPM_MATCH_SCALE,
    PPM_MATCH_SCALE_BY_ORDER,
    PPM_MAX_ORDER,
    PPM_PROFILE_DECODE_CONTRACT,
    PPM_PROFILE_HASH,
    PPM_PROFILE_ID,
    PPM_RECENCY_SCALE,
    PPM_RECENCY_WINDOW,
    CdfOracleError,
    cdf_for_ppm_context,
    decode_cdf_oracle,
    encode_cdf_oracle,
)
from starlight_codec.cdf_profile_registry import profile_hash


REPO_ROOT = Path(__file__).resolve().parents[1]


def _json_fixture(rows: int = 48) -> bytes:
    return (
        "\n".join(
            json.dumps(
                {
                    "event": "cdf.profile",
                    "index": index % 8,
                    "status": "ok",
                    "tags": ["ppm", "cdf", "test"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            for index in range(rows)
        )
        + "\n"
    ).encode("utf-8")


def test_ppm_profile_hash_is_registry_decode_contract_hash() -> None:
    assert PPM_PROFILE_HASH == profile_hash(PPM_PROFILE_DECODE_CONTRACT)


def test_ppm_cdf_is_deterministic_positive_and_totaled() -> None:
    context = (b"alpha beta alpha beta gamma alpha beta " * 8) + b"\x00\xff"

    first = cdf_for_ppm_context(context)
    second = cdf_for_ppm_context(context)

    assert first == second
    assert len(first) == 257
    assert first[0] == 0
    assert first[-1] == CDF_TOTAL
    freqs = [right - left for left, right in zip(first, first[1:])]
    assert all(freq > 0 for freq in freqs)
    assert all(left < right for left, right in zip(first, first[1:]))


def test_ppm_profile_metadata_records_decode_critical_parameters() -> None:
    encoded = encode_cdf_oracle(b"abcabcabcabc" * 12, profile_id=PPM_PROFILE_ID)

    assert encoded.metadata["profileId"] == PPM_PROFILE_ID
    assert encoded.metadata["profileHash"] == PPM_PROFILE_HASH
    assert encoded.metadata["contextWindow"] == PPM_CONTEXT_WINDOW
    assert encoded.metadata["maxOrder"] == PPM_MAX_ORDER
    assert encoded.metadata["baseFrequency"] == PPM_BASE_FREQUENCY
    assert encoded.metadata["recencyWindow"] == PPM_RECENCY_WINDOW
    assert encoded.metadata["recencyScale"] == PPM_RECENCY_SCALE
    assert encoded.metadata["matchScale"] == PPM_MATCH_SCALE
    assert encoded.metadata["matchScaleByOrder"] == PPM_MATCH_SCALE_BY_ORDER


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"Star Light CDF oracle profile test. " * 24,
        b"def score(value):\n    return (value * 17) % 251\n\n" * 24,
        _json_fixture(),
        bytes([0, 0, 0, 0, 1, 0, 0, 2, 0, 0, 3, 255]) * 40,
    ],
)
def test_ppm_profile_round_trips_representative_bytes(data: bytes) -> None:
    encoded = encode_cdf_oracle(data, profile_id=PPM_PROFILE_ID)

    assert decode_cdf_oracle(encoded.payload, encoded.metadata) == data
    assert encoded.metadata["payloadBytes"] == len(encoded.payload)
    assert encoded.metadata["standalonePrototype"] is True
    assert encoded.metadata["productionSlb1Compatible"] is False


def test_ppm_profile_beats_toy_profile_on_repeated_structured_fixtures() -> None:
    fixtures = [
        b"Star Light CDF oracle profile test. " * 24,
        b"def score(value):\n    return (value * 17) % 251\n\n" * 24,
        _json_fixture(),
    ]

    for data in fixtures:
        toy = encode_cdf_oracle(data)
        ppm = encode_cdf_oracle(data, profile_id=PPM_PROFILE_ID)
        assert ppm.metadata["payloadRatio"] < toy.metadata["payloadRatio"] * 0.25


def test_ppm_profile_specific_metadata_tampering_fails_closed() -> None:
    encoded = encode_cdf_oracle(b"metadata-tamper " * 32, profile_id=PPM_PROFILE_ID)
    tampered = dict(encoded.metadata)
    tampered["maxOrder"] = PPM_MAX_ORDER + 1

    with pytest.raises(CdfOracleError):
        decode_cdf_oracle(encoded.payload, tampered)


def test_cli_cdf_encode_decode_round_trips_ppm_profile(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    payload = tmp_path / "source.cdf"
    metadata = tmp_path / "source.cdf.json"
    output = tmp_path / "output.txt"
    source.write_bytes(b"cli cdf ppm profile roundtrip\n" * 12)

    encode_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "cdf",
            "encode",
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

    assert encode_result.returncode == 0, encode_result.stdout + encode_result.stderr
    encode_summary = json.loads(encode_result.stdout)
    assert encode_summary["ok"] is True
    assert encode_summary["profileId"] == PPM_PROFILE_ID
    metadata_doc = json.loads(metadata.read_text(encoding="utf-8"))
    assert metadata_doc["profileId"] == PPM_PROFILE_ID

    decode_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "cdf",
            "decode",
            str(payload),
            str(metadata),
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert decode_result.returncode == 0, decode_result.stdout + decode_result.stderr
    decode_summary = json.loads(decode_result.stdout)
    assert decode_summary["ok"] is True
    assert decode_summary["profileId"] == PPM_PROFILE_ID
    assert output.read_bytes() == source.read_bytes()


def test_cli_cdf_encode_rejects_unknown_profile_as_json(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    payload = tmp_path / "source.cdf"
    metadata = tmp_path / "source.cdf.json"
    source.write_bytes(b"unknown profile")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "cdf",
            "encode",
            str(source),
            str(payload),
            str(metadata),
            "--profile",
            "missing-cdf-profile",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert summary["ok"] is False
    assert "unsupported CDF oracle profile" in summary["error"]
