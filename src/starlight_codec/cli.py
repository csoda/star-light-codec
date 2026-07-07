from __future__ import annotations

import argparse
import json
from pathlib import Path

from .codec import decode_file, encode_file, inspect_slb1


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slc", description="Star Light Codec reference CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    encode = sub.add_parser("encode", help="encode a file into an SLB1 artifact")
    encode.add_argument("input")
    encode.add_argument("output")
    encode.add_argument("--max-passes", type=int, default=1)

    decode = sub.add_parser("decode", help="decode an SLB1 artifact exactly")
    decode.add_argument("input")
    decode.add_argument("output")

    inspect = sub.add_parser("inspect", help="inspect an SLB1 artifact without decoding raw bytes")
    inspect.add_argument("input")

    args = parser.parse_args(argv)
    if args.command == "encode":
        print_json(encode_file(args.input, args.output, max_passes=args.max_passes))
        return 0
    if args.command == "decode":
        print_json(decode_file(args.input, args.output))
        return 0
    if args.command == "inspect":
        print_json(inspect_slb1(Path(args.input).read_bytes()))
        return 0
    parser.error("unknown command")
    return 2
