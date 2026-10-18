# -*- coding: utf-8 -*-
"""Tests for ScopedZoneDecorator.

The zone tests run scoped_decorator_client.py in a child process with a live
Tracy server (TracyServerBindings.Worker) connected to it, then read back the
zones the server received. The package must be built with TRACY_ENABLE=ON.
"""

import functools
import itertools
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import tracy_client
from tracy_client import TracyServerBindings

CLIENT = Path(__file__).with_name("scoped_decorator_client.py")
TIMEOUT = 20.0

pytestmark = pytest.mark.skipif(
    not tracy_client.is_enabled(), reason="tracy_client built without TRACY_ENABLE"
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, what: str):
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


EXPECTED_COUNTS = {"overlap": 2, "recurse": 4, "raises": 1, "after": 1, "unnamed": 1}


@pytest.fixture(scope="module")
def capture():
    """Closed zones per name, as (start, end, thread) tuples, and the source
    location the server holds for each zone name.

    Threads are compared only with each other: the server bindings report a
    thread index, not the OS thread id.
    """
    port = free_port()
    env = dict(os.environ, TRACY_PORT=str(port))
    env.pop("TRACY_NO_EXIT", None)
    child = subprocess.Popen(
        [sys.executable, str(CLIENT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env,
        text=True,
    )
    worker = None
    try:
        assert child.stdout.readline().strip() == "ready"
        worker = TracyServerBindings.Worker("127.0.0.1", port)
        wait_for(lambda: worker.get_handshake_status() == 1, "the handshake")
        child.stdin.write("go\n")
        child.stdin.flush()
        assert child.stdout.readline().strip() == "done"

        def closed_zones():
            return {
                name: [
                    (start, start + duration, thread)
                    for start, duration, thread in worker.get_zone_occurrences_with_thread(
                        name, 1000
                    )
                ]
                for name in EXPECTED_COUNTS
            }

        def counts(zones):
            return {name: len(found) for name, found in zones.items()}

        # A zone that never closes never arrives, so on a timeout the tests
        # run against what did arrive and report the shortfall themselves.
        deadline = time.monotonic() + TIMEOUT
        zones = closed_zones()
        while counts(zones) != EXPECTED_COUNTS and time.monotonic() < deadline:
            time.sleep(0.05)
            zones = closed_zones()
        assert worker.get_failure_type() == 0, worker.get_failure_data()
        yield zones, worker.get_all_zone_source_locations()
    finally:
        if worker is not None:
            worker.disconnect()
        child.stdin.close()
        try:
            child.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def test_overlapping_calls_on_two_threads_get_their_own_zones(capture):
    capture, _ = capture
    assert len(capture["overlap"]) == 2, capture["overlap"]
    first, second = sorted(capture["overlap"])
    assert first[2] != second[2]
    # The first call entered, then the second, then the first left.
    assert first[0] < second[0] < first[1] < second[1]


def test_recursive_calls_nest(capture):
    capture, _ = capture
    nested = sorted(capture["recurse"])
    assert len(nested) == 4, nested
    assert len({thread for _, _, thread in nested}) == 1
    for (outer_start, outer_end, _), (inner_start, inner_end, _) in itertools.pairwise(
        nested
    ):
        assert outer_start <= inner_start <= inner_end <= outer_end


def test_a_raising_call_closes_its_zone(capture):
    capture, _ = capture
    assert len(capture["raises"]) == 1, capture
    [(_, raise_end, raise_thread)] = capture["raises"]
    [(after_start, _, after_thread)] = capture["after"]
    assert raise_thread == after_thread
    assert raise_end <= after_start


def test_zones_carry_the_decorated_functions_source_location(capture):
    _, locations = capture
    # A zone given no name is named after its function.
    for name, function in (("after", "after"), ("unnamed", "unnamed")):
        location = locations[name]
        assert location["function"] == function
        assert Path(location["file"]).name == CLIENT.name
        assert location["line"] > 0
    assert locations["after"]["line"] < locations["unnamed"]["line"]


def test_return_value_and_exception_pass_through():
    @tracy_client.ScopedZoneDecorator()
    def echo(*args, **kwargs):
        return args, kwargs

    assert echo(1, 2, key="value") == ((1, 2), {"key": "value"})

    error = KeyError("mine")

    @tracy_client.ScopedZoneDecorator()
    def fail():
        raise error

    with pytest.raises(KeyError) as raised:
        fail()
    assert raised.value is error


def test_metadata_and_method_binding_are_preserved():
    def documented(self, value):
        """Doc string."""
        return self, value

    wrapped = tracy_client.ScopedZoneDecorator(class_name="Holder")(documented)
    for attribute in functools.WRAPPER_ASSIGNMENTS:
        assert getattr(wrapped, attribute) == getattr(documented, attribute)
    assert wrapped.__wrapped__ is documented

    class Holder:
        method = wrapped

    holder = Holder()
    assert holder.method(5) == (holder, 5)
