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

from tiny_cache.infrastructure.protobuf import load_generated_protobuf_modules

REQUEST_ID_HEADER = "x-request-id"
NAMESPACE_HEADER = "x-cache-namespace"


def _add_value_input_args(
    parser: argparse.ArgumentParser,
    *,
    positional_name: str,
    positional_help: str,
    option_prefix: str,
) -> None:
    parser.add_argument(positional_name, nargs="?", help=positional_help)
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        f"--{option_prefix}-base64", default=None, help=f"{option_prefix} as base64"
    )
    group.add_argument(
        f"--{option_prefix}-hex", default=None, help=f"{option_prefix} as hex"
    )
    group.add_argument(
        f"--{option_prefix}-file",
        default=None,
        help=f"read raw {option_prefix} bytes from a file",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tiny-cache")
    parser.add_argument(
        "--target", default="127.0.0.1:50051", help="gRPC server address (host:port)"
    )
    parser.add_argument(
        "--timeout", type=float, default=5.0, help="per-RPC timeout in seconds"
    )
    parser.add_argument(
        "--request-id", default=None, help="override request id sent as x-request-id"
    )
    parser.add_argument(
        "--show-request-id",
        action="store_true",
        help="print the server x-request-id response metadata to stderr",
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="optional cache namespace sent as x-cache-namespace metadata",
    )
    parser.add_argument("--tls", action="store_true", help="use TLS when connecting")
    parser.add_argument(
        "--tls-ca", default=None, help="path to PEM-encoded CA bundle (optional)"
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
        "--output", default=None, help="write raw bytes to a file instead of printing"
    )

    set_parser = subparsers.add_parser("set", help="set a value")
    set_parser.add_argument("key")
    _add_value_input_args(
        set_parser,
        positional_name="value",
        positional_help="value as UTF-8 text",
        option_prefix="value",
    )
    set_parser.add_argument(
        "--ttl", type=int, default=0, help="ttl seconds (<= 0 means no ttl)"
    )

    set_if_absent_parser = subparsers.add_parser(
        "set-if-absent", help="set only when the key is missing"
    )
    set_if_absent_parser.add_argument("key")
    _add_value_input_args(
        set_if_absent_parser,
        positional_name="value",
        positional_help="value as UTF-8 text",
        option_prefix="value",
    )
    set_if_absent_parser.add_argument(
        "--ttl", type=int, default=0, help="ttl seconds (<= 0 means no ttl)"
    )

    compare_and_set_parser = subparsers.add_parser(
        "compare-and-set", help="replace a value only when the current value matches"
    )
    compare_and_set_parser.add_argument("key")
    _add_value_input_args(
        compare_and_set_parser,
        positional_name="expected_value",
        positional_help="expected value as UTF-8 text",
        option_prefix="expected-value",
    )
    _add_value_input_args(
        compare_and_set_parser,
        positional_name="value",
        positional_help="new value as UTF-8 text",
        option_prefix="value",
    )
    compare_and_set_parser.add_argument(
        "--ttl", type=int, default=0, help="ttl seconds (<= 0 means no ttl)"
    )

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


def _parse_value_input(
    args: argparse.Namespace, *, attr_prefix: str, command: str
) -> bytes:
    file_attr = f"{attr_prefix}_file"
    base64_attr = f"{attr_prefix}_base64"
    hex_attr = f"{attr_prefix}_hex"

    file_value = getattr(args, file_attr, None)
    if file_value:
        return Path(file_value).read_bytes()

    base64_value = getattr(args, base64_attr, None)
    if base64_value is not None:
        return base64.b64decode(base64_value, validate=True)

    hex_value = getattr(args, hex_attr, None)
    if hex_value is not None:
        return bytes.fromhex(hex_value)

    positional_value = getattr(args, attr_prefix, None)
    if positional_value is not None:
        return positional_value.encode("utf-8")

    raise ValueError(
        f"{command} requires {attr_prefix.replace('_', '-')} "
        f"(positional, --{attr_prefix.replace('_', '-')}-file, "
        f"--{attr_prefix.replace('_', '-')}-base64, or --{attr_prefix.replace('_', '-')}-hex)"
    )


def _validate_value_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    commands = {"set", "set-if-absent", "compare-and-set"}
    if args.command not in commands:
        return

    pairs = [("value", "value")]
    if args.command == "compare-and-set":
        pairs.insert(0, ("expected_value", "expected-value"))

    for attr_prefix, label in pairs:
        positional_value = getattr(args, attr_prefix, None)
        file_value = getattr(args, f"{attr_prefix}_file", None)
        base64_value = getattr(args, f"{attr_prefix}_base64", None)
        hex_value = getattr(args, f"{attr_prefix}_hex", None)
        if positional_value is not None and any([file_value, base64_value, hex_value]):
            parser.error(
                f"{args.command.replace('_', '-')}: positional {label} cannot be combined with --{label}-*"
            )


async def _build_channel(args: argparse.Namespace) -> grpc.aio.Channel:
    if not args.tls:
        return grpc.aio.insecure_channel(args.target)

    root_certificates = None
    if args.tls_ca:
        root_certificates = Path(args.tls_ca).read_bytes()
    credentials = grpc.ssl_channel_credentials(root_certificates=root_certificates)
    return grpc.aio.secure_channel(args.target, credentials)


def _metadata(
    request_id: str, namespace: str | None = None
) -> tuple[tuple[str, str], ...]:
    metadata: list[tuple[str, str]] = [(REQUEST_ID_HEADER, request_id)]
    if namespace:
        metadata.append((NAMESPACE_HEADER, namespace))
    return tuple(metadata)


def _get_response_request_id(metadata: grpc.aio.Metadata) -> str | None:
    for key, value in metadata:
        if key.lower() == REQUEST_ID_HEADER and value:
            return value
    return None


async def _emit_response_request_id(
    args: argparse.Namespace, call: grpc.aio.UnaryUnaryCall
) -> None:
    response_request_id = _get_response_request_id(await call.initial_metadata())
    if args.show_request_id and response_request_id:
        print(f"{REQUEST_ID_HEADER}={response_request_id}", file=sys.stderr)


def _conditional_exit_code(status: int, cache_pb2_module) -> int:
    if status == cache_pb2_module.STORED:
        return 0
    return 1


async def run(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    _validate_value_args(parser, args)

    request_id = args.request_id or uuid.uuid4().hex
    cache_pb2, cache_pb2_grpc = load_generated_protobuf_modules()

    channel = await _build_channel(args)
    try:
        await channel.channel_ready()
        stub = cache_pb2_grpc.CacheServiceStub(channel)
        metadata = _metadata(request_id, args.namespace)

        if args.command == "get":
            call = stub.Get(
                cache_pb2.CacheKey(key=args.key),
                metadata=metadata,
                timeout=args.timeout,
            )
            response = await call
            await _emit_response_request_id(args, call)
            if not response.found:
                return 1
            if args.output:
                Path(args.output).write_bytes(response.value)
                return 0
            print(_encode_output(response.value, args.format))
            return 0

        if args.command == "set":
            value = _parse_value_input(args, attr_prefix="value", command="set")
            call = stub.Set(
                cache_pb2.CacheItem(key=args.key, value=value, ttl=int(args.ttl)),
                metadata=metadata,
                timeout=args.timeout,
            )
            response = await call
            await _emit_response_request_id(args, call)
            if response.status != cache_pb2.CacheStatus.OK:
                print("ERROR", file=sys.stderr)
                return 2
            print("OK")
            return 0

        if args.command == "set-if-absent":
            value = _parse_value_input(
                args, attr_prefix="value", command="set-if-absent"
            )
            call = stub.SetIfAbsent(
                cache_pb2.CacheItem(key=args.key, value=value, ttl=int(args.ttl)),
                metadata=metadata,
                timeout=args.timeout,
            )
            response = await call
            await _emit_response_request_id(args, call)
            status_name = cache_pb2.ConditionalCacheStatus.Name(response.status)
            print(status_name)
            return _conditional_exit_code(response.status, cache_pb2)

        if args.command == "compare-and-set":
            expected_value = _parse_value_input(
                args,
                attr_prefix="expected_value",
                command="compare-and-set",
            )
            value = _parse_value_input(
                args, attr_prefix="value", command="compare-and-set"
            )
            call = stub.CompareAndSet(
                cache_pb2.CompareAndSetRequest(
                    key=args.key,
                    expected_value=expected_value,
                    value=value,
                    ttl=int(args.ttl),
                ),
                metadata=metadata,
                timeout=args.timeout,
            )
            response = await call
            await _emit_response_request_id(args, call)
            status_name = cache_pb2.ConditionalCacheStatus.Name(response.status)
            print(status_name)
            return _conditional_exit_code(response.status, cache_pb2)

        if args.command == "delete":
            call = stub.Delete(
                cache_pb2.CacheKey(key=args.key),
                metadata=metadata,
                timeout=args.timeout,
            )
            response = await call
            await _emit_response_request_id(args, call)
            if response.status != cache_pb2.CacheStatus.OK:
                print("ERROR", file=sys.stderr)
                return 2
            print("OK")
            return 0

        if args.command == "stats":
            call = stub.Stats(
                cache_pb2.Empty(), metadata=metadata, timeout=args.timeout
            )
            response = await call
            await _emit_response_request_id(args, call)
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
