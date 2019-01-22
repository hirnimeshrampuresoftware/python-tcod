"""
An alternative, more direct implementation of event handling using cffi
calls to SDL functions.  The current code is incomplete, but can be
extended easily by following the official SDL documentation.

This module be run directly like any other event get example, but is meant
to be copied into your code base.  Then you can use the tcod.event.get and
tcod.event.wait functions in your code.

Printing any event will tell you its attributes in a human readable format.
An events type attr is just the classes name with all letters upper-case.

Like in tdl, events use a type attribute to tell events apart.  Unlike tdl
and tcod the names and values used are directly derived from SDL.

As a general guideline for turn-based rouge-likes, you should use
KeyDown.sym for commands, and TextInput.text for name entry fields.
"""
import collections
from typing import Any, Dict, Optional, Iterator, Tuple

import tcod
import tcod.event_constants
from tcod.event_constants import *


def _describe_bitmask(
    bits: int, table: Dict[Any, str], default: Any = "0"
) -> str:
    """Returns a bitmask in human readable form.

    This is a private function, used internally.

    Args:
        bits (int): The bitmask to be represented.
        table (Dict[Any,str]): A reverse lookup table.
        default (Any): A default return value when bits is 0.

    Returns: str: A printable version of the bits variable.
    """
    result = []
    for bit, name in table.items():
        if bit & bits:
            result.append(name)
    if not result:
        return default
    return "|".join(result)


def _pixel_to_tile(x: float, y: float) -> Tuple[float, float]:
    """Convert pixel coordinates to tile coordinates."""
    xy = tcod.ffi.new("double[2]", (x, y))
    tcod.lib.TCOD_sys_pixel_to_tile(xy, xy + 1)
    return xy[0], xy[1]


Point = collections.namedtuple("Point", ["x", "y"])

# manually define names for SDL macros
BUTTON_LEFT = 1
BUTTON_MIDDLE = 2
BUTTON_RIGHT = 3
BUTTON_X1 = 4
BUTTON_X2 = 5
BUTTON_LMASK = 0x01
BUTTON_MMASK = 0x02
BUTTON_RMASK = 0x04
BUTTON_X1MASK = 0x08
BUTTON_X2MASK = 0x10

# reverse tables are used to get the tcod.event name from the value.
_REVERSE_BUTTON_TABLE = {
    BUTTON_LEFT: "tcod.event.BUTTON_LEFT",
    BUTTON_MIDDLE: "tcod.event.BUTTON_MIDDLE",
    BUTTON_RIGHT: "tcod.event.BUTTON_RIGHT",
    BUTTON_X1: "tcod.event.BUTTON_X1",
    BUTTON_X2: "tcod.event.BUTTON_X2",
}

_REVERSE_BUTTON_MASK_TABLE = {
    BUTTON_LMASK: "tcod.event.BUTTON_LMASK",
    BUTTON_MMASK: "tcod.event.BUTTON_MMASK",
    BUTTON_RMASK: "tcod.event.BUTTON_RMASK",
    BUTTON_X1MASK: "tcod.event.BUTTON_X1MASK",
    BUTTON_X2MASK: "tcod.event.BUTTON_X2MASK",
}


class Event:
    """The base event class."""

    @classmethod
    def from_sdl_event(cls, sdl_event):
        """Return a class instance from a cffi SDL_Event pointer."""
        raise NotImplementedError()

    @property
    def type(self):
        """All event types are just the class name, but all upper-case."""
        return self.__class__.__name__.upper()


class Quit(Event):
    """An application quit request event.

    For more info on when this event is triggered see:
    https://wiki.libsdl.org/SDL_EventType#SDL_QUIT
    """

    @classmethod
    def from_sdl_event(cls, sdl_event):
        return cls()

    def __repr__(self):
        return "tcod.event.%s()" % self.__class__.__name__


class KeyboardEvent(Event):
    def __init__(
        self, scancode: int, sym: int, mod: int, repeat: bool = False
    ):
        self.scancode = scancode
        self.sym = sym
        self.mod = mod
        self.repeat = repeat

    @classmethod
    def from_sdl_event(cls, sdl_event):
        keysym = sdl_event.key.keysym
        return cls(
            keysym.scancode, keysym.sym, keysym.mod, bool(sdl_event.key.repeat)
        )

    def __repr__(self):
        return "tcod.event.%s(scancode=%s, sym=%s, mod=%s%s)" % (
            self.__class__.__name__,
            tcod.event_constants._REVERSE_SCANCODE_TABLE[self.scancode],
            tcod.event_constants._REVERSE_SYM_TABLE[self.sym],
            _describe_bitmask(
                self.mod, tcod.event_constants._REVERSE_MOD_TABLE
            ),
            ", repeat=True" if self.repeat else "",
        )


class KeyDown(KeyboardEvent):
    pass


class KeyUp(KeyboardEvent):
    pass


class MouseMotion(Event):
    def __init__(
        self,
        pixel: Tuple[int, int],
        pixel_motion: Tuple[int, int],
        tile: Tuple[int, int],
        tile_motion: Tuple[int, int],
        state: int,
    ):
        self.pixel = Point(*pixel)
        self.pixel_motion = Point(*pixel_motion)
        self.tile = Point(*tile)
        self.tile_motion = Point(*tile_motion)
        self.state = state

    @classmethod
    def from_sdl_event(cls, sdl_event):
        motion = sdl_event.motion

        pixel = motion.x, motion.y
        pixel_motion = motion.xrel, motion.yrel
        subtile = _pixel_to_tile(*pixel)
        tile = int(subtile[0]), int(subtile[1])
        prev_pixel = pixel[0] - pixel_motion[0], pixel[1] - pixel_motion[1]
        prev_subtile = _pixel_to_tile(*prev_pixel)
        prev_tile = int(prev_subtile[0]), int(prev_subtile[1])
        tile_motion = tile[0] - prev_tile[0], tile[1] - prev_tile[1]
        return cls(pixel, pixel_motion, tile, tile_motion, motion.state)

    def __repr__(self):
        return (
            "tcod.event.%s(pixel=%r, pixel_motion=%r, "
            "tile=%r, tile_motion=%r, state=%s)"
        ) % (
            self.__class__.__name__,
            self.pixel,
            self.pixel_motion,
            self.tile,
            self.tile_motion,
            _describe_bitmask(self.state, _REVERSE_BUTTON_MASK_TABLE),
        )


class MouseButtonEvent(Event):
    def __init__(
        self, pixel: Tuple[int, int], tile: Tuple[int, int], button: int
    ):
        self.pixel = Point(*pixel)
        self.tile = Point(*tile)
        self.button = button

    @classmethod
    def from_sdl_event(cls, sdl_event):
        button = sdl_event.button
        pixel = button.x, button.y
        subtile = _pixel_to_tile(*pixel)
        tile = int(subtile[0]), int(subtile[1])
        return cls(pixel, tile, button.button)

    def __repr__(self):
        return "tcod.event.%s(pixel=%r, tile=%r, button=%s)" % (
            self.__class__.__name__,
            self.pixel,
            self.tile,
            _REVERSE_BUTTON_TABLE[self.button],
        )


class MouseButtonDown(MouseButtonEvent):
    pass


class MouseButtonUp(MouseButtonEvent):
    pass


class MouseWheel(Event):
    def __init__(self, x: int, y: int, flipped: bool = False):
        self.x = x
        self.y = y
        self.flipped = flipped

    @classmethod
    def from_sdl_event(cls, sdl_event):
        wheel = sdl_event.wheel
        return cls(wheel.x, wheel.y, wheel.direction)

    def __repr__(self):
        return "tcod.event.%s(x=%i, y=%i%s)" % (
            self.__class__.__name__,
            self.x,
            self.y,
            ", flipped=True" if self.flipped else "",
        )


class TextInput(Event):
    def __init__(self, text: str):
        self.text = text

    @classmethod
    def from_sdl_event(cls, sdl_event):
        return cls(tcod.ffi.string(sdl_event.text.text, 32).decode("utf8"))

    def __repr__(self):
        return "tcod.event.%s(text=%r)" % (self.__class__.__name__, self.text)


_SDL_TO_CLASS_TABLE = {
    tcod.lib.SDL_QUIT: Quit,
    tcod.lib.SDL_KEYDOWN: KeyDown,
    tcod.lib.SDL_KEYUP: KeyUp,
    tcod.lib.SDL_MOUSEMOTION: MouseMotion,
    tcod.lib.SDL_MOUSEBUTTONDOWN: MouseButtonDown,
    tcod.lib.SDL_MOUSEBUTTONUP: MouseButtonUp,
    tcod.lib.SDL_MOUSEWHEEL: MouseWheel,
    tcod.lib.SDL_TEXTINPUT: TextInput,
}


def get() -> Iterator[Any]:
    """Iterate over all pending events.

    Returns:
        Iterator[tcod.event.Event]:
            An iterator of Event subclasses.
    """
    sdl_event = tcod.ffi.new("SDL_Event*")
    while tcod.lib.SDL_PollEvent(sdl_event):
        if sdl_event.type in _SDL_TO_CLASS_TABLE:
            yield _SDL_TO_CLASS_TABLE[sdl_event.type].from_sdl_event(sdl_event)


def wait(timeout: Optional[float] = None) -> Iterator[Any]:
    """Block until events exist, then iterate over all events.

    Keep in mind that this function will wake even for events not handled by
    this module.

    Args:
        timeout (Optional[float]):
            Maximum number of seconds to wait, or None to wait forever.
            Has millisecond percision.

    Returns:
        Iterator[tcod.event.Event]: Same iterator as a call to tcod.event.get
    """
    if timeout is not None:
        tcod.lib.SDL_WaitEventTimeout(tcod.ffi.NULL, int(timeout * 1000))
    else:
        tcod.lib.SDL_WaitEvent(tcod.ffi.NULL)
    return get()


__all__ = [
    "Point",
    "Event",
    "Quit",
    "KeynoardEvent",
    "KeyDown",
    "KeyUp",
    "MouseMotion",
    "MouseButtonEvent",
    "MouseButtonDown",
    "MouseButtonUp",
    "MouseWheel",
    "TextInput",
    "get",
    "wait",
] + tcod.event_constants.__all__
