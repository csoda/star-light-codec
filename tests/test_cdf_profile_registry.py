from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from starlight_codec.cdf_oracle import (
    CDF_PROFILE_DECODE_CONTRACT,
    CDF_TOTAL,
    CODER_ID,
    CONTEXT_WINDOW,
    PPM_CONTEXT_WINDOW,
    PPM_PROFILE_DECODE_CONTRACT,
    PPM_PROFILE_HASH,
    PPM_PROFILE_ID,
    PROFILE_HASH,
    PROFILE_ID,
)
from starlight_codec.cdf_profile_registry import (
    CdfProfileRegistryError,
    canonical_json_bytes,
    descriptor_digest,
    load_profile_descriptor,
    profile_hash,
    validate_profile_descriptor,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DESCRIPTOR = REPO_ROOT / "profiles" / "byte-context-cdf-v0.json"
PPM_DESCRIPTOR = REPO_ROOT / "profiles" / "byte-ppm-context-v0.json"


def _sample_descriptor() -> dict[str, object]:
    return load_profile_descriptor(SAMPLE_DESCRIPTOR)


def _with_current_descriptor_digest(descriptor: dict[str, object]) -> dict[str, object]:
    updated = deepcopy(descriptor)
    updated["descriptorDigest"] = descriptor_digest(updated)
    return updated


def _with_current_decode_hashes(descriptor: dict[str, object]) -> dict[str, object]:
    updated = deepcopy(descriptor)
    decode_contract = updated["decodeContract"]
    assert isinstance(decode_contract, dict)
    updated["profileHash"] = profile_hash(decode_contract)
    updated["descriptorDigest"] = descriptor_digest(updated)
    return updated


def _add_nested_unknown_field(
    descriptor: dict[str, object], path: tuple[str | int, ...]
) -> None:
    target: object = descriptor
    for part in path:
        if isinstance(part, int):
            assert isinstance(target, list)
            target = target[part]
        else:
            assert isinstance(target, dict)
            target = target[part]
    assert isinstance(target, dict)
    target["unknownDecodeBehavior"] = "fail-closed"


def test_canonical_json_sorts_keys_and_compacts_utf8() -> None:
    assert canonical_json_bytes({"b": [2, 1], "a": "雪"}) == (
        b'{"a":"\xe9\x9b\xaa","b":[2,1]}'
    )


def test_canonical_json_rejects_floats() -> None:
    with pytest.raises(CdfProfileRegistryError):
        canonical_json_bytes({"decodeCritical": 1.25})


def test_sample_descriptor_validates_and_matches_oracle_metadata() -> None:
    descriptor = _sample_descriptor()
    validation = validate_profile_descriptor(descriptor)

    assert validation.profile_id == PROFILE_ID
    assert validation.profile_hash == PROFILE_HASH
    assert validation.coder_id == CODER_ID
    assert validation.cdf_total == CDF_TOTAL
    assert validation.context_window == CONTEXT_WINDOW
    assert descriptor["decodeContract"] == CDF_PROFILE_DECODE_CONTRACT
    assert profile_hash(descriptor["decodeContract"]) == PROFILE_HASH


def test_ppm_descriptor_validates_and_matches_oracle_metadata() -> None:
    descriptor = load_profile_descriptor(PPM_DESCRIPTOR)
    validation = validate_profile_descriptor(descriptor)

    assert validation.profile_id == PPM_PROFILE_ID
    assert validation.profile_hash == PPM_PROFILE_HASH
    assert validation.coder_id == CODER_ID
    assert validation.cdf_total == CDF_TOTAL
    assert validation.context_window == PPM_CONTEXT_WINDOW
    assert descriptor["decodeContract"] == PPM_PROFILE_DECODE_CONTRACT
    assert profile_hash(descriptor["decodeContract"]) == PPM_PROFILE_HASH


@pytest.mark.parametrize(
    "descriptor_path",
    [
        SAMPLE_DESCRIPTOR,
        PPM_DESCRIPTOR,
    ],
)
def test_checked_in_descriptors_validate(descriptor_path: Path) -> None:
    descriptor = load_profile_descriptor(descriptor_path)

    assert validate_profile_descriptor(descriptor).profile_hash == descriptor["profileHash"]


@pytest.mark.parametrize(
    "field",
    [
        "profileHash",
        "descriptorDigest",
    ],
)
def test_descriptor_digest_tampering_fails_closed(field: str) -> None:
    descriptor = _sample_descriptor()
    descriptor[field] = "sha256:" + ("0" * 64)

    with pytest.raises(CdfProfileRegistryError):
        validate_profile_descriptor(descriptor)


def test_descriptor_digest_ignores_descriptor_digest_self_field() -> None:
    descriptor = _sample_descriptor()
    original_digest = descriptor_digest(descriptor)
    descriptor["descriptorDigest"] = "sha256:" + ("0" * 64)

    assert descriptor_digest(descriptor) == original_digest


def test_unsupported_status_fails_closed_even_with_matching_digest() -> None:
    descriptor = _sample_descriptor()
    descriptor["status"] = "revoked"
    descriptor = _with_current_descriptor_digest(descriptor)

    with pytest.raises(CdfProfileRegistryError):
        validate_profile_descriptor(descriptor)


@pytest.mark.parametrize(
    "path",
    [
        ("decodeContract",),
        ("decodeContract", "context"),
        ("decodeContract", "frequencyModel"),
        ("decodeContract", "frequencyToCdf"),
        ("decodeContract", "entropyCoder"),
        ("decodeContract", "resourceLimits"),
        ("decodeContract", "goldenVectors", 0),
    ],
)
def test_unknown_decode_critical_keys_fail_closed_after_rehash(
    path: tuple[str | int, ...],
) -> None:
    descriptor = _sample_descriptor()
    _add_nested_unknown_field(descriptor, path)
    descriptor = _with_current_decode_hashes(descriptor)

    with pytest.raises(CdfProfileRegistryError, match="unknown .* fields"):
        validate_profile_descriptor(descriptor)


@pytest.mark.parametrize(
    "path",
    [
        ("decodeContract", "context"),
        ("decodeContract", "frequencyModel"),
    ],
)
def test_unknown_ppm_decode_critical_keys_fail_closed_after_rehash(
    path: tuple[str | int, ...],
) -> None:
    descriptor = load_profile_descriptor(PPM_DESCRIPTOR)
    _add_nested_unknown_field(descriptor, path)
    descriptor = _with_current_decode_hashes(descriptor)

    with pytest.raises(CdfProfileRegistryError, match="unknown .* fields"):
        validate_profile_descriptor(descriptor)


def test_decode_contract_float_fails_closed() -> None:
    descriptor = _sample_descriptor()
    decode_contract = descriptor["decodeContract"]
    assert isinstance(decode_contract, dict)
    resource_limits = decode_contract["resourceLimits"]
    assert isinstance(resource_limits, dict)
    resource_limits["maxDecodeMemoryBytes"] = 1048576.5

    with pytest.raises(CdfProfileRegistryError):
        validate_profile_descriptor(descriptor)


def test_cli_profile_validate_outputs_json_and_success() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "profile",
            "validate",
            str(SAMPLE_DESCRIPTOR),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["profileId"] == PROFILE_ID
    assert payload["profileHash"] == PROFILE_HASH


@pytest.mark.parametrize("profile_command", ["validate", "show"])
def test_cli_profile_invalid_input_outputs_json_error(
    profile_command: str, tmp_path: Path
) -> None:
    invalid_descriptor = tmp_path / "invalid-profile.json"
    invalid_descriptor.write_text(
        json.dumps({"schema": "slc-cdf-profile-registry-v0"}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "profile",
            profile_command,
            str(invalid_descriptor),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert "missing required descriptor fields" in payload["error"]
