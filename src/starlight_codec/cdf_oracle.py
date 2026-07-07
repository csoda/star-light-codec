from __future__ import annotations

import bisect
import hashlib
import json
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .cdf_profile_registry import canonical_json_sha256


PROFILE_ID = "byte-context-cdf-v0"
CODER_ID = "integer-arithmetic-range-v0"
CDF_TOTAL = 1 << 16
CONTEXT_WINDOW = 64
PPM_PROFILE_ID = "byte-ppm-context-v0"
CDF_PACK_KIND = "slc-cdf-pack-v0"
CDF_PACK_METADATA_SCHEMA = "slc-cdf-pack-metadata-v0"
CDF_PACK_METADATA_ENCODING = "json-sort-keys-compact-v0"
CDF_PACK_MAX_RAW_BYTES = 1_048_576
PPM_CONTEXT_WINDOW = 1024
PPM_MAX_ORDER = 8
PPM_BASE_FREQUENCY = 1
PPM_RECENCY_WINDOW = 128
PPM_RECENCY_SCALE = 1
PPM_MATCH_SCALE = 96
PPM_MATCH_SCALE_BY_ORDER = True
_STATE_BITS = 32
CDF_PROFILE_DECODE_CONTRACT: dict[str, Any] = {
    "oracleKind": "byte-context-counts-v0",
    "symbolAlphabet": "byte256",
    "context": {
        "unit": "byte",
        "maxContextBytes": CONTEXT_WINDOW,
        "slicing": "trailing-decoded-bytes",
    },
    "frequencyModel": {
        "modelId": "byte-counts-plus-one-v0",
        "baseFrequency": 1,
        "countSource": "decodedTrailingContext",
        "countScale": 1,
    },
    "frequencyToCdf": {
        "cdfTotal": CDF_TOTAL,
        "minimumSymbolFrequency": 1,
        "quantization": "floor-proportional-min1-remainder-desc-byte-asc",
        "tieBreak": "byte-ascending",
    },
    "entropyCoder": {
        "coderId": CODER_ID,
        "stateBits": _STATE_BITS,
        "flushRule": "pending-bits-final-quarter-v0",
    },
    "resourceLimits": {
        "maxContextBytes": CONTEXT_WINDOW,
        "maxSymbolAlphabetSize": 256,
        "maxDecoderStateBits": _STATE_BITS,
        "maxDecodeMemoryBytes": 1048576,
        "maxProfileBytes": 65536,
        "maxPayloadExpansionRatio": 1024,
        "streaming": True,
    },
    "goldenVectors": [
        {
            "name": "empty",
            "inputDigest": "sha256:e3b0c44298fc1c149afbf4c8996fb924"
            "27ae41e4649b934ca495991b7852b855",
            "payloadDigest": "sha256:c3641f8544d7c02f3580b07c0f9887f0c6a27ff5ab1d4a3e29caf197cfc299ae",
            "decodedDigest": "sha256:e3b0c44298fc1c149afbf4c8996fb924"
            "27ae41e4649b934ca495991b7852b855",
            "payloadBytes": 1,
            "encodedBitLength": 2,
        }
    ],
}
PROFILE_SPEC = (
    "star-light-codec:cdf-oracle-profile:byte-context-cdf-v0;"
    "alphabet=byte;cdf_total=65536;context_window=64;"
    "freq(byte)=1+count(byte in trailing context);"
    "integer_quantization=floor-proportional-min1-remainder-desc-byte-asc"
)
PROFILE_HASH = canonical_json_sha256(CDF_PROFILE_DECODE_CONTRACT)
PPM_PROFILE_DECODE_CONTRACT: dict[str, Any] = {
    "oracleKind": "byte-ppm-context-v0",
    "symbolAlphabet": "byte256",
    "context": {
        "unit": "byte",
        "maxContextBytes": PPM_CONTEXT_WINDOW,
        "maxOrder": PPM_MAX_ORDER,
        "slicing": "trailing-decoded-bytes",
    },
    "frequencyModel": {
        "modelId": "byte-ppm-suffix-recency-v0",
        "baseFrequency": PPM_BASE_FREQUENCY,
        "countSource": "decodedTrailingContext",
        "recencyWindow": PPM_RECENCY_WINDOW,
        "recencyScale": PPM_RECENCY_SCALE,
        "matchScale": PPM_MATCH_SCALE,
        "matchScaleByOrder": PPM_MATCH_SCALE_BY_ORDER,
        "matchSelection": "longest-suffix-prior-follow-byte-v0",
    },
    "frequencyToCdf": {
        "cdfTotal": CDF_TOTAL,
        "minimumSymbolFrequency": 1,
        "quantization": "floor-proportional-min1-remainder-desc-byte-asc",
        "tieBreak": "byte-ascending",
    },
    "entropyCoder": {
        "coderId": CODER_ID,
        "stateBits": _STATE_BITS,
        "flushRule": "pending-bits-final-quarter-v0",
    },
    "resourceLimits": {
        "maxContextBytes": PPM_CONTEXT_WINDOW,
        "maxSymbolAlphabetSize": 256,
        "maxDecoderStateBits": _STATE_BITS,
        "maxDecodeMemoryBytes": 1048576,
        "maxProfileBytes": 65536,
        "maxPayloadExpansionRatio": 1024,
        "streaming": True,
    },
    "goldenVectors": [
        {
            "name": "empty",
            "inputDigest": "sha256:e3b0c44298fc1c149afbf4c8996fb924"
            "27ae41e4649b934ca495991b7852b855",
            "payloadDigest": "sha256:c3641f8544d7c02f3580b07c0f9887f0c6a27ff5ab1d4a3e29caf197cfc299ae",
            "decodedDigest": "sha256:e3b0c44298fc1c149afbf4c8996fb924"
            "27ae41e4649b934ca495991b7852b855",
            "payloadBytes": 1,
            "encodedBitLength": 2,
        }
    ],
}
PPM_PROFILE_SPEC = (
    "star-light-codec:cdf-oracle-profile:byte-ppm-context-v0;"
    "alphabet=byte;cdf_total=65536;context_window=1024;max_order=8;"
    "freq(byte)=base+recent-counts+longest-suffix-follow-byte-boost;"
    "base_frequency=1;recency_window=128;recency_scale=1;"
    "match_scale=96;match_scale_by_order=true;"
    "integer_quantization=floor-proportional-min1-remainder-desc-byte-asc"
)
PPM_PROFILE_HASH = canonical_json_sha256(PPM_PROFILE_DECODE_CONTRACT)

_MAX_CODE = (1 << _STATE_BITS) - 1
_HALF = 1 << (_STATE_BITS - 1)
_FIRST_QTR = _HALF >> 1
_THIRD_QTR = _FIRST_QTR * 3


class CdfOracleError(ValueError):
    """Raised when the standalone CDF oracle prototype fails closed."""


@dataclass(frozen=True)
class CdfOracleResult:
    metadata: dict[str, Any]
    payload: bytes


@dataclass(frozen=True)
class CdfOraclePackResult:
    metadata: dict[str, Any]
    payload: bytes


@dataclass(frozen=True)
class _CdfOracleProfile:
    profile_id: str
    profile_hash: str
    profile_spec: str
    decode_contract: dict[str, Any]
    context_window: int
    metadata_fields: dict[str, Any]


@dataclass(frozen=True)
class _CdfPackCandidate:
    codec: str
    payload: bytes
    order: int
    profile_id: str | None = None
    oracle_metadata: dict[str, Any] | None = None


_PROFILES: dict[str, _CdfOracleProfile] = {
    PROFILE_ID: _CdfOracleProfile(
        profile_id=PROFILE_ID,
        profile_hash=PROFILE_HASH,
        profile_spec=PROFILE_SPEC,
        decode_contract=CDF_PROFILE_DECODE_CONTRACT,
        context_window=CONTEXT_WINDOW,
        metadata_fields={},
    ),
    PPM_PROFILE_ID: _CdfOracleProfile(
        profile_id=PPM_PROFILE_ID,
        profile_hash=PPM_PROFILE_HASH,
        profile_spec=PPM_PROFILE_SPEC,
        decode_contract=PPM_PROFILE_DECODE_CONTRACT,
        context_window=PPM_CONTEXT_WINDOW,
        metadata_fields={
            "maxOrder": PPM_MAX_ORDER,
            "baseFrequency": PPM_BASE_FREQUENCY,
            "recencyWindow": PPM_RECENCY_WINDOW,
            "recencyScale": PPM_RECENCY_SCALE,
            "matchScale": PPM_MATCH_SCALE,
            "matchScaleByOrder": PPM_MATCH_SCALE_BY_ORDER,
        },
    ),
}
AVAILABLE_PROFILE_IDS = tuple(_PROFILES)


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _is_sha256_digest(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    prefix = "sha256:"
    if not value.startswith(prefix) or len(value) != len(prefix) + 64:
        return False
    return all(char in "0123456789abcdef" for char in value[len(prefix) :])


def _compact_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def profile_metadata(profile_id: str = PROFILE_ID) -> dict[str, Any]:
    profile = _get_profile(profile_id)
    metadata = {
        "profileId": profile.profile_id,
        "profileHash": profile.profile_hash,
        "profileSpec": profile.profile_spec,
        "coderId": CODER_ID,
        "cdfTotal": CDF_TOTAL,
        "contextWindow": profile.context_window,
        "standalonePrototype": True,
        "productionSlb1Compatible": False,
    }
    metadata.update(profile.metadata_fields)
    return metadata


def cdf_for_context(context: bytes, *, context_window: int = CONTEXT_WINDOW) -> tuple[int, ...]:
    if context_window <= 0:
        raise CdfOracleError("context_window must be positive")

    window = bytes(context[-context_window:])
    counts = Counter(window)
    weights = [1 + counts.get(byte, 0) for byte in range(256)]
    return _weights_to_cdf(weights)


def cdf_for_ppm_context(
    context: bytes,
    *,
    context_window: int = PPM_CONTEXT_WINDOW,
    max_order: int = PPM_MAX_ORDER,
    base_frequency: int = PPM_BASE_FREQUENCY,
    recency_window: int = PPM_RECENCY_WINDOW,
    recency_scale: int = PPM_RECENCY_SCALE,
    match_scale: int = PPM_MATCH_SCALE,
    match_scale_by_order: bool = PPM_MATCH_SCALE_BY_ORDER,
) -> tuple[int, ...]:
    if context_window <= 0:
        raise CdfOracleError("context_window must be positive")
    if max_order <= 0:
        raise CdfOracleError("max_order must be positive")
    if base_frequency <= 0:
        raise CdfOracleError("base_frequency must be positive")
    if recency_window <= 0:
        raise CdfOracleError("recency_window must be positive")
    if recency_scale <= 0:
        raise CdfOracleError("recency_scale must be positive")
    if match_scale <= 0:
        raise CdfOracleError("match_scale must be positive")
    if max_order > context_window:
        raise CdfOracleError("max_order must not exceed context_window")

    window = bytes(context[-context_window:])
    weights = [base_frequency for _ in range(256)]
    for byte in window[-recency_window:]:
        weights[byte] += recency_scale

    max_order_for_context = min(max_order, len(window))
    for order in range(max_order_for_context, 0, -1):
        suffix = window[-order:]
        last_start = len(window) - order
        boost = match_scale * order if match_scale_by_order else match_scale
        matches = 0
        for start in range(last_start):
            if window[start : start + order] == suffix:
                weights[window[start + order]] += boost
                matches += 1
        if matches:
            break

    return _weights_to_cdf(weights)


def _weights_to_cdf(weights: list[int]) -> tuple[int, ...]:
    weight_total = sum(weights)
    scaled = [(weight * CDF_TOTAL) // weight_total for weight in weights]
    freqs = [max(1, value) for value in scaled]

    remainder_order = sorted(
        range(256),
        key=lambda byte: (
            -((weights[byte] * CDF_TOTAL) % weight_total),
            byte,
        ),
    )
    diff = CDF_TOTAL - sum(freqs)
    if diff > 0:
        for index in range(diff):
            freqs[remainder_order[index % 256]] += 1
    elif diff < 0:
        shrink_order = list(reversed(remainder_order))
        remaining = -diff
        while remaining:
            changed = False
            for byte in shrink_order:
                if freqs[byte] > 1:
                    freqs[byte] -= 1
                    remaining -= 1
                    changed = True
                    if remaining == 0:
                        break
            if not changed:
                raise CdfOracleError("could not quantize positive byte frequencies")

    cdf = [0]
    running = 0
    for freq in freqs:
        running += freq
        cdf.append(running)
    if running != CDF_TOTAL:
        raise CdfOracleError("CDF quantization total mismatch")
    return tuple(cdf)


def _get_profile(profile_id: Any) -> _CdfOracleProfile:
    if not isinstance(profile_id, str):
        raise CdfOracleError("CDF oracle profile id must be a string")
    try:
        return _PROFILES[profile_id]
    except KeyError:
        raise CdfOracleError(f"unsupported CDF oracle profile: {profile_id}") from None


def _cdf_for_profile(context: bytes, profile: _CdfOracleProfile) -> tuple[int, ...]:
    if profile.profile_id == PROFILE_ID:
        return cdf_for_context(context, context_window=profile.context_window)
    if profile.profile_id == PPM_PROFILE_ID:
        return cdf_for_ppm_context(
            context,
            context_window=profile.context_window,
            max_order=profile.metadata_fields["maxOrder"],
            base_frequency=profile.metadata_fields["baseFrequency"],
            recency_window=profile.metadata_fields["recencyWindow"],
            recency_scale=profile.metadata_fields["recencyScale"],
            match_scale=profile.metadata_fields["matchScale"],
            match_scale_by_order=profile.metadata_fields["matchScaleByOrder"],
        )
    raise CdfOracleError(f"unsupported CDF oracle profile: {profile.profile_id}")


def _context_tail_for_profile(
    context: bytearray, profile: _CdfOracleProfile
) -> bytes:
    if profile.context_window <= 0:
        raise CdfOracleError("context_window must be positive")
    return bytes(context[-profile.context_window :])


class _BitWriter:
    def __init__(self) -> None:
        self._bytes = bytearray()
        self._current = 0
        self._count = 0
        self.bit_count = 0

    def write(self, bit: int) -> None:
        self._current = (self._current << 1) | (bit & 1)
        self._count += 1
        self.bit_count += 1
        if self._count == 8:
            self._bytes.append(self._current)
            self._current = 0
            self._count = 0

    def finish(self) -> bytes:
        if self._count:
            self._bytes.append(self._current << (8 - self._count))
            self._current = 0
            self._count = 0
        return bytes(self._bytes)


class _BitReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def read(self) -> int:
        byte_index = self._offset // 8
        if byte_index >= len(self._data):
            self._offset += 1
            return 0
        shift = 7 - (self._offset % 8)
        self._offset += 1
        return (self._data[byte_index] >> shift) & 1


def _write_with_pending(writer: _BitWriter, bit: int, pending_bits: int) -> None:
    writer.write(bit)
    inverse = 1 - bit
    for _ in range(pending_bits):
        writer.write(inverse)


def _metadata_value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(expected, int):
        return isinstance(actual, int) and not isinstance(actual, bool) and actual == expected
    return actual == expected


def _require_package_int(metadata: dict[str, Any], field: str) -> int:
    value = metadata.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CdfOracleError(f"invalid CDF pack {field}")
    return value


def _require_package_digest(metadata: dict[str, Any], field: str) -> str:
    value = metadata.get(field)
    if not _is_sha256_digest(value):
        raise CdfOracleError(f"invalid CDF pack {field}")
    return value


def _pack_candidate_label(candidate: _CdfPackCandidate) -> str:
    if candidate.profile_id is None:
        return candidate.codec
    return f"{candidate.codec}:{candidate.profile_id}"


def _build_candidate_summaries(
    candidates: list[_CdfPackCandidate],
    metrics_by_label: dict[str, tuple[int, int]],
    *,
    raw_bytes: int,
    min_saving_bytes: int,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for candidate in candidates:
        label = _pack_candidate_label(candidate)
        metadata_bytes, whole_package_bytes = metrics_by_label.get(
            label, (0, len(candidate.payload))
        )
        saving_bytes = raw_bytes - whole_package_bytes
        summary: dict[str, Any] = {
            "codec": candidate.codec,
            "payloadBytes": len(candidate.payload),
            "estimatedMetadataBytes": metadata_bytes,
            "estimatedWholeBytes": whole_package_bytes,
            "savingBytesVsRaw": saving_bytes,
            "passesBenefitGate": saving_bytes >= min_saving_bytes
            and candidate.codec != "stored",
        }
        if candidate.profile_id is not None:
            summary["profileId"] = candidate.profile_id
        summaries.append(summary)
    return summaries


def _build_pack_metadata(
    candidate: _CdfPackCandidate,
    *,
    raw_bytes: int,
    input_digest: str,
    min_saving_bytes: int,
    candidate_summaries: list[dict[str, Any]],
    recommended_for_storage: bool,
    adoption_decision: str,
    fallback_reason: str,
    metadata_bytes: int,
    whole_package_bytes: int,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "packageKind": CDF_PACK_KIND,
        "metadataSchema": CDF_PACK_METADATA_SCHEMA,
        "metadataEncoding": CDF_PACK_METADATA_ENCODING,
        "selectedCodec": candidate.codec,
        "rawBytes": raw_bytes,
        "payloadBytes": len(candidate.payload),
        "metadataBytes": metadata_bytes,
        "wholePackageBytes": whole_package_bytes,
        "recommendedForStorage": recommended_for_storage,
        "adoptionDecision": adoption_decision,
        "fallbackReason": fallback_reason,
        "inputDigest": input_digest,
        "payloadDigest": sha256_digest(candidate.payload),
        "minSavingBytes": min_saving_bytes,
        "candidateSummaries": candidate_summaries,
    }
    if candidate.codec == "zlib":
        metadata["zlibLevel"] = 9
    if candidate.codec == "cdf-oracle":
        metadata["selectedProfileId"] = candidate.profile_id
        metadata["oracle"] = candidate.oracle_metadata
    return metadata


def _finalize_pack_metadata(
    candidate: _CdfPackCandidate,
    *,
    raw_bytes: int,
    input_digest: str,
    min_saving_bytes: int,
    candidate_summaries: list[dict[str, Any]],
    recommended_for_storage: bool,
    adoption_decision: str,
    fallback_reason: str,
) -> dict[str, Any]:
    metadata_bytes = 0
    whole_package_bytes = 0
    metadata: dict[str, Any] | None = None
    for _ in range(12):
        metadata = _build_pack_metadata(
            candidate,
            raw_bytes=raw_bytes,
            input_digest=input_digest,
            min_saving_bytes=min_saving_bytes,
            candidate_summaries=candidate_summaries,
            recommended_for_storage=recommended_for_storage,
            adoption_decision=adoption_decision,
            fallback_reason=fallback_reason,
            metadata_bytes=metadata_bytes,
            whole_package_bytes=whole_package_bytes,
        )
        next_metadata_bytes = len(_compact_json_bytes(metadata))
        next_whole_package_bytes = len(candidate.payload) + next_metadata_bytes
        if (
            next_metadata_bytes == metadata_bytes
            and next_whole_package_bytes == whole_package_bytes
        ):
            return metadata
        metadata_bytes = next_metadata_bytes
        whole_package_bytes = next_whole_package_bytes

    if metadata is None:
        raise CdfOracleError("could not build CDF pack metadata")
    if len(_compact_json_bytes(metadata)) != metadata.get("metadataBytes"):
        raise CdfOracleError("CDF pack metadata size did not stabilize")
    return metadata


def _sync_selected_candidate_summary(
    candidate_summaries: list[dict[str, Any]],
    candidate: _CdfPackCandidate,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    summaries = [dict(summary) for summary in candidate_summaries]
    selected_found = False
    selected_key = (candidate.codec, candidate.profile_id)
    for summary in summaries:
        if _candidate_summary_key(summary) != selected_key:
            continue
        if summary.get("payloadBytes") != len(candidate.payload):
            continue
        saving_bytes = metadata["rawBytes"] - metadata["wholePackageBytes"]
        summary["estimatedMetadataBytes"] = metadata["metadataBytes"]
        summary["estimatedWholeBytes"] = metadata["wholePackageBytes"]
        summary["savingBytesVsRaw"] = saving_bytes
        summary["passesBenefitGate"] = (
            candidate.codec != "stored" and saving_bytes >= metadata["minSavingBytes"]
        )
        selected_found = True
    if not selected_found:
        raise CdfOracleError("selected CDF pack candidate summary missing")
    return summaries


def _finalize_pack_metadata_with_selected_summary(
    candidate: _CdfPackCandidate,
    *,
    raw_bytes: int,
    input_digest: str,
    min_saving_bytes: int,
    candidate_summaries: list[dict[str, Any]],
    recommended_for_storage: bool,
    adoption_decision: str,
    fallback_reason: str,
) -> dict[str, Any]:
    summaries = candidate_summaries
    metadata: dict[str, Any] | None = None
    for _ in range(12):
        metadata = _finalize_pack_metadata(
            candidate,
            raw_bytes=raw_bytes,
            input_digest=input_digest,
            min_saving_bytes=min_saving_bytes,
            candidate_summaries=summaries,
            recommended_for_storage=recommended_for_storage,
            adoption_decision=adoption_decision,
            fallback_reason=fallback_reason,
        )
        next_summaries = _sync_selected_candidate_summary(
            summaries, candidate, metadata
        )
        if next_summaries == summaries:
            return metadata
        summaries = next_summaries
    raise CdfOracleError("CDF pack selected summary size did not stabilize")


def _estimate_pack_candidates(
    candidates: list[_CdfPackCandidate],
    *,
    raw_bytes: int,
    input_digest: str,
    min_saving_bytes: int,
) -> list[dict[str, Any]]:
    summaries = _build_candidate_summaries(
        candidates,
        {},
        raw_bytes=raw_bytes,
        min_saving_bytes=min_saving_bytes,
    )
    for _ in range(12):
        metrics: dict[str, tuple[int, int]] = {}
        for candidate in candidates:
            metadata = _finalize_pack_metadata(
                candidate,
                raw_bytes=raw_bytes,
                input_digest=input_digest,
                min_saving_bytes=min_saving_bytes,
                candidate_summaries=summaries,
                recommended_for_storage=False,
                adoption_decision="candidate-size-estimate",
                fallback_reason="",
            )
            metrics[_pack_candidate_label(candidate)] = (
                metadata["metadataBytes"],
                metadata["wholePackageBytes"],
            )
        next_summaries = _build_candidate_summaries(
            candidates,
            metrics,
            raw_bytes=raw_bytes,
            min_saving_bytes=min_saving_bytes,
        )
        if next_summaries == summaries:
            return summaries
        summaries = next_summaries
    return summaries


def _candidate_summary_key(summary: dict[str, Any]) -> tuple[str, str | None]:
    profile_id = summary.get("profileId")
    if profile_id is not None and not isinstance(profile_id, str):
        raise CdfOracleError("invalid CDF pack candidate profile id")
    return summary.get("codec"), profile_id


def _select_candidate(
    candidates: list[_CdfPackCandidate],
    summaries: list[dict[str, Any]],
    *,
    min_saving_bytes: int,
) -> tuple[_CdfPackCandidate, bool, str, str]:
    summaries_by_key = {_candidate_summary_key(summary): summary for summary in summaries}

    def sort_key(candidate: _CdfPackCandidate) -> tuple[int, int]:
        summary = summaries_by_key[(candidate.codec, candidate.profile_id)]
        return summary["estimatedWholeBytes"], candidate.order

    best = min(candidates, key=sort_key)
    best_summary = summaries_by_key[(best.codec, best.profile_id)]
    if best.codec != "stored" and best_summary["savingBytesVsRaw"] >= min_saving_bytes:
        return (
            best,
            True,
            f"selected-{best.codec}-whole-package-benefit",
            "",
        )

    stored = candidates[0]
    best_nonstored = min(candidates[1:], key=sort_key) if len(candidates) > 1 else None
    if best_nonstored is None:
        detail = "no non-stored candidate was available"
    else:
        summary = summaries_by_key[(best_nonstored.codec, best_nonstored.profile_id)]
        detail = (
            f"best non-stored candidate saved {summary['savingBytesVsRaw']} "
            f"bytes versus raw input"
        )
    return (
        stored,
        False,
        "stored-exact-fallback",
        f"no candidate saved at least {min_saving_bytes} whole-package bytes; {detail}",
    )


def pack_cdf_oracle(
    data: bytes,
    profiles: tuple[str, ...] = (PPM_PROFILE_ID,),
    min_saving_bytes: int = 1,
) -> CdfOraclePackResult:
    if not isinstance(data, (bytes, bytearray)):
        raise CdfOracleError("CDF pack input must be bytes")
    if (
        not isinstance(min_saving_bytes, int)
        or isinstance(min_saving_bytes, bool)
        or min_saving_bytes < 0
    ):
        raise CdfOracleError("min_saving_bytes must be a non-negative integer")
    if isinstance(profiles, str):
        profile_ids = (profiles,)
    else:
        profile_ids = tuple(profiles)

    _require_package_raw_bytes_within_limit(len(data))
    raw = bytes(data)
    input_digest = sha256_digest(raw)
    candidates = [
        _CdfPackCandidate(codec="stored", payload=raw, order=0),
        _CdfPackCandidate(codec="zlib", payload=zlib.compress(raw, 9), order=1),
    ]
    for index, profile_id in enumerate(profile_ids, start=2):
        encoded = encode_cdf_oracle(raw, profile_id=profile_id)
        candidates.append(
            _CdfPackCandidate(
                codec="cdf-oracle",
                payload=encoded.payload,
                order=index,
                profile_id=profile_id,
                oracle_metadata=encoded.metadata,
            )
        )

    summaries = _estimate_pack_candidates(
        candidates,
        raw_bytes=len(raw),
        input_digest=input_digest,
        min_saving_bytes=min_saving_bytes,
    )
    selected, recommended, adoption_decision, fallback_reason = _select_candidate(
        candidates,
        summaries,
        min_saving_bytes=min_saving_bytes,
    )
    metadata = _finalize_pack_metadata_with_selected_summary(
        selected,
        raw_bytes=len(raw),
        input_digest=input_digest,
        min_saving_bytes=min_saving_bytes,
        candidate_summaries=summaries,
        recommended_for_storage=recommended,
        adoption_decision=adoption_decision,
        fallback_reason=fallback_reason,
    )
    if recommended and len(raw) - metadata["wholePackageBytes"] < min_saving_bytes:
        selected = candidates[0]
        recommended = False
        adoption_decision = "stored-exact-fallback"
        fallback_reason = (
            f"finalized selected package saved "
            f"{len(raw) - metadata['wholePackageBytes']} bytes versus raw input, "
            f"below the {min_saving_bytes}-byte benefit gate"
        )
        metadata = _finalize_pack_metadata_with_selected_summary(
            selected,
            raw_bytes=len(raw),
            input_digest=input_digest,
            min_saving_bytes=min_saving_bytes,
            candidate_summaries=summaries,
            recommended_for_storage=recommended,
            adoption_decision=adoption_decision,
            fallback_reason=fallback_reason,
        )
    return CdfOraclePackResult(metadata=metadata, payload=selected.payload)


def _validate_candidate_summaries(
    value: Any,
    *,
    selected_codec: str,
    selected_profile_id: str | None,
    payload_bytes: int,
    metadata_bytes: int,
    whole_package_bytes: int,
    raw_bytes: int,
    min_saving_bytes: int,
    recommended_for_storage: bool,
) -> None:
    if not isinstance(value, list) or not value:
        raise CdfOracleError("CDF pack candidate summaries must be a non-empty list")
    selected_found = False
    selected_passes_gate = False
    for summary in value:
        if not isinstance(summary, dict):
            raise CdfOracleError("CDF pack candidate summary must be a dict")
        codec = summary.get("codec")
        if codec not in {"stored", "zlib", "cdf-oracle"}:
            raise CdfOracleError("unsupported CDF pack candidate codec")
        summary_payload_bytes = summary.get("payloadBytes")
        summary_metadata_bytes = summary.get("estimatedMetadataBytes")
        summary_whole_bytes = summary.get("estimatedWholeBytes")
        if (
            not isinstance(summary_payload_bytes, int)
            or isinstance(summary_payload_bytes, bool)
            or summary_payload_bytes < 0
            or not isinstance(summary_metadata_bytes, int)
            or isinstance(summary_metadata_bytes, bool)
            or summary_metadata_bytes < 0
            or not isinstance(summary_whole_bytes, int)
            or isinstance(summary_whole_bytes, bool)
            or summary_whole_bytes < summary_payload_bytes
        ):
            raise CdfOracleError("invalid CDF pack candidate size summary")
        if summary_whole_bytes != summary_payload_bytes + summary_metadata_bytes:
            raise CdfOracleError("CDF pack candidate whole size summary mismatch")
        saving = summary.get("savingBytesVsRaw")
        if not isinstance(saving, int) or isinstance(saving, bool):
            raise CdfOracleError("invalid CDF pack candidate saving summary")
        if saving != raw_bytes - summary_whole_bytes:
            raise CdfOracleError("CDF pack candidate saving summary mismatch")
        passes_gate = summary.get("passesBenefitGate")
        if not isinstance(passes_gate, bool):
            raise CdfOracleError("invalid CDF pack candidate gate summary")
        if passes_gate != (codec != "stored" and saving >= min_saving_bytes):
            raise CdfOracleError("CDF pack candidate benefit gate summary mismatch")
        profile_id = summary.get("profileId")
        if codec == "cdf-oracle":
            if not isinstance(profile_id, str):
                raise CdfOracleError("CDF oracle candidate summary missing profile id")
        elif profile_id is not None:
            raise CdfOracleError("non-CDF candidate summary must not include profile id")
        if (
            codec == selected_codec
            and profile_id == selected_profile_id
            and summary_payload_bytes == payload_bytes
        ):
            if summary_metadata_bytes != metadata_bytes:
                raise CdfOracleError(
                    "selected CDF pack candidate metadata size summary mismatch"
                )
            if summary_whole_bytes != whole_package_bytes:
                raise CdfOracleError(
                    "selected CDF pack candidate whole package summary mismatch"
                )
            selected_found = True
            selected_passes_gate = passes_gate
    if not selected_found:
        raise CdfOracleError("selected CDF pack candidate summary missing")
    if selected_passes_gate != recommended_for_storage:
        raise CdfOracleError("selected CDF pack candidate summary contradicts recommendation")


def _require_package_raw_bytes_within_limit(raw_bytes: int) -> None:
    if raw_bytes > CDF_PACK_MAX_RAW_BYTES:
        raise CdfOracleError("CDF pack rawBytes exceeds resource limit")


def _require_package_oracle_input_bytes(metadata: dict[str, Any]) -> int:
    oracle_input_bytes = metadata.get("inputBytes")
    if (
        not isinstance(oracle_input_bytes, int)
        or isinstance(oracle_input_bytes, bool)
        or oracle_input_bytes < 0
    ):
        raise CdfOracleError("invalid CDF pack oracle inputBytes")
    if oracle_input_bytes > CDF_PACK_MAX_RAW_BYTES:
        raise CdfOracleError("CDF pack oracle inputBytes exceeds resource limit")
    return oracle_input_bytes


def _decompress_zlib_bounded(payload: bytes, expected_raw_bytes: int) -> bytes:
    decompressor = zlib.decompressobj()
    try:
        decoded = decompressor.decompress(payload, expected_raw_bytes + 1)
        if len(decoded) > expected_raw_bytes:
            raise CdfOracleError("CDF pack zlib payload exceeds declared rawBytes")
        if decompressor.unconsumed_tail:
            raise CdfOracleError("CDF pack zlib payload exceeds declared rawBytes")
        extra = decompressor.decompress(b"", 1)
        if extra:
            raise CdfOracleError("CDF pack zlib payload exceeds declared rawBytes")
        if not decompressor.eof:
            raise CdfOracleError("CDF pack zlib payload did not decompress")
        if decompressor.unused_data:
            raise CdfOracleError("CDF pack zlib payload has trailing data")
        flushed = decompressor.flush()
    except zlib.error as exc:
        raise CdfOracleError("CDF pack zlib payload did not decompress") from exc
    if flushed:
        raise CdfOracleError("CDF pack zlib payload exceeds declared rawBytes")
    return decoded


def _validate_pack_metadata(payload: bytes, metadata: dict[str, Any]) -> tuple[str, str | None]:
    if not isinstance(metadata, dict):
        raise CdfOracleError("CDF pack metadata must be a dict")
    allowed_fields = {
        "packageKind",
        "metadataSchema",
        "metadataEncoding",
        "selectedCodec",
        "selectedProfileId",
        "rawBytes",
        "payloadBytes",
        "metadataBytes",
        "wholePackageBytes",
        "recommendedForStorage",
        "adoptionDecision",
        "fallbackReason",
        "inputDigest",
        "payloadDigest",
        "minSavingBytes",
        "candidateSummaries",
        "oracle",
        "zlibLevel",
    }
    unexpected = set(metadata) - allowed_fields
    if unexpected:
        raise CdfOracleError("unexpected CDF pack metadata field")
    for field, expected in {
        "packageKind": CDF_PACK_KIND,
        "metadataSchema": CDF_PACK_METADATA_SCHEMA,
        "metadataEncoding": CDF_PACK_METADATA_ENCODING,
    }.items():
        if metadata.get(field) != expected:
            raise CdfOracleError(f"CDF pack {field} mismatch")

    selected_codec = metadata.get("selectedCodec")
    if selected_codec not in {"stored", "zlib", "cdf-oracle"}:
        raise CdfOracleError("unsupported CDF pack selected codec")
    raw_bytes = _require_package_int(metadata, "rawBytes")
    payload_bytes = _require_package_int(metadata, "payloadBytes")
    metadata_bytes = _require_package_int(metadata, "metadataBytes")
    whole_package_bytes = _require_package_int(metadata, "wholePackageBytes")
    min_saving_bytes = _require_package_int(metadata, "minSavingBytes")
    _require_package_raw_bytes_within_limit(raw_bytes)
    if payload_bytes != len(payload):
        raise CdfOracleError("CDF pack payload byte length mismatch")
    if metadata_bytes != len(_compact_json_bytes(metadata)):
        raise CdfOracleError("CDF pack metadata byte length mismatch")
    if whole_package_bytes != payload_bytes + metadata_bytes:
        raise CdfOracleError("CDF pack whole package byte length mismatch")
    if not isinstance(metadata.get("recommendedForStorage"), bool):
        raise CdfOracleError("invalid CDF pack storage recommendation")
    if not isinstance(metadata.get("adoptionDecision"), str):
        raise CdfOracleError("invalid CDF pack adoption decision")
    if not isinstance(metadata.get("fallbackReason"), str):
        raise CdfOracleError("invalid CDF pack fallback reason")
    _require_package_digest(metadata, "inputDigest")
    payload_digest = _require_package_digest(metadata, "payloadDigest")
    if sha256_digest(payload) != payload_digest:
        raise CdfOracleError("CDF pack payload digest mismatch")
    if metadata["recommendedForStorage"]:
        if selected_codec == "stored":
            raise CdfOracleError("stored CDF pack must not be recommended for storage")
        if raw_bytes - whole_package_bytes < min_saving_bytes:
            raise CdfOracleError("CDF pack storage recommendation does not pass gate")

    selected_profile_id = metadata.get("selectedProfileId")
    if selected_codec == "cdf-oracle":
        if not isinstance(selected_profile_id, str):
            raise CdfOracleError("CDF oracle pack missing selected profile id")
        if not isinstance(metadata.get("oracle"), dict):
            raise CdfOracleError("CDF oracle pack missing oracle metadata")
        oracle_input_bytes = _require_package_oracle_input_bytes(metadata["oracle"])
        if oracle_input_bytes != raw_bytes:
            raise CdfOracleError("CDF pack oracle input byte length mismatch")
        if "zlibLevel" in metadata:
            raise CdfOracleError("CDF oracle pack must not include zlib metadata")
    else:
        if selected_profile_id is not None:
            raise CdfOracleError("non-CDF pack must not include selected profile id")
        if "oracle" in metadata:
            raise CdfOracleError("non-CDF pack must not include oracle metadata")
    if selected_codec == "zlib":
        if metadata.get("zlibLevel") != 9:
            raise CdfOracleError("CDF pack zlib level mismatch")
    elif "zlibLevel" in metadata:
        raise CdfOracleError("non-zlib pack must not include zlib metadata")

    _validate_candidate_summaries(
        metadata.get("candidateSummaries"),
        selected_codec=selected_codec,
        selected_profile_id=selected_profile_id,
        payload_bytes=payload_bytes,
        metadata_bytes=metadata_bytes,
        whole_package_bytes=whole_package_bytes,
        raw_bytes=raw_bytes,
        min_saving_bytes=min_saving_bytes,
        recommended_for_storage=metadata["recommendedForStorage"],
    )
    if min_saving_bytes < 0 or raw_bytes < 0:
        raise CdfOracleError("invalid CDF pack size metadata")
    return selected_codec, selected_profile_id


def open_cdf_oracle_pack(payload: bytes, metadata: dict[str, Any]) -> bytes:
    selected_codec, selected_profile_id = _validate_pack_metadata(payload, metadata)
    expected_raw_bytes = metadata["rawBytes"]
    expected_input_digest = metadata["inputDigest"]

    if selected_codec == "stored":
        decoded = bytes(payload)
    elif selected_codec == "zlib":
        decoded = _decompress_zlib_bounded(payload, expected_raw_bytes)
    elif selected_codec == "cdf-oracle":
        oracle_metadata = metadata["oracle"]
        if oracle_metadata.get("profileId") != selected_profile_id:
            raise CdfOracleError("CDF pack oracle profile mismatch")
        if oracle_metadata.get("inputDigest") != expected_input_digest:
            raise CdfOracleError("CDF pack oracle input digest mismatch")
        if oracle_metadata.get("payloadBytes") != metadata["payloadBytes"]:
            raise CdfOracleError("CDF pack oracle payload byte length mismatch")
        if oracle_metadata.get("payloadDigest") != metadata["payloadDigest"]:
            raise CdfOracleError("CDF pack oracle payload digest mismatch")
        decoded = decode_cdf_oracle(payload, oracle_metadata)
    else:
        raise CdfOracleError("unsupported CDF pack selected codec")

    if len(decoded) != expected_raw_bytes:
        raise CdfOracleError("CDF pack decoded byte length mismatch")
    if sha256_digest(decoded) != expected_input_digest:
        raise CdfOracleError("CDF pack decoded digest mismatch")
    return decoded


def _validate_metadata(
    metadata: dict[str, Any], *, payload_length: int
) -> tuple[_CdfOracleProfile, int]:
    if not isinstance(metadata, dict):
        raise CdfOracleError("metadata must be a dict")
    profile = _get_profile(metadata.get("profileId"))
    if metadata.get("profileHash") != profile.profile_hash:
        raise CdfOracleError("CDF oracle profile hash mismatch")
    if metadata.get("profileSpec") != profile.profile_spec:
        raise CdfOracleError("CDF oracle profile spec mismatch")
    if metadata.get("coderId") != CODER_ID:
        raise CdfOracleError("unsupported CDF oracle coder")
    for field, expected in {
        "cdfTotal": CDF_TOTAL,
        "contextWindow": profile.context_window,
        **profile.metadata_fields,
    }.items():
        if not _metadata_value_matches(metadata.get(field), expected):
            raise CdfOracleError(f"CDF oracle {field} metadata mismatch")
    if metadata.get("standalonePrototype") is not True:
        raise CdfOracleError("CDF oracle standalone prototype flag mismatch")
    if metadata.get("productionSlb1Compatible") is not False:
        raise CdfOracleError("CDF oracle production compatibility flag mismatch")

    input_bytes = metadata.get("inputBytes")
    if (
        not isinstance(input_bytes, int)
        or isinstance(input_bytes, bool)
        or input_bytes < 0
        or input_bytes > CDF_PACK_MAX_RAW_BYTES
    ):
        raise CdfOracleError("invalid inputBytes")
    payload_bytes = metadata.get("payloadBytes")
    if not isinstance(payload_bytes, int) or isinstance(payload_bytes, bool):
        raise CdfOracleError("invalid payloadBytes")
    if payload_bytes != payload_length:
        raise CdfOracleError("CDF oracle payload byte length mismatch")
    bit_length = metadata.get("encodedBitLength")
    if not isinstance(bit_length, int) or isinstance(bit_length, bool):
        raise CdfOracleError("invalid encodedBitLength")
    if bit_length < 0 or bit_length > payload_length * 8:
        raise CdfOracleError("CDF oracle encoded bit length out of range")
    payload_bits_per_byte = metadata.get("payloadBitsPerByte")
    expected_bits_per_byte = bit_length / input_bytes if input_bytes else 0.0
    if payload_bits_per_byte != expected_bits_per_byte:
        raise CdfOracleError("CDF oracle payload bits-per-byte mismatch")
    payload_ratio = metadata.get("payloadRatio")
    expected_payload_ratio = payload_length / input_bytes if input_bytes else 0.0
    if payload_ratio != expected_payload_ratio:
        raise CdfOracleError("CDF oracle payload ratio mismatch")
    input_digest = metadata.get("inputDigest")
    if not isinstance(input_digest, str) or not input_digest.startswith("sha256:"):
        raise CdfOracleError("invalid inputDigest")
    payload_digest = metadata.get("payloadDigest")
    if not isinstance(payload_digest, str) or not payload_digest.startswith("sha256:"):
        raise CdfOracleError("invalid payloadDigest")
    return profile, input_bytes


def encode_cdf_oracle(data: bytes, profile_id: str = PROFILE_ID) -> CdfOracleResult:
    profile = _get_profile(profile_id)
    low = 0
    high = _MAX_CODE
    pending_bits = 0
    writer = _BitWriter()
    decoded_context = bytearray()

    for symbol in data:
        cdf = _cdf_for_profile(
            _context_tail_for_profile(decoded_context, profile), profile
        )
        span = high - low + 1
        high = low + (span * cdf[symbol + 1] // CDF_TOTAL) - 1
        low = low + (span * cdf[symbol] // CDF_TOTAL)

        while True:
            if high < _HALF:
                _write_with_pending(writer, 0, pending_bits)
                pending_bits = 0
            elif low >= _HALF:
                _write_with_pending(writer, 1, pending_bits)
                pending_bits = 0
                low -= _HALF
                high -= _HALF
            elif low >= _FIRST_QTR and high < _THIRD_QTR:
                pending_bits += 1
                low -= _FIRST_QTR
                high -= _FIRST_QTR
            else:
                break
            low = low * 2
            high = high * 2 + 1
        decoded_context.append(symbol)

    pending_bits += 1
    if low < _FIRST_QTR:
        _write_with_pending(writer, 0, pending_bits)
    else:
        _write_with_pending(writer, 1, pending_bits)

    payload = writer.finish()
    bit_length = writer.bit_count
    payload_bits_per_byte = bit_length / len(data) if data else 0.0
    metadata = {
        **profile_metadata(profile.profile_id),
        "inputBytes": len(data),
        "inputDigest": sha256_digest(data),
        "payloadDigest": sha256_digest(payload),
        "payloadBytes": len(payload),
        "encodedBitLength": bit_length,
        "payloadBitsPerByte": payload_bits_per_byte,
        "payloadRatio": len(payload) / len(data) if data else 0.0,
    }
    return CdfOracleResult(metadata=metadata, payload=payload)


def decode_cdf_oracle(payload: bytes, metadata: dict[str, Any]) -> bytes:
    profile, input_bytes = _validate_metadata(metadata, payload_length=len(payload))
    if sha256_digest(payload) != metadata["payloadDigest"]:
        raise CdfOracleError("CDF oracle payload digest mismatch")
    reader = _BitReader(payload)
    value = 0
    for _ in range(_STATE_BITS):
        value = (value << 1) | reader.read()

    low = 0
    high = _MAX_CODE
    output = bytearray()
    for _ in range(input_bytes):
        cdf = _cdf_for_profile(_context_tail_for_profile(output, profile), profile)
        span = high - low + 1
        scaled = ((value - low + 1) * CDF_TOTAL - 1) // span
        symbol = bisect.bisect_right(cdf, scaled) - 1
        if symbol < 0 or symbol > 255:
            raise CdfOracleError("payload does not decode under the CDF oracle profile")

        high = low + (span * cdf[symbol + 1] // CDF_TOTAL) - 1
        low = low + (span * cdf[symbol] // CDF_TOTAL)

        while True:
            if high < _HALF:
                pass
            elif low >= _HALF:
                value -= _HALF
                low -= _HALF
                high -= _HALF
            elif low >= _FIRST_QTR and high < _THIRD_QTR:
                value -= _FIRST_QTR
                low -= _FIRST_QTR
                high -= _FIRST_QTR
            else:
                break
            low = low * 2
            high = high * 2 + 1
            value = value * 2 + reader.read()
        output.append(symbol)

    decoded = bytes(output)
    if sha256_digest(decoded) != metadata["inputDigest"]:
        raise CdfOracleError("decoded CDF oracle digest mismatch")
    expected = encode_cdf_oracle(decoded, profile_id=profile.profile_id)
    if expected.payload != payload:
        raise CdfOracleError("CDF oracle payload is not canonical for decoded input")
    if metadata["encodedBitLength"] != expected.metadata["encodedBitLength"]:
        raise CdfOracleError("CDF oracle encoded bit length mismatch")
    return decoded
