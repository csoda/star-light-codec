from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Iterable

from .cdf_oracle import (
    CODER_ID,
    PPM_PROFILE_ID,
    PROFILE_ID,
    CdfOracleError,
    CdfOraclePackResult,
    open_cdf_oracle_pack,
    pack_cdf_oracle,
    sha256_digest,
)
from .cdf_profile_registry import (
    CdfProfileRegistryError,
    canonical_json_sha256,
    load_profile_descriptor,
    parse_profile_descriptor,
    validate_profile_descriptor,
)


PUBLIC_COMPONENT_SCHEMA_ID = "slc-public-component-v0"
DEFAULT_AUTO_PROFILE_IDS = (PPM_PROFILE_ID, PROFILE_ID)
COMPONENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$")
SUPPORTED_COMPONENT_KINDS = frozenset(
    {"terminal-codec", "profile-codec", "entropy-coder"}
)
SUPPORTED_COMPONENT_ROLES = frozenset({"encoder", "decoder"})
SUPPORTED_COMPONENT_STATUSES = frozenset({"experimental", "stable", "deprecated"})
SUPPORTED_TERMINAL_CODECS = frozenset({"stored", "zlib"})
SUPPORTED_PROFILE_CODECS = frozenset({"cdf-oracle"})
SUPPORTED_COMPONENT_BASE_FIELDS = frozenset(
    {
        "schema",
        "componentId",
        "componentVersion",
        "componentKind",
        "role",
        "status",
        "deterministic",
        "networkRequired",
        "implementation",
        "componentDigest",
    }
)


class CdfPublicRegistryError(ValueError):
    """Raised when bundled public profile/component resolution fails closed."""


@dataclass(frozen=True)
class _ProfileSource:
    location: str
    read_text: Callable[[], str]
    read_bytes: Callable[[], bytes]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _profiles_dir() -> Path:
    return _repo_root() / "profiles"


def _repo_profile_path(profile_id: str) -> Path:
    return _profiles_dir() / f"{profile_id}.json"


def _packaged_profile_resource(profile_id: str) -> Any:
    return resources.files("starlight_codec").joinpath(
        "profiles", f"{profile_id}.json"
    )


def _profile_source(profile_id: str) -> _ProfileSource:
    if profile_id not in _PUBLIC_PROFILE_FILENAMES:
        raise CdfPublicRegistryError(f"missing public profile: {profile_id}")

    resource = _packaged_profile_resource(profile_id)
    if resource.is_file():
        return _ProfileSource(
            location=f"starlight_codec/profiles/{profile_id}.json",
            read_text=lambda: resource.read_text(encoding="utf-8"),
            read_bytes=resource.read_bytes,
        )

    path = _repo_profile_path(profile_id)
    if path.is_file():
        return _ProfileSource(
            location=str(path),
            read_text=lambda: path.read_text(encoding="utf-8"),
            read_bytes=path.read_bytes,
        )

    raise CdfPublicRegistryError(f"missing bundled public profile file: {profile_id}")


def _load_public_profile_descriptor(
    profile_id: str,
) -> tuple[dict[str, Any], _ProfileSource]:
    source = _profile_source(profile_id)
    try:
        descriptor = parse_profile_descriptor(source.read_text())
        validation = validate_profile_descriptor(descriptor)
    except (CdfProfileRegistryError, OSError) as exc:
        raise CdfPublicRegistryError(
            f"public profile descriptor failed validation: {profile_id}: {exc}"
        ) from exc
    if validation.profile_id != profile_id:
        raise CdfPublicRegistryError("public profile descriptor id mismatch")
    return descriptor, source


def _component_digest(component: dict[str, Any]) -> str:
    digest_body = dict(component)
    digest_body.pop("componentDigest", None)
    return canonical_json_sha256(digest_body)


def _component(component: dict[str, Any]) -> dict[str, Any]:
    with_digest = dict(component)
    with_digest["componentDigest"] = _component_digest(with_digest)
    return with_digest


def _require_component_str(component: dict[str, Any], field: str) -> str:
    value = component.get(field)
    if not isinstance(value, str) or not value:
        raise CdfPublicRegistryError(f"component {field} must be a non-empty string")
    return value


def _require_component_non_negative_int(
    component: dict[str, Any], field: str
) -> int:
    value = component.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CdfPublicRegistryError(
            f"component {field} must be a non-negative integer"
        )
    return value


def _reject_unknown_component_fields(
    component: dict[str, Any], allowed_fields: Iterable[str]
) -> None:
    unknown = sorted(set(component) - set(allowed_fields))
    if unknown:
        raise CdfPublicRegistryError(f"unknown component fields: {unknown}")


_PUBLIC_PROFILE_FILENAMES = {
    PROFILE_ID: f"{PROFILE_ID}.json",
    PPM_PROFILE_ID: f"{PPM_PROFILE_ID}.json",
}

_PUBLIC_COMPONENTS = (
    _component(
        {
            "schema": PUBLIC_COMPONENT_SCHEMA_ID,
            "componentId": "stored-bytes-encoder-v0",
            "componentVersion": 0,
            "componentKind": "terminal-codec",
            "role": "encoder",
            "status": "experimental",
            "codec": "stored",
            "deterministic": True,
            "networkRequired": False,
            "implementation": "starlight_codec.cdf_oracle:pack_cdf_oracle",
        }
    ),
    _component(
        {
            "schema": PUBLIC_COMPONENT_SCHEMA_ID,
            "componentId": "stored-bytes-decoder-v0",
            "componentVersion": 0,
            "componentKind": "terminal-codec",
            "role": "decoder",
            "status": "experimental",
            "codec": "stored",
            "deterministic": True,
            "networkRequired": False,
            "implementation": "starlight_codec.cdf_oracle:open_cdf_oracle_pack",
        }
    ),
    _component(
        {
            "schema": PUBLIC_COMPONENT_SCHEMA_ID,
            "componentId": "zlib-level9-encoder-v0",
            "componentVersion": 0,
            "componentKind": "terminal-codec",
            "role": "encoder",
            "status": "experimental",
            "codec": "zlib",
            "zlibLevel": 9,
            "deterministic": True,
            "networkRequired": False,
            "implementation": "python-stdlib:zlib.compress",
        }
    ),
    _component(
        {
            "schema": PUBLIC_COMPONENT_SCHEMA_ID,
            "componentId": "zlib-level9-decoder-v0",
            "componentVersion": 0,
            "componentKind": "terminal-codec",
            "role": "decoder",
            "status": "experimental",
            "codec": "zlib",
            "zlibLevel": 9,
            "deterministic": True,
            "networkRequired": False,
            "implementation": "python-stdlib:zlib.decompressobj",
        }
    ),
    _component(
        {
            "schema": PUBLIC_COMPONENT_SCHEMA_ID,
            "componentId": "cdf-oracle-encoder-v0",
            "componentVersion": 0,
            "componentKind": "profile-codec",
            "role": "encoder",
            "status": "experimental",
            "codec": "cdf-oracle",
            "requiresCoderId": CODER_ID,
            "deterministic": True,
            "networkRequired": False,
            "implementation": "starlight_codec.cdf_oracle:encode_cdf_oracle",
        }
    ),
    _component(
        {
            "schema": PUBLIC_COMPONENT_SCHEMA_ID,
            "componentId": "cdf-oracle-decoder-v0",
            "componentVersion": 0,
            "componentKind": "profile-codec",
            "role": "decoder",
            "status": "experimental",
            "codec": "cdf-oracle",
            "requiresCoderId": CODER_ID,
            "deterministic": True,
            "networkRequired": False,
            "implementation": "starlight_codec.cdf_oracle:decode_cdf_oracle",
        }
    ),
    _component(
        {
            "schema": PUBLIC_COMPONENT_SCHEMA_ID,
            "componentId": "integer-arithmetic-range-encoder-v0",
            "componentVersion": 0,
            "componentKind": "entropy-coder",
            "role": "encoder",
            "status": "experimental",
            "coderId": CODER_ID,
            "stateBits": 32,
            "deterministic": True,
            "networkRequired": False,
            "implementation": "starlight_codec.cdf_oracle:_BitWriter",
        }
    ),
    _component(
        {
            "schema": PUBLIC_COMPONENT_SCHEMA_ID,
            "componentId": "integer-arithmetic-range-decoder-v0",
            "componentVersion": 0,
            "componentKind": "entropy-coder",
            "role": "decoder",
            "status": "experimental",
            "coderId": CODER_ID,
            "stateBits": 32,
            "deterministic": True,
            "networkRequired": False,
            "implementation": "starlight_codec.cdf_oracle:_BitReader",
        }
    ),
)
_PUBLIC_COMPONENTS_BY_ID = {
    component["componentId"]: component for component in _PUBLIC_COMPONENTS
}


def list_public_profiles() -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for profile_id in sorted(_PUBLIC_PROFILE_FILENAMES):
        descriptor, source = _load_public_profile_descriptor(profile_id)
        validation = validate_profile_descriptor(descriptor)
        profiles.append(
            {
                "profileId": profile_id,
                "profileHash": validation.profile_hash,
                "descriptorDigest": validation.descriptor_digest,
                "status": validation.status,
                "availability": validation.availability,
                "oracleKind": validation.oracle_kind,
                "coderId": validation.coder_id,
                "path": source.location,
            }
        )
    return profiles


def list_public_components(
    *, role: str | None = None, component_kind: str | None = None
) -> list[dict[str, Any]]:
    components = []
    for component in _PUBLIC_COMPONENTS:
        if role is not None and component.get("role") != role:
            continue
        if (
            component_kind is not None
            and component.get("componentKind") != component_kind
        ):
            continue
        components.append(dict(component))
    return sorted(components, key=lambda item: item["componentId"])


def resolve_public_profile_descriptor(profile_id: str) -> dict[str, Any]:
    descriptor, _source = _load_public_profile_descriptor(profile_id)
    return descriptor


def fetch_public_profile_descriptor(profile_id: str, output_dir: str | Path) -> dict[str, Any]:
    descriptor, source = _load_public_profile_descriptor(profile_id)
    validation = validate_profile_descriptor(descriptor)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{profile_id}.json"
    target.write_bytes(source.read_bytes())
    fetched = load_profile_descriptor(target)
    fetched_validation = validate_profile_descriptor(fetched)
    if fetched_validation.descriptor_digest != validation.descriptor_digest:
        try:
            target.unlink()
        except OSError:
            pass
        raise CdfPublicRegistryError("fetched profile descriptor digest mismatch")
    return {
        "profileId": profile_id,
        "profileHash": validation.profile_hash,
        "descriptorDigest": validation.descriptor_digest,
        "path": str(target),
        "bytes": target.stat().st_size,
    }


def validate_public_component(component: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(component, dict):
        raise CdfPublicRegistryError("component must be a dict")
    required = set(SUPPORTED_COMPONENT_BASE_FIELDS)
    missing = sorted(required - component.keys())
    if missing:
        raise CdfPublicRegistryError(f"missing component fields: {missing}")
    if component.get("schema") != PUBLIC_COMPONENT_SCHEMA_ID:
        raise CdfPublicRegistryError("component schema mismatch")

    component_id = _require_component_str(component, "componentId")
    if not COMPONENT_ID_RE.match(component_id):
        raise CdfPublicRegistryError(
            "componentId is not a supported stable identifier"
        )
    _require_component_non_negative_int(component, "componentVersion")

    component_kind = _require_component_str(component, "componentKind")
    if component_kind not in SUPPORTED_COMPONENT_KINDS:
        raise CdfPublicRegistryError("componentKind is not supported")
    role = _require_component_str(component, "role")
    if role not in SUPPORTED_COMPONENT_ROLES:
        raise CdfPublicRegistryError("component role is not supported")
    status = _require_component_str(component, "status")
    if status not in SUPPORTED_COMPONENT_STATUSES:
        raise CdfPublicRegistryError("component status is not allowed")
    if component.get("deterministic") is not True:
        raise CdfPublicRegistryError("component must be deterministic")
    if component.get("networkRequired") is not False:
        raise CdfPublicRegistryError("public component must not require network")
    _require_component_str(component, "implementation")
    _require_component_str(component, "componentDigest")

    if component_kind == "terminal-codec":
        codec = _require_component_str(component, "codec")
        if codec not in SUPPORTED_TERMINAL_CODECS:
            raise CdfPublicRegistryError("terminal component codec is not supported")
        allowed = set(SUPPORTED_COMPONENT_BASE_FIELDS) | {"codec"}
        if codec == "zlib":
            allowed.add("zlibLevel")
            if component.get("zlibLevel") != 9 or isinstance(
                component.get("zlibLevel"), bool
            ):
                raise CdfPublicRegistryError("zlib public component must use level 9")
        _reject_unknown_component_fields(component, allowed)
    elif component_kind == "profile-codec":
        codec = _require_component_str(component, "codec")
        if codec not in SUPPORTED_PROFILE_CODECS:
            raise CdfPublicRegistryError("profile component codec is not supported")
        if component.get("requiresCoderId") != CODER_ID:
            raise CdfPublicRegistryError(
                "profile component requires an unsupported coder"
            )
        _reject_unknown_component_fields(
            component,
            set(SUPPORTED_COMPONENT_BASE_FIELDS) | {"codec", "requiresCoderId"},
        )
    else:
        if component.get("coderId") != CODER_ID:
            raise CdfPublicRegistryError("entropy component coder is not supported")
        if component.get("stateBits") != 32 or isinstance(
            component.get("stateBits"), bool
        ):
            raise CdfPublicRegistryError("entropy component stateBits must be 32")
        _reject_unknown_component_fields(
            component,
            set(SUPPORTED_COMPONENT_BASE_FIELDS) | {"coderId", "stateBits"},
        )

    expected = _component_digest(component)
    if component.get("componentDigest") != expected:
        raise CdfPublicRegistryError("componentDigest does not match component")
    return dict(component)


def resolve_public_component(component_id: str) -> dict[str, Any]:
    component = _PUBLIC_COMPONENTS_BY_ID.get(component_id)
    if component is None:
        raise CdfPublicRegistryError(f"missing public component: {component_id}")
    return validate_public_component(component)


def fetch_public_component(component_id: str, output_dir: str | Path) -> dict[str, Any]:
    component = resolve_public_component(component_id)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{component_id}.json"
    target.write_text(
        json.dumps(component, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fetched = json.loads(target.read_text(encoding="utf-8"))
    fetched = validate_public_component(fetched)
    return {
        "componentId": component_id,
        "componentDigest": fetched["componentDigest"],
        "path": str(target),
        "bytes": target.stat().st_size,
    }


def _resolve_component_for(
    *,
    role: str,
    codec: str | None = None,
    coder_id: str | None = None,
) -> dict[str, Any]:
    matches = []
    for component in _PUBLIC_COMPONENTS_BY_ID.values():
        if component.get("role") != role:
            continue
        if codec is not None and component.get("codec") != codec:
            continue
        if coder_id is not None and component.get("coderId") != coder_id:
            continue
        matches.append(validate_public_component(component))
    if len(matches) != 1:
        requirement = codec if codec is not None else coder_id
        raise CdfPublicRegistryError(f"missing public {role} component: {requirement}")
    return matches[0]


def _resolve_profile_ids(profile_ids: Iterable[str]) -> tuple[str, ...]:
    resolved = []
    for profile_id in profile_ids:
        descriptor = resolve_public_profile_descriptor(profile_id)
        validation = validate_profile_descriptor(descriptor)
        if validation.coder_id != CODER_ID:
            raise CdfPublicRegistryError(
                f"unsupported coder for public profile: {profile_id}"
            )
        resolved.append(profile_id)
    if not resolved:
        raise CdfPublicRegistryError("at least one public profile is required")
    return tuple(resolved)


def plan_cdf_compression(
    data: bytes,
    *,
    profiles: Iterable[str] | None = None,
    min_saving_bytes: int = 1,
) -> dict[str, Any]:
    profile_ids = _resolve_profile_ids(profiles or DEFAULT_AUTO_PROFILE_IDS)
    encoder_components = [
        _resolve_component_for(role="encoder", codec="stored"),
        _resolve_component_for(role="encoder", codec="zlib"),
        _resolve_component_for(role="encoder", codec="cdf-oracle"),
        _resolve_component_for(role="encoder", coder_id=CODER_ID),
    ]
    packed = pack_cdf_oracle(
        bytes(data),
        profiles=profile_ids,
        min_saving_bytes=min_saving_bytes,
    )
    requirements = plan_cdf_open_requirements(packed.metadata)
    return {
        "inputBytes": len(data),
        "inputDigest": sha256_digest(bytes(data)),
        "profileCandidates": list(profile_ids),
        "encoderComponents": [
            {
                "componentId": component["componentId"],
                "componentDigest": component["componentDigest"],
            }
            for component in encoder_components
        ],
        "selectedCodec": packed.metadata["selectedCodec"],
        "selectedProfileId": packed.metadata.get("selectedProfileId"),
        "recommendedForStorage": packed.metadata["recommendedForStorage"],
        "adoptionDecision": packed.metadata["adoptionDecision"],
        "fallbackReason": packed.metadata["fallbackReason"],
        "candidateSummaries": packed.metadata["candidateSummaries"],
        "decodeRequirements": requirements,
    }


def auto_pack_cdf_oracle(
    data: bytes,
    *,
    profiles: Iterable[str] | None = None,
    min_saving_bytes: int = 1,
    cache_dir: str | Path | None = None,
) -> CdfOraclePackResult:
    profile_ids = _resolve_profile_ids(profiles or DEFAULT_AUTO_PROFILE_IDS)
    _resolve_component_for(role="encoder", codec="stored")
    _resolve_component_for(role="encoder", codec="zlib")
    _resolve_component_for(role="encoder", codec="cdf-oracle")
    _resolve_component_for(role="encoder", coder_id=CODER_ID)
    packed = pack_cdf_oracle(
        bytes(data),
        profiles=profile_ids,
        min_saving_bytes=min_saving_bytes,
    )
    if cache_dir is not None:
        _fetch_requirements(plan_cdf_open_requirements(packed.metadata), cache_dir)
    return packed


def plan_cdf_open_requirements(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise CdfPublicRegistryError("package metadata must be a dict")
    selected_codec = metadata.get("selectedCodec")
    if selected_codec not in {"stored", "zlib", "cdf-oracle"}:
        raise CdfPublicRegistryError("unsupported package selected codec")

    components = [_resolve_component_for(role="decoder", codec=selected_codec)]
    profiles: list[dict[str, Any]] = []
    if selected_codec == "cdf-oracle":
        profile_id = metadata.get("selectedProfileId")
        if not isinstance(profile_id, str):
            raise CdfPublicRegistryError("CDF oracle package missing selected profile")
        descriptor = resolve_public_profile_descriptor(profile_id)
        validation = validate_profile_descriptor(descriptor)
        oracle_metadata = metadata.get("oracle")
        if not isinstance(oracle_metadata, dict):
            raise CdfPublicRegistryError("CDF oracle package missing oracle metadata")
        coder_id = oracle_metadata.get("coderId")
        if coder_id != validation.coder_id:
            raise CdfPublicRegistryError("CDF oracle package coder/profile mismatch")
        components.append(_resolve_component_for(role="decoder", coder_id=coder_id))
        profiles.append(
            {
                "profileId": profile_id,
                "profileHash": validation.profile_hash,
                "descriptorDigest": validation.descriptor_digest,
            }
        )

    return {
        "selectedCodec": selected_codec,
        "selectedProfileId": metadata.get("selectedProfileId"),
        "profiles": profiles,
        "components": [
            {
                "componentId": component["componentId"],
                "componentDigest": component["componentDigest"],
            }
            for component in components
        ],
    }


def auto_open_cdf_oracle_pack(
    payload: bytes,
    metadata: dict[str, Any],
    *,
    cache_dir: str | Path | None = None,
) -> bytes:
    try:
        requirements = plan_cdf_open_requirements(metadata)
        if cache_dir is not None:
            _fetch_requirements(requirements, cache_dir)
        return open_cdf_oracle_pack(payload, metadata)
    except CdfOracleError:
        raise


def _fetch_requirements(requirements: dict[str, Any], output_dir: str | Path) -> None:
    for profile in requirements.get("profiles", []):
        fetch_public_profile_descriptor(profile["profileId"], output_dir)
    for component in requirements.get("components", []):
        fetch_public_component(component["componentId"], output_dir)
