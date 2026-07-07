from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cdf_oracle import (
    PPM_PROFILE_ID as DEFAULT_CDF_PACK_PROFILE_ID,
    PROFILE_ID as DEFAULT_CDF_PROFILE_ID,
    CdfOracleError,
    decode_cdf_oracle,
    encode_cdf_oracle,
    open_cdf_oracle_pack,
    pack_cdf_oracle,
    sha256_digest,
)
from .cdf_profile_registry import (
    CdfProfileRegistryError,
    load_profile_descriptor,
    profile_descriptor_summary,
    validate_profile_descriptor,
)
from .cdf_public_registry import (
    CdfPublicRegistryError,
    auto_open_cdf_oracle_pack,
    auto_pack_cdf_oracle,
    fetch_public_component,
    fetch_public_profile_descriptor,
    list_public_components,
    list_public_profiles,
    plan_cdf_compression,
    plan_cdf_open_requirements,
)
from .codec import (
    create_capsule_file,
    create_capsule_pack_file,
    decode_file,
    encode_file,
    hydrate_file,
    inspect_slb1,
    token_report_file,
)


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def write_json_file(path: str | Path, value: object) -> None:
    Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slc", description="Star Light Codec reference CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    encode = sub.add_parser("encode", help="encode a file into an SLB1 artifact")
    encode.add_argument("input")
    encode.add_argument("output")
    encode.add_argument("--max-passes", type=int, default=1)
    encode.add_argument("--model", choices=["none", "auto", "delta-prev-v1"], default="none")
    encode.add_argument("--planner", choices=["gzip", "stdlib-auto"], default="gzip")

    decode = sub.add_parser("decode", help="decode an SLB1 artifact exactly")
    decode.add_argument("input")
    decode.add_argument("output")

    inspect = sub.add_parser("inspect", help="inspect an SLB1 artifact without decoding raw bytes")
    inspect.add_argument("input")

    capsule = sub.add_parser("capsule", help="encode a file and write an LLM transport capsule")
    capsule.add_argument("input")
    capsule.add_argument("artifact")
    capsule.add_argument("capsule")
    capsule.add_argument("--max-passes", type=int, default=1)
    capsule.add_argument("--model", choices=["none", "auto", "delta-prev-v1"], default="none")
    capsule.add_argument("--planner", choices=["gzip", "stdlib-auto"], default="gzip")
    capsule.add_argument("--summary", default="")
    capsule.add_argument("--tag", action="append", default=[])
    capsule.add_argument("--chunk-size", type=int, default=4096)

    capsule_pack = sub.add_parser("capsule-pack", help="write a recursive LLM capsule pack")
    capsule_pack.add_argument("output")
    capsule_pack.add_argument("input", nargs="+")
    capsule_pack.add_argument("--summary", default="")
    capsule_pack.add_argument("--tag", action="append", default=[])

    token_report = sub.add_parser("token-report", help="estimate raw prompt tokens vs capsule prompt tokens")
    token_report.add_argument("input", nargs="+")

    profile = sub.add_parser("profile", help="validate and inspect CDF profile descriptors")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_validate = profile_sub.add_parser("validate", help="validate a CDF profile descriptor")
    profile_validate.add_argument("descriptor")
    profile_show = profile_sub.add_parser("show", help="show a validated CDF profile descriptor")
    profile_show.add_argument("descriptor")
    profile_sub.add_parser("list", help="list bundled public CDF profiles")
    profile_fetch = profile_sub.add_parser("fetch", help="fetch a bundled public CDF profile")
    profile_fetch.add_argument("profile_id")
    profile_fetch.add_argument("output_dir")

    component = sub.add_parser("component", help="inspect bundled public codec components")
    component_sub = component.add_subparsers(dest="component_command", required=True)
    component_list = component_sub.add_parser("list", help="list bundled public components")
    component_list.add_argument("--role", choices=["encoder", "decoder"])
    component_list.add_argument("--kind")
    component_fetch = component_sub.add_parser("fetch", help="fetch bundled component metadata")
    component_fetch.add_argument("component_id")
    component_fetch.add_argument("output_dir")

    cdf = sub.add_parser("cdf", help="encode and decode standalone CDF oracle payloads")
    cdf_sub = cdf.add_subparsers(dest="cdf_command", required=True)
    cdf_encode = cdf_sub.add_parser("encode", help="encode a CDF oracle payload")
    cdf_encode.add_argument("input")
    cdf_encode.add_argument("payload")
    cdf_encode.add_argument("metadata")
    cdf_encode.add_argument("--profile", default=DEFAULT_CDF_PROFILE_ID)
    cdf_decode = cdf_sub.add_parser("decode", help="decode a CDF oracle payload")
    cdf_decode.add_argument("payload")
    cdf_decode.add_argument("metadata")
    cdf_decode.add_argument("output")
    cdf_pack = cdf_sub.add_parser("pack", help="benefit-gate a standalone CDF package")
    cdf_pack.add_argument("input")
    cdf_pack.add_argument("payload")
    cdf_pack.add_argument("metadata")
    cdf_pack.add_argument("--profile", action="append", default=None)
    cdf_pack.add_argument("--min-saving-bytes", type=int, default=1)
    cdf_open = cdf_sub.add_parser("open", help="open a benefit-gated CDF package")
    cdf_open.add_argument("payload")
    cdf_open.add_argument("metadata")
    cdf_open.add_argument("output")
    cdf_plan = cdf_sub.add_parser("plan", help="plan public CDF package candidates")
    cdf_plan.add_argument("input")
    cdf_plan.add_argument("--profile", action="append", default=None)
    cdf_plan.add_argument("--min-saving-bytes", type=int, default=1)
    cdf_auto_pack = cdf_sub.add_parser("auto-pack", help="pack through the public resolver")
    cdf_auto_pack.add_argument("input")
    cdf_auto_pack.add_argument("payload")
    cdf_auto_pack.add_argument("metadata")
    cdf_auto_pack.add_argument("--profile", action="append", default=None)
    cdf_auto_pack.add_argument("--min-saving-bytes", type=int, default=1)
    cdf_auto_pack.add_argument("--cache-dir")
    cdf_auto_open = cdf_sub.add_parser("auto-open", help="open through the public resolver")
    cdf_auto_open.add_argument("payload")
    cdf_auto_open.add_argument("metadata")
    cdf_auto_open.add_argument("output")
    cdf_auto_open.add_argument("--cache-dir")

    hydrate = sub.add_parser("hydrate", help="hydrate bytes from an SLB1 artifact or capsule")
    hydrate.add_argument("input")
    hydrate.add_argument("output")
    hydrate.add_argument("--range", dest="byte_range")
    hydrate.add_argument("--chunk")

    args = parser.parse_args(argv)
    if args.command == "encode":
        print_json(
            encode_file(
                args.input,
                args.output,
                max_passes=args.max_passes,
                model=args.model,
                planner=args.planner,
            )
        )
        return 0
    if args.command == "decode":
        print_json(decode_file(args.input, args.output))
        return 0
    if args.command == "inspect":
        print_json(inspect_slb1(Path(args.input).read_bytes()))
        return 0
    if args.command == "capsule":
        print_json(
            create_capsule_file(
                args.input,
                args.artifact,
                args.capsule,
                max_passes=args.max_passes,
                model=args.model,
                planner=args.planner,
                summary=args.summary,
                tags=args.tag,
                chunk_size=args.chunk_size,
            )
        )
        return 0
    if args.command == "capsule-pack":
        print_json(
            create_capsule_pack_file(
                args.input,
                args.output,
                summary=args.summary,
                tags=args.tag,
            )
        )
        return 0
    if args.command == "token-report":
        reports = [token_report_file(input_path) for input_path in args.input]
        print_json(reports[0] if len(reports) == 1 else {"items": reports})
        return 0
    if args.command == "profile":
        try:
            if args.profile_command == "list":
                print_json({"ok": True, "profiles": list_public_profiles()})
                return 0
            if args.profile_command == "fetch":
                print_json(
                    {
                        "ok": True,
                        "profile": fetch_public_profile_descriptor(
                            args.profile_id, args.output_dir
                        ),
                    }
                )
                return 0
            descriptor = load_profile_descriptor(args.descriptor)
            if args.profile_command == "validate":
                print_json(validate_profile_descriptor(descriptor).as_dict())
            elif args.profile_command == "show":
                print_json(
                    {
                        "validation": profile_descriptor_summary(descriptor),
                        "descriptor": descriptor,
                    }
                )
            else:
                parser.error("unknown profile command")
            return 0
        except (CdfProfileRegistryError, CdfPublicRegistryError, OSError) as exc:
            envelope = {"ok": False, "error": str(exc)}
            if args.profile_command in {"validate", "show"}:
                envelope = {"valid": False, "error": str(exc)}
            print_json(envelope)
            return 1
    if args.command == "component":
        try:
            if args.component_command == "list":
                print_json(
                    {
                        "ok": True,
                        "components": list_public_components(
                            role=args.role,
                            component_kind=args.kind,
                        ),
                    }
                )
                return 0
            if args.component_command == "fetch":
                print_json(
                    {
                        "ok": True,
                        "component": fetch_public_component(
                            args.component_id, args.output_dir
                        ),
                    }
                )
                return 0
            parser.error("unknown component command")
        except (CdfPublicRegistryError, OSError, json.JSONDecodeError) as exc:
            print_json({"ok": False, "error": str(exc)})
            return 1
    if args.command == "cdf":
        try:
            if args.cdf_command == "encode":
                data = Path(args.input).read_bytes()
                encoded = encode_cdf_oracle(data, profile_id=args.profile)
                Path(args.payload).write_bytes(encoded.payload)
                write_json_file(args.metadata, encoded.metadata)
                print_json(
                    {
                        "ok": True,
                        "action": "cdf-encode",
                        "profileId": encoded.metadata["profileId"],
                        "inputBytes": encoded.metadata["inputBytes"],
                        "inputDigest": encoded.metadata["inputDigest"],
                        "payloadBytes": encoded.metadata["payloadBytes"],
                        "payloadDigest": encoded.metadata["payloadDigest"],
                        "encodedBitLength": encoded.metadata["encodedBitLength"],
                        "payloadBitsPerByte": encoded.metadata["payloadBitsPerByte"],
                        "payloadRatio": encoded.metadata["payloadRatio"],
                    }
                )
                return 0
            if args.cdf_command == "decode":
                payload = Path(args.payload).read_bytes()
                metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
                decoded = decode_cdf_oracle(payload, metadata)
                Path(args.output).write_bytes(decoded)
                print_json(
                    {
                        "ok": True,
                        "action": "cdf-decode",
                        "profileId": metadata["profileId"],
                        "outputBytes": len(decoded),
                        "outputDigest": sha256_digest(decoded),
                        "digestMatch": sha256_digest(decoded)
                        == metadata["inputDigest"],
                    }
                )
                return 0
            if args.cdf_command == "pack":
                data = Path(args.input).read_bytes()
                profiles = tuple(args.profile or [DEFAULT_CDF_PACK_PROFILE_ID])
                packed = pack_cdf_oracle(
                    data,
                    profiles=profiles,
                    min_saving_bytes=args.min_saving_bytes,
                )
                Path(args.payload).write_bytes(packed.payload)
                write_json_file(args.metadata, packed.metadata)
                print_json(
                    {
                        "ok": True,
                        "action": "cdf-pack",
                        "selectedCodec": packed.metadata["selectedCodec"],
                        "selectedProfileId": packed.metadata.get("selectedProfileId"),
                        "rawBytes": packed.metadata["rawBytes"],
                        "payloadBytes": packed.metadata["payloadBytes"],
                        "metadataBytes": packed.metadata["metadataBytes"],
                        "wholePackageBytes": packed.metadata["wholePackageBytes"],
                        "recommendedForStorage": packed.metadata[
                            "recommendedForStorage"
                        ],
                        "adoptionDecision": packed.metadata["adoptionDecision"],
                        "fallbackReason": packed.metadata["fallbackReason"],
                        "inputDigest": packed.metadata["inputDigest"],
                        "payloadDigest": packed.metadata["payloadDigest"],
                        "candidateSummaries": packed.metadata["candidateSummaries"],
                    }
                )
                return 0
            if args.cdf_command == "open":
                payload = Path(args.payload).read_bytes()
                metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
                decoded = open_cdf_oracle_pack(payload, metadata)
                Path(args.output).write_bytes(decoded)
                print_json(
                    {
                        "ok": True,
                        "action": "cdf-open",
                        "selectedCodec": metadata["selectedCodec"],
                        "selectedProfileId": metadata.get("selectedProfileId"),
                        "outputBytes": len(decoded),
                        "outputDigest": sha256_digest(decoded),
                        "digestMatch": sha256_digest(decoded)
                        == metadata["inputDigest"],
                    }
                )
                return 0
            if args.cdf_command == "plan":
                data = Path(args.input).read_bytes()
                print_json(
                    {
                        "ok": True,
                        "action": "cdf-plan",
                        "plan": plan_cdf_compression(
                            data,
                            profiles=args.profile,
                            min_saving_bytes=args.min_saving_bytes,
                        ),
                    }
                )
                return 0
            if args.cdf_command == "auto-pack":
                data = Path(args.input).read_bytes()
                packed = auto_pack_cdf_oracle(
                    data,
                    profiles=args.profile,
                    min_saving_bytes=args.min_saving_bytes,
                    cache_dir=args.cache_dir,
                )
                Path(args.payload).write_bytes(packed.payload)
                write_json_file(args.metadata, packed.metadata)
                print_json(
                    {
                        "ok": True,
                        "action": "cdf-auto-pack",
                        "selectedCodec": packed.metadata["selectedCodec"],
                        "selectedProfileId": packed.metadata.get("selectedProfileId"),
                        "rawBytes": packed.metadata["rawBytes"],
                        "payloadBytes": packed.metadata["payloadBytes"],
                        "metadataBytes": packed.metadata["metadataBytes"],
                        "wholePackageBytes": packed.metadata["wholePackageBytes"],
                        "recommendedForStorage": packed.metadata[
                            "recommendedForStorage"
                        ],
                        "adoptionDecision": packed.metadata["adoptionDecision"],
                        "fallbackReason": packed.metadata["fallbackReason"],
                        "inputDigest": packed.metadata["inputDigest"],
                        "payloadDigest": packed.metadata["payloadDigest"],
                        "requirements": plan_cdf_open_requirements(packed.metadata),
                    }
                )
                return 0
            if args.cdf_command == "auto-open":
                payload = Path(args.payload).read_bytes()
                metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
                decoded = auto_open_cdf_oracle_pack(
                    payload,
                    metadata,
                    cache_dir=args.cache_dir,
                )
                Path(args.output).write_bytes(decoded)
                print_json(
                    {
                        "ok": True,
                        "action": "cdf-auto-open",
                        "selectedCodec": metadata["selectedCodec"],
                        "selectedProfileId": metadata.get("selectedProfileId"),
                        "outputBytes": len(decoded),
                        "outputDigest": sha256_digest(decoded),
                        "digestMatch": sha256_digest(decoded)
                        == metadata["inputDigest"],
                        "requirements": plan_cdf_open_requirements(metadata),
                    }
                )
                return 0
            parser.error("unknown cdf command")
        except (
            CdfOracleError,
            CdfPublicRegistryError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            print_json({"ok": False, "error": str(exc)})
            return 1
    if args.command == "hydrate":
        print_json(hydrate_file(args.input, args.output, byte_range=args.byte_range, chunk_id=args.chunk))
        return 0
    parser.error("unknown command")
    return 2
