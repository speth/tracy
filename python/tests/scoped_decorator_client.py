# -*- coding: utf-8 -*-
"""Profiled client for test_scoped_decorator.py.

Runs as a separate process so it owns a Tracy client listening on the port in
TRACY_PORT. It prints "ready", waits for "go" on stdin (sent once a server has
connected), runs every scenario, prints "done", and then waits for stdin to close.
"""

import sys
import threading

import tracy_client


@tracy_client.ScopedZoneDecorator(name="overlap")
def overlap(entered: threading.Event, release: threading.Event) -> None:
    entered.set()
    release.wait()


@tracy_client.ScopedZoneDecorator(name="recurse")
def recurse(depth: int) -> int:
    if depth == 0:
        return 0
    return 1 + recurse(depth - 1)


@tracy_client.ScopedZoneDecorator(name="raises")
def raises() -> None:
    raise ValueError("expected")


@tracy_client.ScopedZoneDecorator(name="after")
def after() -> None:
    pass


@tracy_client.ScopedZoneDecorator()
def unnamed() -> None:
    pass


def run_overlap() -> None:
    """Two threads inside one decorated function at once.

    First enters before second; first leaves while second is still inside.
    """
    events = {
        key: (threading.Event(), threading.Event()) for key in ("first", "second")
    }
    threads = {
        key: threading.Thread(target=overlap, args=events[key]) for key in events
    }
    threads["first"].start()
    events["first"][0].wait()
    threads["second"].start()
    events["second"][0].wait()
    events["first"][1].set()
    threads["first"].join()
    events["second"][1].set()
    threads["second"].join()


def main() -> None:
    print("ready", flush=True)
    if sys.stdin.readline().strip() != "go":
        return
    run_overlap()
    assert recurse(3) == 3
    try:
        raises()
    except ValueError:
        pass
    after()
    unnamed()
    print("done", flush=True)
    sys.stdin.read()


if __name__ == "__main__":
    main()
