# -*- coding: utf-8 -*-
"""Per-call overhead of ScopedZoneDecorator on a no-op function.

Compares the decorator with the pre-fix implementation, which shared one
_ScopedZone between calls (reproduced below as `shared_zone_decorator`; it is
only correct for calls that neither overlap nor raise, which is all this
benchmark makes). Each measurement runs in a child process that owns its own
Tracy client, once with no profiler connected and once with a live server
connected through TracyServerBindings.Worker.

    python bench_scoped_decorator.py [--calls N] [--repeats R]

Reports the median over the repeats of (decorated - undecorated) ns per call,
each repeat timing the variants back to back.
"""

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from functools import wraps

import tracy_client
from tracy_client import TracyServerBindings
from tracy_client.TracyClientBindings import _ScopedZone


def shared_zone_decorator(function):
    zone = _ScopedZone(
        None,
        0,
        None,
        True,
        function.__name__,
        function.__code__.co_filename,
        function.__code__.co_firstlineno,
    )

    @wraps(function)
    def wrapped(*args, **kwargs):
        zone.enter()
        value = function(*args, **kwargs)
        zone.exit()
        return value

    return wrapped


def noop():
    pass


IMPLEMENTATIONS = {
    "undecorated": noop,
    "shared_zone": shared_zone_decorator(noop),
    "per_call_zone": tracy_client.ScopedZoneDecorator()(noop),
}


def per_call_ns(function, calls):
    start = time.perf_counter_ns()
    for _ in range(calls):
        function()
    return (time.perf_counter_ns() - start) / calls


def child(calls, repeats):
    print("ready", flush=True)
    if sys.stdin.readline().strip() != "go":
        return
    samples = {name: [] for name in IMPLEMENTATIONS}
    for name, function in IMPLEMENTATIONS.items():
        per_call_ns(function, calls // 10)  # warm up
    for _ in range(repeats):
        # Interleave so drift in machine load affects every variant alike.
        for name, function in IMPLEMENTATIONS.items():
            samples[name].append(per_call_ns(function, calls))
    print(json.dumps(samples), flush=True)
    sys.stdin.read()


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def measure(connected, calls, repeats):
    port = free_port()
    env = dict(os.environ, TRACY_PORT=str(port))
    process = subprocess.Popen(
        [
            sys.executable,
            __file__,
            "--child",
            f"--calls={calls}",
            f"--repeats={repeats}",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env,
        text=True,
    )
    worker = None
    try:
        assert process.stdout.readline().strip() == "ready"
        if connected:
            worker = TracyServerBindings.Worker("127.0.0.1", port)
            deadline = time.monotonic() + 20
            while worker.get_handshake_status() != 1:
                if time.monotonic() > deadline:
                    raise RuntimeError("profiler did not connect")
                time.sleep(0.05)
        process.stdin.write("go\n")
        process.stdin.flush()
        return json.loads(process.stdout.readline())
    finally:
        if worker is not None:
            worker.disconnect()
        process.stdin.close()
        process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=200_000)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    if args.child:
        child(args.calls, args.repeats)
        return
    if not tracy_client.is_enabled():
        sys.exit("tracy_client was built without TRACY_ENABLE")
    print(f"{args.calls} calls x {args.repeats} repeats; overhead in ns per call")
    print(f"{'profiler':<14}{'shared_zone':>14}{'per_call_zone':>16}")
    for connected in (False, True):
        samples = measure(connected, args.calls, args.repeats)
        row = [
            statistics.median(
                decorated - plain
                for decorated, plain in zip(samples[name], samples["undecorated"])
            )
            for name in ("shared_zone", "per_call_zone")
        ]
        label = "connected" if connected else "disconnected"
        print(f"{label:<14}{row[0]:>14.1f}{row[1]:>16.1f}")


if __name__ == "__main__":
    main()
