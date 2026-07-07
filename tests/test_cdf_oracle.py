from __future__ import annotations

import random

import pytest

import starlight_codec.cdf_oracle as cdf_oracle
from starlight_codec.cdf_oracle import (
    CDF_PROFILE_DECODE_CONTRACT,
    CDF_TOTAL,
    CdfOracleError,
    PROFILE_HASH,
    cdf_for_context,
    decode_cdf_oracle,
    encode_cdf_oracle,
)
from starlight_codec.cdf_profile_registry import profile_hash


def test_cdf_is_deterministic_monotonic_positive_and_totaled() -> None:
    context = b"hello hello hello\x00\xff"

    first = cdf_for_context(context)
    second = cdf_for_context(context)

    assert first == second
    assert len(first) == 257
    assert first[0] == 0
    assert first[-1] == CDF_TOTAL
    freqs = [right - left for left, right in zip(first, first[1:])]
    assert all(freq > 0 for freq in freqs)
    assert all(left < right for left, right in zip(first, first[1:]))


def test_profile_hash_is_registry_decode_contract_hash() -> None:
    assert PROFILE_HASH == profile_hash(CDF_PROFILE_DECODE_CONTRACT)


def test_cdf_trace_depends_on_previous_decoded_context_only() -> None:
    data = b"abcabcabcabc"
    encoded = encode_cdf_oracle(data)
    decoded_context = bytearray()

    for symbol in data:
        encoder_cdf = cdf_for_context(bytes(decoded_context))
        decoder_cdf = cdf_for_context(bytes(decoded_context))
        assert encoder_cdf == decoder_cdf
        assert encoder_cdf[symbol] < encoder_cdf[symbol + 1]
        decoded_context.append(symbol)

    assert decode_cdf_oracle(encoded.payload, encoded.metadata) == data


def test_encode_decode_passes_bounded_context_tail_to_cdf(monkeypatch) -> None:
    original_cdf_for_profile = cdf_oracle._cdf_for_profile
    seen_context_lengths: list[int] = []

    def spy_cdf_for_profile(context, profile):
        seen_context_lengths.append(len(context))
        assert len(context) <= profile.context_window
        return original_cdf_for_profile(context, profile)

    monkeypatch.setattr(cdf_oracle, "_cdf_for_profile", spy_cdf_for_profile)
    data = bytes(range(256)) * 3

    encoded = cdf_oracle.encode_cdf_oracle(data)

    assert cdf_oracle.decode_cdf_oracle(encoded.payload, encoded.metadata) == data
    assert max(seen_context_lengths) == cdf_oracle.CONTEXT_WINDOW


@pytest.mark.parametrize(
    "data",
    [
        b"",
        (b"def fn(x):\n    return x + 1\n" * 80),
        random.Random(12345).randbytes(2048),
        bytes([0, 1, 0, 2, 0, 3, 255, 0]) * 128,
    ],
)
def test_encode_decode_round_trips_representative_bytes(data: bytes) -> None:
    encoded = encode_cdf_oracle(data)

    assert decode_cdf_oracle(encoded.payload, encoded.metadata) == data
    assert encoded.metadata["payloadBytes"] == len(encoded.payload)
    assert encoded.metadata["standalonePrototype"] is True
    assert encoded.metadata["productionSlb1Compatible"] is False


def test_metadata_and_profile_tampering_fail_closed() -> None:
    encoded = encode_cdf_oracle(b"tamper me" * 50)

    bad_profile = dict(encoded.metadata)
    bad_profile["profileHash"] = "sha256:" + ("0" * 64)
    with pytest.raises(CdfOracleError):
        decode_cdf_oracle(encoded.payload, bad_profile)

    bad_digest = dict(encoded.metadata)
    bad_digest["inputDigest"] = "sha256:" + ("0" * 64)
    with pytest.raises(CdfOracleError):
        decode_cdf_oracle(encoded.payload, bad_digest)

    bad_payload = bytearray(encoded.payload)
    bad_payload[-1] ^= 0x01
    with pytest.raises(CdfOracleError):
        decode_cdf_oracle(bytes(bad_payload), encoded.metadata)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profileSpec", "tampered-profile-spec"),
        ("payloadBytes", 0),
        ("encodedBitLength", -1),
        ("encodedBitLength", 0),
        ("encodedBitLength", True),
        ("standalonePrototype", False),
        ("productionSlb1Compatible", True),
        ("payloadBitsPerByte", 0.0),
        ("payloadRatio", 0.0),
    ],
)
def test_emitted_contract_metadata_tampering_fails_closed(field: str, value: object) -> None:
    encoded = encode_cdf_oracle(b"contract metadata tamper" * 20)
    tampered = dict(encoded.metadata)
    tampered[field] = value

    with pytest.raises(CdfOracleError):
        decode_cdf_oracle(encoded.payload, tampered)


@pytest.mark.parametrize("input_bytes", [False, True])
def test_input_bytes_bool_metadata_fails_closed(input_bytes: bool) -> None:
    encoded = encode_cdf_oracle(b"")
    tampered = dict(encoded.metadata)
    tampered["inputBytes"] = input_bytes
    divisor = 1 if input_bytes else 0
    tampered["payloadBitsPerByte"] = (
        tampered["encodedBitLength"] / divisor if divisor else 0.0
    )
    tampered["payloadRatio"] = len(encoded.payload) / divisor if divisor else 0.0

    with pytest.raises(CdfOracleError, match="invalid inputBytes"):
        decode_cdf_oracle(encoded.payload, tampered)


def test_predictable_data_uses_fewer_bits_than_random_like_data() -> None:
    predictable = (b"abcabcabcabc0000\n" * 160)
    random_like = random.Random(67890).randbytes(len(predictable))

    predictable_encoded = encode_cdf_oracle(predictable)
    random_encoded = encode_cdf_oracle(random_like)

    assert (
        predictable_encoded.metadata["payloadBitsPerByte"]
        < random_encoded.metadata["payloadBitsPerByte"]
    )
    assert predictable_encoded.metadata["payloadRatio"] < random_encoded.metadata["payloadRatio"]
