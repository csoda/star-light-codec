from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CANONICAL_JSON_ID = "slc-canonical-json-v0"
DESCRIPTOR_SCHEMA_ID = "slc-cdf-profile-registry-v0"

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

SUPPORTED_PROFILE_CLASSES = frozenset(
    {
        "stream-small",
        "code-md-public",
        "archive-large",
        "private-domain",
        "bundled",
    }
)
SUPPORTED_STATUSES = frozenset(
    {
        "experimental",
        "stable",
        "deprecated",
        "discouraged",
        "revoked",
    }
)
DEFAULT_ALLOWED_STATUSES = frozenset({"experimental", "stable", "deprecated"})
SUPPORTED_AVAILABILITY = frozenset(
    {
        "public-registry",
        "private-arrangement",
        "bundled",
        "inline-descriptor",
    }
)
SUPPORTED_FALLBACK_MODES = frozenset(
    {
        "none",
        "stored-alternative",
        "stdlib-alternative",
        "residual-contained",
    }
)

SUPPORTED_ORACLE_KINDS = frozenset(
    {"byte-context-counts-v0", "byte-ppm-context-v0"}
)
SUPPORTED_SYMBOL_ALPHABETS = frozenset({"byte256"})
SUPPORTED_CONTEXT_UNITS = frozenset({"byte"})
SUPPORTED_CONTEXT_SLICING = frozenset({"trailing-decoded-bytes"})
SUPPORTED_FREQUENCY_MODELS = frozenset(
    {"byte-counts-plus-one-v0", "byte-ppm-suffix-recency-v0"}
)
SUPPORTED_COUNT_SOURCES = frozenset({"decodedTrailingContext"})
SUPPORTED_MATCH_SELECTIONS = frozenset({"longest-suffix-prior-follow-byte-v0"})
SUPPORTED_CDF_QUANTIZATION = frozenset(
    {"floor-proportional-min1-remainder-desc-byte-asc"}
)
SUPPORTED_CDF_TIE_BREAKS = frozenset({"byte-ascending"})
SUPPORTED_CODER_IDS = frozenset({"integer-arithmetic-range-v0"})
SUPPORTED_FLUSH_RULES = frozenset({"pending-bits-final-quarter-v0"})

REQUIRED_DESCRIPTOR_FIELDS = frozenset(
    {
        "schema",
        "profileId",
        "profileVersion",
        "profileClass",
        "status",
        "profileHash",
        "descriptorDigest",
        "availability",
        "decodeContract",
    }
)
OPTIONAL_DESCRIPTOR_FIELDS = frozenset(
    {
        "canonicalJson",
        "distribution",
        "compatibility",
        "security",
        "fallback",
    }
)
ALLOWED_DESCRIPTOR_FIELDS = REQUIRED_DESCRIPTOR_FIELDS | OPTIONAL_DESCRIPTOR_FIELDS
DECODE_CONTRACT_FIELDS = frozenset(
    {
        "oracleKind",
        "symbolAlphabet",
        "context",
        "frequencyModel",
        "frequencyToCdf",
        "entropyCoder",
        "resourceLimits",
        "goldenVectors",
    }
)
CONTEXT_FIELDS = frozenset({"unit", "maxContextBytes", "slicing"})
PPM_CONTEXT_FIELDS = CONTEXT_FIELDS | frozenset({"maxOrder"})
FREQUENCY_MODEL_FIELDS = frozenset(
    {"modelId", "baseFrequency", "countSource", "countScale"}
)
PPM_FREQUENCY_MODEL_FIELDS = frozenset(
    {
        "modelId",
        "baseFrequency",
        "countSource",
        "recencyWindow",
        "recencyScale",
        "matchScale",
        "matchScaleByOrder",
        "matchSelection",
    }
)
FREQUENCY_TO_CDF_FIELDS = frozenset(
    {"cdfTotal", "minimumSymbolFrequency", "quantization", "tieBreak"}
)
ENTROPY_CODER_FIELDS = frozenset({"coderId", "stateBits", "flushRule"})
RESOURCE_LIMITS_FIELDS = frozenset(
    {
        "maxContextBytes",
        "maxSymbolAlphabetSize",
        "maxDecoderStateBits",
        "maxDecodeMemoryBytes",
        "maxProfileBytes",
        "maxPayloadExpansionRatio",
        "streaming",
    }
)
GOLDEN_VECTOR_FIELDS = frozenset(
    {
        "name",
        "inputDigest",
        "payloadDigest",
        "decodedDigest",
        "cdfTraceDigest",
        "payloadBytes",
        "encodedBitLength",
    }
)


class CdfProfileRegistryError(ValueError):
    """Raised when a CDF profile descriptor fails closed."""


@dataclass(frozen=True)
class ProfileDescriptorValidation:
    schema: str
    profile_id: str
    profile_version: int
    profile_class: str
    status: str
    availability: str
    profile_hash: str
    descriptor_digest: str
    oracle_kind: str
    coder_id: str
    cdf_total: int
    context_window: int
    fallback_mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": True,
            "schema": self.schema,
            "profileId": self.profile_id,
            "profileVersion": self.profile_version,
            "profileClass": self.profile_class,
            "status": self.status,
            "availability": self.availability,
            "profileHash": self.profile_hash,
            "descriptorDigest": self.descriptor_digest,
            "decodeContract": {
                "oracleKind": self.oracle_kind,
                "coderId": self.coder_id,
                "cdfTotal": self.cdf_total,
                "contextWindow": self.context_window,
            },
            "fallbackMode": self.fallback_mode,
        }


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a JSON-compatible value as slc-canonical-json-v0 bytes."""

    _validate_canonical_json_value(value, "$")
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return encoded.encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def profile_hash(decode_contract: dict[str, Any]) -> str:
    return canonical_json_sha256(decode_contract)


def descriptor_digest(descriptor: dict[str, Any]) -> str:
    digest_body = dict(descriptor)
    digest_body.pop("descriptorDigest", None)
    return canonical_json_sha256(digest_body)


def parse_profile_descriptor(text: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=_reject_float_token,
            parse_constant=_reject_json_constant,
        )
    except CdfProfileRegistryError:
        raise
    except json.JSONDecodeError as exc:
        raise CdfProfileRegistryError(f"invalid JSON: {exc.msg}") from exc

    if not isinstance(value, dict):
        raise CdfProfileRegistryError("descriptor must be a JSON object")
    return value


def load_profile_descriptor(path: str | Path) -> dict[str, Any]:
    descriptor_path = Path(path)
    try:
        return parse_profile_descriptor(descriptor_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CdfProfileRegistryError(f"could not read descriptor: {exc}") from exc


def validate_profile_descriptor(
    descriptor: dict[str, Any],
    *,
    allowed_statuses: Iterable[str] | None = None,
) -> ProfileDescriptorValidation:
    if not isinstance(descriptor, dict):
        raise CdfProfileRegistryError("descriptor must be a dict")

    missing = sorted(REQUIRED_DESCRIPTOR_FIELDS - descriptor.keys())
    if missing:
        raise CdfProfileRegistryError(f"missing required descriptor fields: {missing}")
    unknown = sorted(set(descriptor) - ALLOWED_DESCRIPTOR_FIELDS)
    if unknown:
        raise CdfProfileRegistryError(f"unknown descriptor fields: {unknown}")

    canonical_json = descriptor.get("canonicalJson", CANONICAL_JSON_ID)
    _require_literal(canonical_json, CANONICAL_JSON_ID, "canonicalJson")

    schema = _require_str(descriptor["schema"], "schema")
    _require_literal(schema, DESCRIPTOR_SCHEMA_ID, "schema")
    profile_id = _require_str(descriptor["profileId"], "profileId")
    if not PROFILE_ID_RE.match(profile_id):
        raise CdfProfileRegistryError("profileId is not a supported stable identifier")
    profile_version = _require_non_negative_int(
        descriptor["profileVersion"], "profileVersion"
    )
    profile_class = _require_one_of(
        descriptor["profileClass"], SUPPORTED_PROFILE_CLASSES, "profileClass"
    )
    status = _require_one_of(descriptor["status"], SUPPORTED_STATUSES, "status")
    allowed = set(allowed_statuses or DEFAULT_ALLOWED_STATUSES)
    if status not in allowed:
        raise CdfProfileRegistryError(f"profile status is not allowed: {status}")
    availability = _require_one_of(
        descriptor["availability"], SUPPORTED_AVAILABILITY, "availability"
    )

    decode_contract = _require_dict(descriptor["decodeContract"], "decodeContract")
    contract_info = _validate_decode_contract(decode_contract)

    expected_profile_hash = _require_hash(descriptor["profileHash"], "profileHash")
    actual_profile_hash = profile_hash(decode_contract)
    if expected_profile_hash != actual_profile_hash:
        raise CdfProfileRegistryError("profileHash does not match decodeContract")

    fallback_mode = _validate_fallback(descriptor.get("fallback", {"mode": "none"}))
    _validate_optional_metadata_sections(descriptor)

    expected_descriptor_digest = _require_hash(
        descriptor["descriptorDigest"], "descriptorDigest"
    )
    actual_descriptor_digest = descriptor_digest(descriptor)
    if expected_descriptor_digest != actual_descriptor_digest:
        raise CdfProfileRegistryError("descriptorDigest does not match descriptor")

    return ProfileDescriptorValidation(
        schema=schema,
        profile_id=profile_id,
        profile_version=profile_version,
        profile_class=profile_class,
        status=status,
        availability=availability,
        profile_hash=expected_profile_hash,
        descriptor_digest=expected_descriptor_digest,
        oracle_kind=contract_info["oracleKind"],
        coder_id=contract_info["coderId"],
        cdf_total=contract_info["cdfTotal"],
        context_window=contract_info["contextWindow"],
        fallback_mode=fallback_mode,
    )


def profile_descriptor_summary(descriptor: dict[str, Any]) -> dict[str, Any]:
    return validate_profile_descriptor(descriptor).as_dict()


def _validate_decode_contract(contract: dict[str, Any]) -> dict[str, Any]:
    required = DECODE_CONTRACT_FIELDS
    missing = sorted(required - contract.keys())
    if missing:
        raise CdfProfileRegistryError(f"missing decodeContract fields: {missing}")
    _reject_unknown_fields(contract, DECODE_CONTRACT_FIELDS, "decodeContract")

    oracle_kind = _require_one_of(
        contract["oracleKind"], SUPPORTED_ORACLE_KINDS, "decodeContract.oracleKind"
    )
    _require_one_of(
        contract["symbolAlphabet"],
        SUPPORTED_SYMBOL_ALPHABETS,
        "decodeContract.symbolAlphabet",
    )

    frequency_model = _require_dict(
        contract["frequencyModel"], "decodeContract.frequencyModel"
    )
    model_id = _require_one_of(
        frequency_model.get("modelId"),
        SUPPORTED_FREQUENCY_MODELS,
        "decodeContract.frequencyModel.modelId",
    )
    expected_model_id = {
        "byte-context-counts-v0": "byte-counts-plus-one-v0",
        "byte-ppm-context-v0": "byte-ppm-suffix-recency-v0",
    }[oracle_kind]
    if model_id != expected_model_id:
        raise CdfProfileRegistryError(
            "decodeContract oracleKind and frequencyModel.modelId mismatch"
        )

    context = _require_dict(contract["context"], "decodeContract.context")
    context_fields = (
        PPM_CONTEXT_FIELDS if model_id == "byte-ppm-suffix-recency-v0" else CONTEXT_FIELDS
    )
    _reject_unknown_fields(context, context_fields, "decodeContract.context")
    _require_one_of(
        context.get("unit"), SUPPORTED_CONTEXT_UNITS, "decodeContract.context.unit"
    )
    context_window = _require_positive_int(
        context.get("maxContextBytes"), "decodeContract.context.maxContextBytes"
    )
    _require_one_of(
        context.get("slicing"),
        SUPPORTED_CONTEXT_SLICING,
        "decodeContract.context.slicing",
    )

    _validate_frequency_model(frequency_model, model_id, context, context_window)

    frequency_to_cdf = _require_dict(
        contract["frequencyToCdf"], "decodeContract.frequencyToCdf"
    )
    _reject_unknown_fields(
        frequency_to_cdf, FREQUENCY_TO_CDF_FIELDS, "decodeContract.frequencyToCdf"
    )
    cdf_total = _require_positive_int(
        frequency_to_cdf.get("cdfTotal"), "decodeContract.frequencyToCdf.cdfTotal"
    )
    _require_positive_int(
        frequency_to_cdf.get("minimumSymbolFrequency"),
        "decodeContract.frequencyToCdf.minimumSymbolFrequency",
    )
    _require_one_of(
        frequency_to_cdf.get("quantization"),
        SUPPORTED_CDF_QUANTIZATION,
        "decodeContract.frequencyToCdf.quantization",
    )
    _require_one_of(
        frequency_to_cdf.get("tieBreak"),
        SUPPORTED_CDF_TIE_BREAKS,
        "decodeContract.frequencyToCdf.tieBreak",
    )

    entropy_coder = _require_dict(
        contract["entropyCoder"], "decodeContract.entropyCoder"
    )
    _reject_unknown_fields(
        entropy_coder, ENTROPY_CODER_FIELDS, "decodeContract.entropyCoder"
    )
    coder_id = _require_one_of(
        entropy_coder.get("coderId"),
        SUPPORTED_CODER_IDS,
        "decodeContract.entropyCoder.coderId",
    )
    state_bits = _require_positive_int(
        entropy_coder.get("stateBits"), "decodeContract.entropyCoder.stateBits"
    )
    _require_one_of(
        entropy_coder.get("flushRule"),
        SUPPORTED_FLUSH_RULES,
        "decodeContract.entropyCoder.flushRule",
    )

    resource_limits = _require_dict(
        contract["resourceLimits"], "decodeContract.resourceLimits"
    )
    _reject_unknown_fields(
        resource_limits, RESOURCE_LIMITS_FIELDS, "decodeContract.resourceLimits"
    )
    max_context = _require_positive_int(
        resource_limits.get("maxContextBytes"),
        "decodeContract.resourceLimits.maxContextBytes",
    )
    max_alphabet = _require_positive_int(
        resource_limits.get("maxSymbolAlphabetSize"),
        "decodeContract.resourceLimits.maxSymbolAlphabetSize",
    )
    max_state_bits = _require_positive_int(
        resource_limits.get("maxDecoderStateBits"),
        "decodeContract.resourceLimits.maxDecoderStateBits",
    )
    _require_positive_int(
        resource_limits.get("maxDecodeMemoryBytes"),
        "decodeContract.resourceLimits.maxDecodeMemoryBytes",
    )
    _require_positive_int(
        resource_limits.get("maxProfileBytes"),
        "decodeContract.resourceLimits.maxProfileBytes",
    )
    _require_positive_int(
        resource_limits.get("maxPayloadExpansionRatio"),
        "decodeContract.resourceLimits.maxPayloadExpansionRatio",
    )
    _require_bool(
        resource_limits.get("streaming"), "decodeContract.resourceLimits.streaming"
    )
    if max_context < context_window:
        raise CdfProfileRegistryError("resource limit maxContextBytes is too small")
    if max_alphabet < 256:
        raise CdfProfileRegistryError("resource limit maxSymbolAlphabetSize is too small")
    if max_state_bits < state_bits:
        raise CdfProfileRegistryError("resource limit maxDecoderStateBits is too small")

    _validate_golden_vectors(contract["goldenVectors"])

    return {
        "oracleKind": oracle_kind,
        "coderId": coder_id,
        "cdfTotal": cdf_total,
        "contextWindow": context_window,
    }


def _validate_frequency_model(
    frequency_model: dict[str, Any],
    model_id: str,
    context: dict[str, Any],
    context_window: int,
) -> None:
    if model_id == "byte-counts-plus-one-v0":
        _reject_unknown_fields(
            frequency_model, FREQUENCY_MODEL_FIELDS, "decodeContract.frequencyModel"
        )
        _require_positive_int(
            frequency_model.get("baseFrequency"),
            "decodeContract.frequencyModel.baseFrequency",
        )
        _require_one_of(
            frequency_model.get("countSource"),
            SUPPORTED_COUNT_SOURCES,
            "decodeContract.frequencyModel.countSource",
        )
        _require_positive_int(
            frequency_model.get("countScale"),
            "decodeContract.frequencyModel.countScale",
        )
        return

    if model_id != "byte-ppm-suffix-recency-v0":
        raise CdfProfileRegistryError(
            f"decodeContract.frequencyModel.modelId is not supported: {model_id}"
        )

    _reject_unknown_fields(
        frequency_model, PPM_FREQUENCY_MODEL_FIELDS, "decodeContract.frequencyModel"
    )
    max_order = _require_positive_int(
        context.get("maxOrder"), "decodeContract.context.maxOrder"
    )
    if max_order > context_window:
        raise CdfProfileRegistryError("decodeContract.context.maxOrder is too large")
    _require_positive_int(
        frequency_model.get("baseFrequency"),
        "decodeContract.frequencyModel.baseFrequency",
    )
    _require_one_of(
        frequency_model.get("countSource"),
        SUPPORTED_COUNT_SOURCES,
        "decodeContract.frequencyModel.countSource",
    )
    recency_window = _require_positive_int(
        frequency_model.get("recencyWindow"),
        "decodeContract.frequencyModel.recencyWindow",
    )
    if recency_window > context_window:
        raise CdfProfileRegistryError(
            "decodeContract.frequencyModel.recencyWindow is too large"
        )
    _require_positive_int(
        frequency_model.get("recencyScale"),
        "decodeContract.frequencyModel.recencyScale",
    )
    _require_positive_int(
        frequency_model.get("matchScale"),
        "decodeContract.frequencyModel.matchScale",
    )
    _require_bool(
        frequency_model.get("matchScaleByOrder"),
        "decodeContract.frequencyModel.matchScaleByOrder",
    )
    _require_one_of(
        frequency_model.get("matchSelection"),
        SUPPORTED_MATCH_SELECTIONS,
        "decodeContract.frequencyModel.matchSelection",
    )


def _validate_golden_vectors(value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise CdfProfileRegistryError("decodeContract.goldenVectors must be a non-empty list")
    seen_names: set[str] = set()
    for index, vector_value in enumerate(value):
        path = f"decodeContract.goldenVectors[{index}]"
        vector = _require_dict(vector_value, path)
        _reject_unknown_fields(vector, GOLDEN_VECTOR_FIELDS, path)
        name = _require_str(vector.get("name"), f"{path}.name")
        if name in seen_names:
            raise CdfProfileRegistryError(f"duplicate golden vector name: {name}")
        seen_names.add(name)
        _require_hash(vector.get("inputDigest"), f"{path}.inputDigest")
        _require_hash(vector.get("payloadDigest"), f"{path}.payloadDigest")
        _require_hash(vector.get("decodedDigest"), f"{path}.decodedDigest")
        if "cdfTraceDigest" in vector:
            _require_hash(vector["cdfTraceDigest"], f"{path}.cdfTraceDigest")
        if "payloadBytes" in vector:
            _require_non_negative_int(vector["payloadBytes"], f"{path}.payloadBytes")
        if "encodedBitLength" in vector:
            _require_non_negative_int(
                vector["encodedBitLength"], f"{path}.encodedBitLength"
            )


def _validate_fallback(value: Any) -> str:
    fallback = _require_dict(value, "fallback")
    mode = _require_one_of(fallback.get("mode"), SUPPORTED_FALLBACK_MODES, "fallback.mode")
    if mode == "none" and set(fallback) != {"mode"}:
        raise CdfProfileRegistryError("fallback mode none must not include payload metadata")
    return mode


def _validate_optional_metadata_sections(descriptor: dict[str, Any]) -> None:
    for field in ("distribution", "compatibility", "security"):
        if field in descriptor:
            _require_dict(descriptor[field], field)


def _validate_canonical_json_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, str) or isinstance(value, bool):
        return
    if isinstance(value, int):
        if value < 0:
            raise CdfProfileRegistryError(f"{path} contains a negative integer")
        return
    if isinstance(value, float):
        raise CdfProfileRegistryError(f"{path} contains a float")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_canonical_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CdfProfileRegistryError(f"{path} contains a non-string object key")
            _validate_canonical_json_value(item, f"{path}.{key}")
        return
    raise CdfProfileRegistryError(f"{path} contains unsupported JSON value")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise CdfProfileRegistryError(f"duplicate object key: {key}")
        output[key] = value
    return output


def _reject_float_token(value: str) -> None:
    raise CdfProfileRegistryError(f"floats are not supported: {value}")


def _reject_json_constant(value: str) -> None:
    raise CdfProfileRegistryError(f"non-finite JSON number is not supported: {value}")


def _require_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CdfProfileRegistryError(f"{field} must be an object")
    return value


def _reject_unknown_fields(
    value: dict[str, Any], allowed_fields: Iterable[str], field: str
) -> None:
    unknown = sorted(set(value) - set(allowed_fields))
    if unknown:
        raise CdfProfileRegistryError(f"unknown {field} fields: {unknown}")


def _require_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CdfProfileRegistryError(f"{field} must be a non-empty string")
    return value


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise CdfProfileRegistryError(f"{field} must be a boolean")
    return value


def _require_non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CdfProfileRegistryError(f"{field} must be a non-negative integer")
    return value


def _require_positive_int(value: Any, field: str) -> int:
    integer = _require_non_negative_int(value, field)
    if integer <= 0:
        raise CdfProfileRegistryError(f"{field} must be a positive integer")
    return integer


def _require_hash(value: Any, field: str) -> str:
    digest = _require_str(value, field)
    if not HASH_RE.match(digest):
        raise CdfProfileRegistryError(f"{field} must be sha256:<64 lowercase hex>")
    return digest


def _require_literal(value: Any, expected: str, field: str) -> str:
    actual = _require_str(value, field)
    if actual != expected:
        raise CdfProfileRegistryError(f"{field} must be {expected}")
    return actual


def _require_one_of(value: Any, supported: Iterable[str], field: str) -> str:
    actual = _require_str(value, field)
    if actual not in supported:
        raise CdfProfileRegistryError(f"{field} is not supported: {actual}")
    return actual
