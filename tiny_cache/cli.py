from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import uuid
from pathlib import Path
from typing import Sequence

import grpc

import cache_pb2
import cache_pb2_grpc

REQUEST_ID_HEADER = "x-request-id"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tiny-cache")
    parser.add_argument(
        "--target",
        default="127.0.0.1:50051",
        help="gRPC server address (host:port)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="per-RPC timeout in seconds",
    )
    parser.add_argument(
        "--request-id",
        default=None,
        help="override request id sent as x-request-id",
    )
    parser.add_argument(
        "--show-request-id",
        action="store_true",
        help="print the server x-request-id response metadata to stderr",
    )
    parser.add_argument(
        "--tls",
        action="store_true",
        help="use TLS when connecting",
    )
    parser.add_argument(
        "--tls-ca",
        default=None,
        help="path to PEM-encoded CA bundle (optional)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    get_parser = subparsers.add_parser("get", help="get a value")
    get_parser.add_argument("key")
    get_parser.add_argument(
        "--format",
        choices=("base64", "hex", "utf8"),
        default="base64",
        help="output encoding for bytes when printing to stdout",
    )
    get_parser.add_argument(
        "--output",
        default=None,
        help="write raw bytes to a file instead of printing",
    )

    set_parser = subparsers.add_parser("set", help="set a value")
    set_parser.add_argument("key")
    set_parser.add_argument("value", nargs="?", help="value as UTF-8 text")
    set_parser.add_argument("--ttl", type=int, default=0, help="ttl seconds (<= 0 means no ttl)")
    set_group = set_parser.add_mutually_exclusive_group(required=False)
    set_group.add_argument("--value-base64", default=None, help="value as base64")
    set_group.add_argument("--value-hex", default=None, help="value as hex")
    set_group.add_argument("--value-file", default=None, help="read raw bytes from a file")

    delete_parser = subparsers.add_parser("delete", help="delete a key")
    delete_parser.add_argument("key")

    subparsers.add_parser("stats", help="print cache statistics as json")

    return parser


def _encode_output(value: bytes, fmt: str) -> str:
    if fmt == "base64":
        return base64.b64encode(value).decode("ascii")
    if fmt == "hex":
        return value.hex()
    if fmt == "utf8":
        return value.decode("utf-8")
    raise ValueError(f"unknown format: {fmt}")


def _parse_set_value(args: argparse.Namespace) -> bytes:
    if args.value_file:
        return Path(args.value_file).read_bytes()
    if args.value_base64 is not None:
        return base64.b64decode(args.value_base64, validate=True)
    if args.value_hex is not None:
        return bytes.fromhex(args.value_hex)
    if args.value is not None:
        return args.value.encode("utf-8")
    raise ValueError("set requires a value (positional, --value-file, --value-base64, or --value-hex)")


def _validate_set_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.command != "set":
        return
    if args.value is not None and any([args.value_file, args.value_base64, args.value_hex]):
        parser.error("set: positional value cannot be combined with --value-*")


async def _build_channel(args: argparse.Namespace) -> grpc.aio.Channel:
    if not args.tls:
        return grpc.aio.insecure_channel(args.target)

    root_certificates = None
    if args.tls_ca:
        root_certificates = Path(args.tls_ca).read_bytes()
    credentials = grpc.ssl_channel_credentials(root_certificates=root_certificates)
    return grpc.aio.secure_channel(args.target, credentials)


def _metadata(request_id: str) -> tuple[tuple[str, str]]:
    return ((REQUEST_ID_HEADER, request_id),)


def _get_response_request_id(metadata: grpc.aio.Metadata) -> str | None:
    for key, value in metadata:
        if key.lower() == REQUEST_ID_HEADER and value:
            return value
    return None


async def run(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    _validate_set_args(parser, args)

    request_id = args.request_id or uuid.uuid4().hex

    channel = await _build_channel(args)
    try:
        await channel.channel_ready()
        stub = cache_pb2_grpc.CacheServiceStub(channel)

        if args.command == "get":
            call = stub.Get(
                cache_pb2.CacheKey(key=args.key),
                metadata=_metadata(request_id),
                timeout=args.timeout,
            )
            response = await call
            response_request_id = _get_response_request_id(await call.initial_metadata())
            if args.show_request_id and response_request_id:
                print(f"{REQUEST_ID_HEADER}={response_request_id}", file=sys.stderr)

            if not response.found:
                return 1

            if args.output:
                Path(args.output).write_bytes(response.value)
                return 0

            print(_encode_output(response.value, args.format))
            return 0

        if args.command == "set":
            value = _parse_set_value(args)
            call = stub.Set(
                cache_pb2.CacheItem(key=args.key, value=value, ttl=int(args.ttl)),
                metadata=_metadata(request_id),
                timeout=args.timeout,
            )
            response = await call
            response_request_id = _get_response_request_id(await call.initial_metadata())
            if args.show_request_id and response_request_id:
                print(f"{REQUEST_ID_HEADER}={response_request_id}", file=sys.stderr)

            if response.status != cache_pb2.CacheStatus.OK:
                print("ERROR", file=sys.stderr)
                return 2

            print("OK")
            return 0

        if args.command == "delete":
            call = stub.Delete(
                cache_pb2.CacheKey(key=args.key),
                metadata=_metadata(request_id),
                timeout=args.timeout,
            )
            response = await call
            response_request_id = _get_response_request_id(await call.initial_metadata())
            if args.show_request_id and response_request_id:
                print(f"{REQUEST_ID_HEADER}={response_request_id}", file=sys.stderr)

            if response.status != cache_pb2.CacheStatus.OK:
                print("ERROR", file=sys.stderr)
                return 2

            print("OK")
            return 0

        if args.command == "stats":
            call = stub.Stats(
                cache_pb2.Empty(),
                metadata=_metadata(request_id),
                timeout=args.timeout,
            )
            response = await call
            response_request_id = _get_response_request_id(await call.initial_metadata())
            if args.show_request_id and response_request_id:
                print(f"{REQUEST_ID_HEADER}={response_request_id}", file=sys.stderr)

            payload = {
                "size": response.size,
                "hits": response.hits,
                "misses": response.misses,
                "evictions": response.evictions,
                "hit_rate": response.hit_rate,
                "memory_usage_bytes": response.memory_usage_bytes,
                "max_memory_bytes": response.max_memory_bytes,
                "max_items": response.max_items,
            }
            print(json.dumps(payload))
            return 0

        parser.error(f"unknown command: {args.command}")
        return 2
    except grpc.aio.AioRpcError as exc:
        print(f"rpc failed: {exc.code().name}: {exc.details()}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await channel.close()


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()

