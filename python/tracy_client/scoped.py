# -*- coding: utf-8 -*-

import sys

from functools import wraps
from typing import Callable, Optional, Union

from tracy_client.TracyClientBindings import (
    is_enabled,
    _ScopedZone,
    _zoned_call,
    ColorType,
    frame_mark_start,
    frame_mark_end,
)

Color = Union[int, ColorType]


class ScopedZone(_ScopedZone):
    def __init__(
        self,
        name: Optional[str] = None,
        color: Color = 0,
        depth: Optional[int] = None,
        active: bool = True,
    ) -> None:
        frame = sys._getframe(1)
        _ScopedZone.__init__(
            self,
            name,
            int(color),
            depth,
            active,
            frame.f_code.co_name,
            frame.f_code.co_filename,
            frame.f_lineno,
        )

    def color(self, color: Color) -> None:
        self._color(int(color))

    def __enter__(self):
        self.enter()
        return self

    def __exit__(self, *args):
        self.exit()


def ScopedZoneDecorator(
    name: Optional[str] = None,
    color: Color = 0,
    depth: Optional[int] = None,
    active: bool = True,
    class_name: Optional[str] = None,
):

    def decorator(function: Callable):
        if not is_enabled():
            return function

        source = function.__name__
        if class_name is not None:
            source = f"{class_name}:{source}"

        # Each call gets its own native zone, opened and closed around the
        # call; sharing one zone object between calls would let concurrent or
        # recursive calls overwrite each other's zone. `wrapped` stays a Python
        # function so that @wraps applies and it still binds as a method.
        call = _zoned_call(
            function,
            name,
            int(color),
            depth,
            active,
            source,
            function.__code__.co_filename,
            function.__code__.co_firstlineno,
        )

        @wraps(function)
        def wrapped(*args, **kwargs):
            return call(*args, **kwargs)

        return wrapped

    return decorator


class ScopedFrame:
    def __init__(self, name: str) -> None:
        self.__name = name
        self.__id: Optional[int] = None

    def __enter__(self):
        self.__id = frame_mark_start(self.__name)
        return self

    def __exit__(self, *args):
        if self.__id is None:
            return
        frame_mark_end(self.__id)


def ScopedFrameDecorator(name: str):

    def decorator(function: Callable):
        if not is_enabled():
            return function

        @wraps(function)
        def wrapped(*args, **kwargs):
            frame_id = frame_mark_start(name)
            value = function(*args, **kwargs)
            if frame_id is not None:
                frame_mark_end(frame_id)
            return value

        return wrapped

    return decorator
