from __future__ import annotations

import argparse
import json
from pathlib import Path

from .codec import create_capsule_file, decode_file, encode_file, hydrate_file, inspect_slb1


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


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
    if args.command == "hydrate":
        print_json(hydrate_file(args.input, args.output, byte_range=args.byte_range, chunk_id=args.chunk))
        return 0
    parser.error("unknown command")
    return 2
