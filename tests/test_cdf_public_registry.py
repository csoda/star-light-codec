from __future__ import annotations

import json
import random
import subprocess
import sys
from importlib import resources
from pathlib import Path

import pytest

import starlight_codec.cdf_public_registry as public_registry
from starlight_codec.cdf_oracle import (
    PPM_PROFILE_ID,
    PROFILE_ID,
    open_cdf_oracle_pack,
    pack_cdf_oracle,
)
from starlight_codec.cdf_profile_registry import load_profile_descriptor, validate_profile_descriptor
from starlight_codec.cdf_public_registry import (
    CdfPublicRegistryError,
    auto_open_cdf_oracle_pack,
    auto_pack_cdf_oracle,
    fetch_public_component,
    fetch_public_profile_descriptor,
    list_public_components,
    list_public_profiles,
    plan_cdf_compression,
    plan_cdf_open_requirements,
    resolve_public_component,
    resolve_public_profile_descriptor,
    validate_public_component,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _locally_predictive_bytes(size: int = 10_240) -> bytes:
    rng = random.Random(123)
    data = bytearray()
    for _ in range(size // 2):
        key = rng.randrange(256)
        data.append(key)
        data.append(key ^ 0xA5)
    return bytes(data)


def _repeated_code_fixture(repetitions: int = 32) -> bytes:
    return b"def score(value):\n    return (value * 17) % 251\n\n" * repetitions


def _with_recomputed_component_digest(
    component: dict[str, object],
) -> dict[str, object]:
    mutated = dict(component)
    mutated["componentDigest"] = public_registry._component_digest(mutated)
    return mutated


def test_public_profile_list_resolves_and_fetches_with_digest_validation(
    tmp_path: Path,
) -> None:
    profiles = list_public_profiles()
    profile_ids = {profile["profileId"] for profile in profiles}

    assert PPM_PROFILE_ID in profile_ids
    descriptor = resolve_public_profile_descriptor(PPM_PROFILE_ID)
    validation = validate_profile_descriptor(descriptor)
    fetched = fetch_public_profile_descriptor(PPM_PROFILE_ID, tmp_path)
    fetched_descriptor = load_profile_descriptor(fetched["path"])

    assert fetched["descriptorDigest"] == validation.descriptor_digest
    assert validate_profile_descriptor(fetched_descriptor).descriptor_digest == fetched[
        "descriptorDigest"
    ]


def test_public_profile_descriptors_are_visible_package_resources() -> None:
    profile_resources = resources.files("starlight_codec").joinpath("profiles")
    resource_names = {
        resource.name for resource in profile_resources.iterdir() if resource.is_file()
    }

    assert f"{PROFILE_ID}.json" in resource_names
    assert f"{PPM_PROFILE_ID}.json" in resource_names

    for profile_id in (PROFILE_ID, PPM_PROFILE_ID):
        resource = profile_resources.joinpath(f"{profile_id}.json")
        descriptor = json.loads(resource.read_text(encoding="utf-8"))

        assert validate_profile_descriptor(descriptor).profile_id == profile_id


def test_public_profile_resolver_fetches_from_package_resources_without_repo_profiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_repo_profiles = tmp_path / "missing-repo-profiles"
    monkeypatch.setattr(
        public_registry,
        "_repo_profile_path",
        lambda profile_id: missing_repo_profiles / f"{profile_id}.json",
    )

    descriptor = resolve_public_profile_descriptor(PPM_PROFILE_ID)
    validation = validate_profile_descriptor(descriptor)
    fetched = fetch_public_profile_descriptor(PPM_PROFILE_ID, tmp_path / "cache")
    fetched_descriptor = load_profile_descriptor(fetched["path"])

    assert validation.profile_id == PPM_PROFILE_ID
    assert fetched["descriptorDigest"] == validation.descriptor_digest
    assert validate_profile_descriptor(fetched_descriptor).profile_hash == validation.profile_hash


def test_public_component_list_resolves_fetches_and_validates(tmp_path: Path) -> None:
    components = list_public_components(role="decoder")
    component_ids = {component["componentId"] for component in components}

    assert "cdf-oracle-decoder-v0" in component_ids
    component = resolve_public_component("integer-arithmetic-range-decoder-v0")
    fetched = fetch_public_component(component["componentId"], tmp_path)
    fetched_component = json.loads(Path(fetched["path"]).read_text(encoding="utf-8"))

    assert fetched["componentDigest"] == component["componentDigest"]
    assert validate_public_component(fetched_component)["componentDigest"] == component[
        "componentDigest"
    ]


@pytest.mark.parametrize(
    ("component_id", "updates", "removed"),
    [
        ("stored-bytes-encoder-v0", {"componentId": ""}, ()),
        ("stored-bytes-encoder-v0", {"componentVersion": True}, ()),
        ("stored-bytes-encoder-v0", {"componentKind": "nonsense-kind"}, ()),
        ("stored-bytes-encoder-v0", {"implementation": ""}, ()),
        ("stored-bytes-encoder-v0", {"role": "transcoder"}, ()),
        ("stored-bytes-encoder-v0", {"codec": "brotli"}, ()),
        ("stored-bytes-encoder-v0", {}, ("codec",)),
        ("zlib-level9-encoder-v0", {"zlibLevel": 6}, ()),
        ("cdf-oracle-encoder-v0", {"codec": "stored"}, ()),
        ("cdf-oracle-encoder-v0", {"requiresCoderId": "other-coder-v0"}, ()),
        ("cdf-oracle-encoder-v0", {}, ("requiresCoderId",)),
        ("integer-arithmetic-range-encoder-v0", {"coderId": "other-coder-v0"}, ()),
        ("integer-arithmetic-range-encoder-v0", {"stateBits": 64}, ()),
        ("integer-arithmetic-range-encoder-v0", {}, ("stateBits",)),
    ],
)
def test_public_component_validation_rejects_structural_invalidity_with_matching_digest(
    component_id: str,
    updates: dict[str, object],
    removed: tuple[str, ...],
) -> None:
    component = resolve_public_component(component_id)
    mutated = dict(component)
    mutated.update(updates)
    for field in removed:
        mutated.pop(field, None)
    mutated = _with_recomputed_component_digest(mutated)

    with pytest.raises(CdfPublicRegistryError):
        validate_public_component(mutated)


def test_plan_cdf_compression_auto_selects_profile_and_components() -> None:
    plan = plan_cdf_compression(
        _locally_predictive_bytes(),
        profiles=(PPM_PROFILE_ID,),
    )

    assert plan["selectedCodec"] == "cdf-oracle"
    assert plan["selectedProfileId"] == PPM_PROFILE_ID
    assert plan["recommendedForStorage"] is True
    assert "cdf-oracle-encoder-v0" in {
        component["componentId"] for component in plan["encoderComponents"]
    }
    assert plan["decodeRequirements"]["profiles"][0]["profileId"] == PPM_PROFILE_ID


def test_auto_pack_and_auto_open_round_trip_through_public_requirements(
    tmp_path: Path,
) -> None:
    data = _locally_predictive_bytes()
    cache_dir = tmp_path / "cache"

    packed = auto_pack_cdf_oracle(
        data,
        profiles=(PPM_PROFILE_ID,),
        cache_dir=cache_dir,
    )
    requirements = plan_cdf_open_requirements(packed.metadata)
    opened = auto_open_cdf_oracle_pack(
        packed.payload,
        packed.metadata,
        cache_dir=cache_dir,
    )

    assert opened == data
    assert requirements["profiles"][0]["profileId"] == PPM_PROFILE_ID
    assert (cache_dir / f"{PPM_PROFILE_ID}.json").is_file()
    assert (cache_dir / "cdf-oracle-decoder-v0.json").is_file()
    assert open_cdf_oracle_pack(packed.payload, packed.metadata) == data


def test_auto_open_missing_public_profile_fails_before_approximation() -> None:
    packed = pack_cdf_oracle(_locally_predictive_bytes(), profiles=(PPM_PROFILE_ID,))
    metadata = dict(packed.metadata)
    metadata["selectedProfileId"] = "missing-profile-v0"

    with pytest.raises(CdfPublicRegistryError, match="missing public profile"):
        auto_open_cdf_oracle_pack(packed.payload, metadata)


def test_auto_open_missing_public_decoder_fails_before_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    packed = pack_cdf_oracle(_repeated_code_fixture(), profiles=(PPM_PROFILE_ID,))
    assert packed.metadata["selectedCodec"] == "zlib"
    without_zlib_decoder = {
        key: value
        for key, value in public_registry._PUBLIC_COMPONENTS_BY_ID.items()
        if key != "zlib-level9-decoder-v0"
    }
    monkeypatch.setattr(
        public_registry,
        "_PUBLIC_COMPONENTS_BY_ID",
        without_zlib_decoder,
    )

    with pytest.raises(CdfPublicRegistryError, match="missing public decoder component"):
        auto_open_cdf_oracle_pack(packed.payload, packed.metadata)


def test_cli_public_registry_and_auto_pack_open_smoke(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    payload = tmp_path / "source.cdf-pack"
    metadata = tmp_path / "source.cdf-pack.json"
    output = tmp_path / "output.bin"
    cache = tmp_path / "cache"
    source.write_bytes(_locally_predictive_bytes())

    commands = [
        [sys.executable, "-m", "starlight_codec", "profile", "list"],
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "profile",
            "fetch",
            PPM_PROFILE_ID,
            str(cache),
        ],
        [sys.executable, "-m", "starlight_codec", "component", "list"],
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "component",
            "fetch",
            "cdf-oracle-decoder-v0",
            str(cache),
        ],
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "cdf",
            "plan",
            str(source),
            "--profile",
            PPM_PROFILE_ID,
        ],
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "cdf",
            "auto-pack",
            str(source),
            str(payload),
            str(metadata),
            "--profile",
            PPM_PROFILE_ID,
            "--cache-dir",
            str(cache),
        ],
        [
            sys.executable,
            "-m",
            "starlight_codec",
            "cdf",
            "auto-open",
            str(payload),
            str(metadata),
            str(output),
            "--cache-dir",
            str(cache),
        ],
    ]

    for command in commands:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["ok"] is True

    assert output.read_bytes() == source.read_bytes()
