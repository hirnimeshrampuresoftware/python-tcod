"""

Example::

    >>> import numpy as np
    >>> import tcod
    >>> dungeon = np.array(
    ...     [
    ...         [1, 0, 1, 1, 1],
    ...         [1, 0, 1, 0, 1],
    ...         [1, 1, 1, 0, 1],
    ...     ],
    ...     dtype=np.int8,
    ...     )
    ...

    # Create a pathfinder from a numpy array.
    # This is the recommended way to use the tcod.path module.
    >>> astar = tcod.path.AStar(dungeon)
    >>> print(astar.get_path(0, 0, 2, 4))
    [(1, 0), (2, 1), (1, 2), (0, 3), (1, 4), (2, 4)]
    >>> astar.cost[0, 1] = 1 # You can access the map array via this attribute.
    >>> print(astar.get_path(0, 0, 2, 4))
    [(0, 1), (0, 2), (0, 3), (1, 4), (2, 4)]

    # Create a pathfinder from an edge_cost function.
    # Calling Python functions from C is known to be very slow.
    >>> def edge_cost(my_x, my_y, dest_x, dest_y):
    ...     return dungeon[dest_x, dest_y]
    ...
    >>> dijkstra = tcod.path.Dijkstra(
    ...     tcod.path.EdgeCostCallback(edge_cost, dungeon.shape),
    ...     )
    ...
    >>> dijkstra.set_goal(0, 0)
    >>> print(dijkstra.get_path(2, 4))
    [(0, 1), (0, 2), (0, 3), (1, 4), (2, 4)]

.. versionchanged:: 5.0
    All path-finding functions now respect the NumPy array shape (if a NumPy
    array is used.)
"""
import functools
import itertools
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from tcod.loader import lib, ffi
from tcod._internal import _check
import tcod.map  # noqa: F401


@ffi.def_extern()  # type: ignore
def _pycall_path_old(x1: int, y1: int, x2: int, y2: int, handle: Any) -> float:
    """libtcodpy style callback, needs to preserve the old userData issue."""
    func, userData = ffi.from_handle(handle)
    return func(x1, y1, x2, y2, userData)  # type: ignore


@ffi.def_extern()  # type: ignore
def _pycall_path_simple(
    x1: int, y1: int, x2: int, y2: int, handle: Any
) -> float:
    """Does less and should run faster, just calls the handle function."""
    return ffi.from_handle(handle)(x1, y1, x2, y2)  # type: ignore


@ffi.def_extern()  # type: ignore
def _pycall_path_swap_src_dest(
    x1: int, y1: int, x2: int, y2: int, handle: Any
) -> float:
    """A TDL function dest comes first to match up with a dest only call."""
    return ffi.from_handle(handle)(x2, y2, x1, y1)  # type: ignore


@ffi.def_extern()  # type: ignore
def _pycall_path_dest_only(
    x1: int, y1: int, x2: int, y2: int, handle: Any
) -> float:
    """A TDL function which samples the dest coordinate only."""
    return ffi.from_handle(handle)(x2, y2)  # type: ignore


def _get_pathcost_func(
    name: str,
) -> Callable[[int, int, int, int, Any], float]:
    """Return a properly cast PathCostArray callback."""
    return ffi.cast(  # type: ignore
        "TCOD_path_func_t", ffi.addressof(lib, name)
    )


class _EdgeCostFunc(object):
    """Generic edge-cost function factory.

    `userdata` is the custom userdata to send to the C call.

    `shape` is the maximum boundary for the algorithm.
    """

    _CALLBACK_P = lib._pycall_path_old

    def __init__(self, userdata: Any, shape: Tuple[int, int]) -> None:
        self._userdata = userdata
        self.shape = shape

    def get_tcod_path_ffi(self) -> Tuple[Any, Any, Tuple[int, int]]:
        """Return (C callback, userdata handle, shape)"""
        return self._CALLBACK_P, ffi.new_handle(self._userdata), self.shape

    def __repr__(self) -> str:
        return "%s(%r, shape=%r)" % (
            self.__class__.__name__,
            self._userdata,
            self.shape,
        )


class EdgeCostCallback(_EdgeCostFunc):
    """Calculate cost from an edge-cost callback.

    `callback` is the custom userdata to send to the C call.

    `shape` is a 2-item tuple representing the maximum boundary for the
    algorithm.  The callback will not be called with parameters outside of
    these bounds.

    .. versionchanged:: 5.0
        Now only accepts a `shape` argument instead of `width` and `height`.
    """

    _CALLBACK_P = lib._pycall_path_simple

    def __init__(
        self,
        callback: Callable[[int, int, int, int], float],
        shape: Tuple[int, int],
    ):
        self.callback = callback
        super(EdgeCostCallback, self).__init__(callback, shape)


class NodeCostArray(np.ndarray):  # type: ignore
    """Calculate cost from a numpy array of nodes.

    `array` is a NumPy array holding the path-cost of each node.
    A cost of 0 means the node is blocking.
    """

    _C_ARRAY_CALLBACKS = {
        np.float32: ("float*", _get_pathcost_func("PathCostArrayFloat32")),
        np.bool_: ("int8_t*", _get_pathcost_func("PathCostArrayInt8")),
        np.int8: ("int8_t*", _get_pathcost_func("PathCostArrayInt8")),
        np.uint8: ("uint8_t*", _get_pathcost_func("PathCostArrayUInt8")),
        np.int16: ("int16_t*", _get_pathcost_func("PathCostArrayInt16")),
        np.uint16: ("uint16_t*", _get_pathcost_func("PathCostArrayUInt16")),
        np.int32: ("int32_t*", _get_pathcost_func("PathCostArrayInt32")),
        np.uint32: ("uint32_t*", _get_pathcost_func("PathCostArrayUInt32")),
    }

    def __new__(cls, array: np.ndarray) -> "NodeCostArray":
        """Validate a numpy array and setup a C callback."""
        self = np.asarray(array).view(cls)
        return self  # type: ignore

    def __repr__(self) -> str:
        return "%s(%r)" % (
            self.__class__.__name__,
            repr(self.view(np.ndarray)),
        )

    def get_tcod_path_ffi(self) -> Tuple[Any, Any, Tuple[int, int]]:
        if len(self.shape) != 2:
            raise ValueError(
                "Array must have a 2d shape, shape is %r" % (self.shape,)
            )
        if self.dtype.type not in self._C_ARRAY_CALLBACKS:
            raise ValueError(
                "dtype must be one of %r, dtype is %r"
                % (self._C_ARRAY_CALLBACKS.keys(), self.dtype.type)
            )

        array_type, callback = self._C_ARRAY_CALLBACKS[self.dtype.type]
        userdata = ffi.new(
            "struct PathCostArray*",
            (ffi.cast("char*", self.ctypes.data), self.strides),
        )
        return callback, userdata, self.shape


class _PathFinder(object):
    """A class sharing methods used by AStar and Dijkstra."""

    def __init__(self, cost: Any, diagonal: float = 1.41):
        self.cost = cost
        self.diagonal = diagonal
        self._path_c = None  # type: Any
        self._callback = self._userdata = None

        if hasattr(self.cost, "map_c"):
            self.shape = self.cost.width, self.cost.height
            self._path_c = ffi.gc(
                self._path_new_using_map(self.cost.map_c, diagonal),
                self._path_delete,
            )
            return

        if not hasattr(self.cost, "get_tcod_path_ffi"):
            assert not callable(self.cost), (
                "Any callback alone is missing shape information. "
                "Wrap your callback in tcod.path.EdgeCostCallback"
            )
            self.cost = NodeCostArray(self.cost)

        (
            self._callback,
            self._userdata,
            self.shape,
        ) = self.cost.get_tcod_path_ffi()
        self._path_c = ffi.gc(
            self._path_new_using_function(
                self.cost.shape[0],
                self.cost.shape[1],
                self._callback,
                self._userdata,
                diagonal,
            ),
            self._path_delete,
        )

    def __repr__(self) -> str:
        return "%s(cost=%r, diagonal=%r)" % (
            self.__class__.__name__,
            self.cost,
            self.diagonal,
        )

    def __getstate__(self) -> Any:
        state = self.__dict__.copy()
        del state["_path_c"]
        del state["shape"]
        del state["_callback"]
        del state["_userdata"]
        return state

    def __setstate__(self, state: Any) -> None:
        self.__dict__.update(state)
        self.__init__(self.cost, self.diagonal)  # type: ignore

    _path_new_using_map = lib.TCOD_path_new_using_map
    _path_new_using_function = lib.TCOD_path_new_using_function
    _path_delete = lib.TCOD_path_delete


class AStar(_PathFinder):
    """
    Args:
        cost (Union[tcod.map.Map, numpy.ndarray, Any]):
        diagonal (float): Multiplier for diagonal movement.
            A value of 0 will disable diagonal movement entirely.
    """

    def get_path(
        self, start_x: int, start_y: int, goal_x: int, goal_y: int
    ) -> List[Tuple[int, int]]:
        """Return a list of (x, y) steps to reach the goal point, if possible.

        Args:
            start_x (int): Starting X position.
            start_y (int): Starting Y position.
            goal_x (int): Destination X position.
            goal_y (int): Destination Y position.
        Returns:
            List[Tuple[int, int]]:
                A list of points, or an empty list if there is no valid path.
        """
        lib.TCOD_path_compute(self._path_c, start_x, start_y, goal_x, goal_y)
        path = []
        x = ffi.new("int[2]")
        y = x + 1
        while lib.TCOD_path_walk(self._path_c, x, y, False):
            path.append((x[0], y[0]))
        return path


class Dijkstra(_PathFinder):
    """
    Args:
        cost (Union[tcod.map.Map, numpy.ndarray, Any]):
        diagonal (float): Multiplier for diagonal movement.
            A value of 0 will disable diagonal movement entirely.
    """

    _path_new_using_map = lib.TCOD_dijkstra_new
    _path_new_using_function = lib.TCOD_dijkstra_new_using_function
    _path_delete = lib.TCOD_dijkstra_delete

    def set_goal(self, x: int, y: int) -> None:
        """Set the goal point and recompute the Dijkstra path-finder.
        """
        lib.TCOD_dijkstra_compute(self._path_c, x, y)

    def get_path(self, x: int, y: int) -> List[Tuple[int, int]]:
        """Return a list of (x, y) steps to reach the goal point, if possible.
        """
        lib.TCOD_dijkstra_path_set(self._path_c, x, y)
        path = []
        pointer_x = ffi.new("int[2]")
        pointer_y = pointer_x + 1
        while lib.TCOD_dijkstra_path_walk(self._path_c, pointer_x, pointer_y):
            path.append((pointer_x[0], pointer_y[0]))
        return path


_INT_TYPES = {
    np.int8: lib.np_int8,
    np.int16: lib.np_int16,
    np.int32: lib.np_int32,
    np.intc: lib.np_int32,
    np.int64: lib.np_int64,
    np.uint8: lib.np_uint8,
    np.uint16: lib.np_uint16,
    np.uint32: lib.np_uint32,
    np.uint64: lib.np_uint64,
}


def maxarray(
    shape: Tuple[int, ...], dtype: Any = np.int32, order: str = "C"
) -> np.array:
    """Return a new array filled with the maximum finite value for `dtype`.

    `shape` is of the new array.  Same as other NumPy array initializers.

    `dtype` should be a single NumPy integer type.

    `order` can be "C" or "F".

    This works the same as
    ``np.full(shape, np.iinfo(dtype).max, dtype, order)``.

    This kind of array is an ideal starting point for distance maps.  Just set
    any point to a lower value such as 0 and then pass this array to a
    function such as :any:`dijkstra2d`.
    """
    return np.full(shape, np.iinfo(dtype).max, dtype, order)


def _export_dict(array: np.array) -> Dict[str, Any]:
    """Convert a NumPy array into a format compatible with CFFI."""
    return {
        "type": _INT_TYPES[array.dtype.type],
        "ndim": array.ndim,
        "data": ffi.cast("void*", array.ctypes.data),
        "shape": array.shape,
        "strides": array.strides,
    }


def _export(array: np.array) -> Any:
    """Convert a NumPy array into a ctype object."""
    return ffi.new("struct NArray*", _export_dict(array))


def _compile_cost_edges(edge_map: Any) -> Tuple[Any, int]:
    """Return an edge_cost array using an integer map."""
    edge_map = np.copy(edge_map)
    if edge_map.ndim != 2:
        raise ValueError(
            "edge_map must be 2 dimensional. (Got %i)" % edge_map.ndim
        )
    edge_center = edge_map.shape[0] // 2, edge_map.shape[1] // 2
    edge_map[edge_center] = 0
    edge_map[edge_map < 0] = 0
    edge_nz = edge_map.nonzero()
    edge_array = np.transpose(edge_nz)
    edge_array -= edge_center
    c_edges = ffi.new("int[]", len(edge_array) * 3)
    edges = np.frombuffer(ffi.buffer(c_edges), dtype=np.intc).reshape(
        len(edge_array), 3
    )
    edges[:, :2] = edge_array
    edges[:, 2] = edge_map[edge_nz]
    return c_edges, len(edge_array)


def dijkstra2d(
    distance: np.array,
    cost: np.array,
    cardinal: Optional[int] = None,
    diagonal: Optional[int] = None,
    *,
    edge_map: Any = None
) -> None:
    """Return the computed distance of all nodes on a 2D Dijkstra grid.

    `distance` is an input/output array of node distances.  Is this often an
    array filled with maximum finite values and 1 or more points with a low
    value such as 0.  Distance will flow from these low values to adjacent
    nodes based the cost to reach those nodes.  This array is modified
    in-place.

    `cost` is an array of node costs.  Any node with a cost less than or equal
    to 0 is considered blocked off.  Positive values are the distance needed to
    reach that node.

    `cardinal` and `diagonal` are the cost multipliers for edges in those
    directions.  A value of None or 0 will disable those directions.  Typical
    values could be: ``1, None``, ``1, 1``, ``2, 3``, etc.

    `edge_map` is a 2D array of edge costs with the origin point centered on
    the array.  This can be used to define the edges used from one node to
    another.  This parameter can be hard to understand so you should see how
    it's used in the examples.

    Example::

        >>> import numpy as np
        >>> import tcod
        >>> cost = np.ones((3, 3), dtype=np.uint8)
        >>> cost[:2, 1] = 0
        >>> cost
        array([[1, 0, 1],
               [1, 0, 1],
               [1, 1, 1]], dtype=uint8)
        >>> dist = tcod.path.maxarray((3, 3), dtype=np.int32)
        >>> dist[0, 0] = 0
        >>> dist
        array([[         0, 2147483647, 2147483647],
               [2147483647, 2147483647, 2147483647],
               [2147483647, 2147483647, 2147483647]]...)
        >>> tcod.path.dijkstra2d(dist, cost, 2, 3)
        >>> dist
        array([[         0, 2147483647,         10],
               [         2, 2147483647,          8],
               [         4,          5,          7]]...)
        >>> path = tcod.path.hillclimb2d(dist, (2, 2), True, True)
        >>> path
        array([[2, 2],
               [2, 1],
               [1, 0],
               [0, 0]], dtype=int32)
        >>> path = path[::-1].tolist()
        >>> while path:
        ...     print(path.pop(0))
        [0, 0]
        [1, 0]
        [2, 1]
        [2, 2]

    `edge_map` is used for more complicated graphs.  The following example
    uses a 'knight move' edge map.

    Example::

        >>> import numpy as np
        >>> import tcod
        >>> knight_moves = [
        ...     [0, 1, 0, 1, 0],
        ...     [1, 0, 0, 0, 1],
        ...     [0, 0, 0, 0, 0],
        ...     [1, 0, 0, 0, 1],
        ...     [0, 1, 0, 1, 0],
        ... ]
        >>> dist = tcod.path.maxarray((8, 8))
        >>> dist[0,0] = 0
        >>> cost = np.ones((8, 8), int)
        >>> tcod.path.dijkstra2d(dist, cost, edge_map=knight_moves)
        >>> dist
        array([[0, 3, 2, 3, 2, 3, 4, 5],
               [3, 4, 1, 2, 3, 4, 3, 4],
               [2, 1, 4, 3, 2, 3, 4, 5],
               [3, 2, 3, 2, 3, 4, 3, 4],
               [2, 3, 2, 3, 4, 3, 4, 5],
               [3, 4, 3, 4, 3, 4, 5, 4],
               [4, 3, 4, 3, 4, 5, 4, 5],
               [5, 4, 5, 4, 5, 4, 5, 6]]...)
        >>> tcod.path.hillclimb2d(dist, (7, 7), edge_map=knight_moves)
        array([[7, 7],
               [5, 6],
               [3, 5],
               [1, 4],
               [0, 2],
               [2, 1],
               [0, 0]], dtype=int32)

    `edge_map` can also be used to define a hex-grid.
    See https://www.redblobgames.com/grids/hexagons/ for more info.
    The following example is using axial coordinates.

    Example::

        hex_edges = [
            [0, 1, 1],
            [1, 0, 1],
            [1, 1, 0],
        ]

    .. versionadded:: 11.2

    .. versionchanged:: 11.13
        Added the `edge_map` parameter.
    """
    dist = distance
    cost = np.asarray(cost)
    if dist.shape != cost.shape:
        raise TypeError(
            "distance and cost must have the same shape %r != %r"
            % (dist.shape, cost.shape)
        )
    c_dist = _export(dist)
    if edge_map is not None:
        if cardinal is not None or diagonal is not None:
            raise TypeError(
                "`edge_map` can not be set at the same time as"
                " `cardinal` or `diagonal`."
            )
        c_edges, n_edges = _compile_cost_edges(edge_map)
        _check(lib.dijkstra2d(c_dist, _export(cost), n_edges, c_edges))
    else:
        if cardinal is None:
            cardinal = 0
        if diagonal is None:
            diagonal = 0
        _check(lib.dijkstra2d_basic(c_dist, _export(cost), cardinal, diagonal))


def _compile_bool_edges(edge_map: Any) -> Tuple[Any, int]:
    """Return an edge array using a boolean map."""
    edge_map = np.copy(edge_map)
    edge_center = edge_map.shape[0] // 2, edge_map.shape[1] // 2
    edge_map[edge_center] = 0
    edge_array = np.transpose(edge_map.nonzero())
    edge_array -= edge_center
    return ffi.new("int[]", list(edge_array.flat)), len(edge_array)


def hillclimb2d(
    distance: np.array,
    start: Tuple[int, int],
    cardinal: Optional[bool] = None,
    diagonal: Optional[bool] = None,
    *,
    edge_map: Any = None
) -> np.array:
    """Return a path on a grid from `start` to the lowest point.

    `distance` should be a fully computed distance array.  This kind of array
    is returned by :any:`dijkstra2d`.

    `start` is a 2-item tuple with starting coordinates.  The axes if these
    coordinates should match the axis of the `distance` array.
    An out-of-bounds `start` index will raise an IndexError.

    At each step nodes adjacent toe current will be checked for a value lower
    than the current one.  Which directions are checked is decided by the
    boolean values `cardinal` and `diagonal`.  This process is repeated until
    all adjacent nodes are equal to or larger than the last point on the path.

    If `edge_map` was used with :any:`tcod.path.dijkstra2d` then it should be
    reused for this function.  Keep in mind that `edge_map` must be
    bidirectional since hill-climbing will traverse the map backwards.

    The returned array is a 2D NumPy array with the shape: (length, axis).
    This array always includes both the starting and ending point and will
    always have at least one item.

    Typical uses of the returned array will be to either convert it into a list
    which can be popped from, or transpose it and convert it into a tuple which
    can be used to index other arrays using NumPy's advanced indexing rules.

    .. versionadded:: 11.2

    .. versionchanged:: 11.13
        Added `edge_map` parameter.
    """
    x, y = start
    dist = np.asarray(distance)
    if not (0 <= x < dist.shape[0] and 0 <= y < dist.shape[1]):
        raise IndexError(
            "Starting point %r not in shape %r" % (start, dist.shape)
        )
    c_dist = _export(dist)
    if edge_map is not None:
        if cardinal is not None or diagonal is not None:
            raise TypeError(
                "`edge_map` can not be set at the same time as"
                " `cardinal` or `diagonal`."
            )
        c_edges, n_edges = _compile_bool_edges(edge_map)
        func = functools.partial(
            lib.hillclimb2d, c_dist, x, y, n_edges, c_edges
        )
    else:
        func = functools.partial(
            lib.hillclimb2d_basic, c_dist, x, y, cardinal, diagonal
        )
    length = _check(func(ffi.NULL))
    path = np.ndarray((length, 2), dtype=np.intc)
    c_path = ffi.cast("int*", path.ctypes.data)
    _check(func(c_path))
    return path


def _world_array(shape: Tuple[int, ...], dtype: Any = np.int32) -> np.ndarray:
    """Return an array where ``ij == arr[ij]``."""
    return np.ascontiguousarray(
        np.transpose(
            np.meshgrid(
                *(np.arange(i, dtype=dtype) for i in shape),
                indexing="ij",
                copy=False,
            ),
            axes=(*range(1, len(shape) + 1), 0),
        )
    )


def _as_hashable(obj: Optional[np.ndarray]) -> Optional[Any]:
    """Return NumPy arrays as a more hashable form."""
    if obj is None:
        return obj
    return obj.ctypes.data, tuple(obj.shape), tuple(obj.strides)


class CustomGraph:
    """A customizable graph defining how a pathfinder traverses the world.

    The graph is created with a `shape` defining the size and number of
    dimensions of the graph.  The `shape` can only be 4 dimensions or lower.

    After this graph is created you'll need to add edges which define the
    rules of the pathfinder.  These rules usually define movement in the
    cardinal and diagonal directions, but can also include stairway type edges.
    :any:`set_heuristic` should also be called so that the pathfinder will use
    A*.

    After all edge rules are added the graph can be used to make one or more
    :any:`Pathfinder` instances.

    Because the arrays used are in row-major order the indexes used in the
    examples be reversed from what you expect.
    A 2D edge or index is ``(y, x)`` and in 3D it is ``(z, y, x)``.

    Example::

        >>> import numpy as np
        >>> import tcod
        >>> graph = tcod.path.CustomGraph((5, 5))
        >>> cost = np.ones((5, 5), dtype=np.int8)
        >>> CARDINAL = [
        ...     [0, 1, 0],
        ...     [1, 0, 1],
        ...     [0, 1, 0],
        ... ]
        >>> graph.add_edges(edge_map=CARDINAL, cost=cost)
        >>> pf = tcod.path.Pathfinder(graph)
        >>> pf.add_root((0, 0))
        >>> pf.resolve()
        >>> pf.distance
        array([[0, 1, 2, 3, 4],
               [1, 2, 3, 4, 5],
               [2, 3, 4, 5, 6],
               [3, 4, 5, 6, 7],
               [4, 5, 6, 7, 8]]...)
        >>> pf.path_to((3, 3))
        array([[0, 0],
               [0, 1],
               [1, 1],
               [2, 1],
               [2, 2],
               [2, 3],
               [3, 3]]...)

    .. versionadded:: 11.13
    """

    def __init__(self, shape: Tuple[int, ...]):
        self._shape = tuple(shape)
        self._ndim = len(self._shape)
        assert 0 < self._ndim <= 4
        self._graph = {}  # type: Dict[Tuple[Any, ...], Dict[str, Any]]
        self._edge_rules_keep_alive = []  # type: List[Any]
        self._edge_rules_p = None  # type: Any
        self._heuristic = None  # type: Optional[Tuple[int, int, int, int]]

    @property
    def ndim(self) -> int:
        """The number of dimensions."""
        return self._ndim

    @property
    def shape(self) -> Tuple[int, ...]:
        """The shape of this graph."""
        return self._shape

    def add_edge(
        self,
        edge_dir: Tuple[int, ...],
        edge_cost: int = 1,
        *,
        cost: np.ndarray,
        condition: Optional[np.ndarray] = None
    ) -> None:
        """Add a single edge rule.

        `edge_dir` is a tuple with the same length as the graphs dimensions.
        The edge is relative to any node.

        `edge_cost` is the cost multiplier of the edge. Its multiplied with the
        `cost` array to the edges actual cost.

        `cost` is a NumPy array where each node has the cost for movement into
        that node.  Zero or negative values are used to mark blocked areas.

        `condition` is an optional array to mark which nodes have this edge.
        If the node in `condition` is zero then the edge will be skipped.
        This is useful to mark portals or stairs for some edges.

        Example::

            >>> import numpy as np
            >>> import tcod
            >>> graph3d = tcod.path.CustomGraph((2, 5, 5))
            >>> cost = np.ones((3, 5, 5), dtype=np.int8)
            >>> up_stairs = np.zeros((3, 5, 5), dtype=np.int8)
            >>> down_stairs = np.zeros((3, 5, 5), dtype=np.int8)
            >>> up_stairs[0, 0, 4] = 1
            >>> down_stairs[1, 0, 4] = 1
            >>> CARDINAL = [[0, 1, 0], [1, 0, 1], [0, 1, 0]]
            >>> graph3d.add_edges(edge_map=CARDINAL, cost=cost)
            >>> graph3d.add_edge((1, 0, 0), 1, cost=cost, condition=up_stairs)
            >>> graph3d.add_edge((-1, 0, 0), 1, cost=cost, condition=down_stairs)
            >>> pf3d = tcod.path.Pathfinder(graph3d)
            >>> pf3d.add_root((0, 1, 1))
            >>> pf3d.path_to((1, 2, 2))
            array([[0, 1, 1],
                   [0, 1, 2],
                   [0, 1, 3],
                   [0, 0, 3],
                   [0, 0, 4],
                   [1, 0, 4],
                   [1, 1, 4],
                   [1, 1, 3],
                   [1, 2, 3],
                   [1, 2, 2]]...)

        Note in the above example that both sets of up/down stairs were added,
        but bidirectional edges are not a requirement for the graph.
        One directional edges such as pits can be added which will
        only allow movement outwards from the root nodes of the pathfinder.
        """  # noqa: E501
        self._edge_rules_p = None
        edge_dir = tuple(edge_dir)
        assert len(edge_dir) == self._ndim
        assert edge_cost > 0, (edge_dir, edge_cost)
        cost = np.asarray(cost)
        assert cost.ndim == self.ndim
        if condition is not None:
            condition = np.asarray(condition)
        key = (_as_hashable(cost), _as_hashable(condition))
        try:
            rule = self._graph[key]
        except KeyError:
            rule = self._graph[key] = {
                "cost": cost,
                "edge_list": [],
            }
            if condition is not None:
                rule["condition"] = condition
        edge = edge_dir + (edge_cost,)
        if edge not in rule["edge_list"]:
            rule["edge_list"].append(edge)

    def add_edges(
        self,
        *,
        edge_map: Any,
        cost: np.ndarray,
        condition: Optional[np.ndarray] = None
    ) -> None:
        """Add a rule with multiple edges.

        `edge_map` is a NumPy array mapping the edges and their costs.
        This is easier to understand by looking at the examples below.
        Edges are relative to center of the array.  The center most value is
        always ignored.  If `edge_map` has fewer dimensions than the graph then
        it will apply to the right-most axes of the graph.

        `cost` is a NumPy array where each node has the cost for movement into
        that node.  Zero or negative values are used to mark blocked areas.

        `condition` is an optional array to mark which nodes have this edge.
        See :any:`add_edge`.
        If `condition` is the same array as `cost` then the pathfinder will
        not move into open area from a non-open ones.

        Example::

            # 2D edge maps:
            CARDINAL = [  # Simple arrow-key moves.  Manhattan distance.
                [0, 1, 0],
                [1, 0, 1],
                [0, 1, 0],
            ]
            CHEBYSHEV = [  # Chess king moves.  Chebyshev distance.
                [1, 1, 1],
                [1, 0, 1],
                [1, 1, 1],
            ]
            EUCLIDEAN = [  # Approximate euclidean distance.
                [99, 70, 99],
                [70, 0, 70],
                [99, 70, 99],
            ]
            EUCLIDEAN_SIMPLE = [  # Very approximate euclidean distance.
                [3, 2, 3],
                [2, 0, 2],
                [3, 2, 3],
            ]
            KNIGHT_MOVE = [  # Chess knight L-moves.
                [0, 1, 0, 1, 0],
                [1, 0, 0, 0, 1],
                [0, 0, 0, 0, 0],
                [1, 0, 0, 0, 1],
                [0, 1, 0, 1, 0],
            ]
            AXIAL = [  # https://www.redblobgames.com/grids/hexagons/
                [0, 1, 1],
                [1, 0, 1],
                [1, 1, 0],
            ]
            # 3D edge maps:
            CARDINAL_PLUS_Z = [  # Cardinal movement with Z up/down edges.
                [
                    [0, 0, 0],
                    [0, 1, 0],
                    [0, 0, 0],
                ],
                [
                    [0, 1, 0],
                    [1, 0, 1],
                    [0, 1, 0],
                ],
                [
                    [0, 0, 0],
                    [0, 1, 0],
                    [0, 0, 0],
                ],
            ]
            CHEBYSHEV_3D = [  # Chebyshev distance, but in 3D.
                [
                    [1, 1, 1],
                    [1, 1, 1],
                    [1, 1, 1],
                ],
                [
                    [1, 1, 1],
                    [1, 0, 1],
                    [1, 1, 1],
                ],
                [
                    [1, 1, 1],
                    [1, 1, 1],
                    [1, 1, 1],
                ],
            ]
        """
        edge_map = np.copy(edge_map)
        if edge_map.ndim < self._ndim:
            edge_map = edge_map[(np.newaxis,) * (self._ndim - edge_map.ndim)]
        if edge_map.ndim != self._ndim:
            raise ValueError(
                "edge_map must must match graph dimensions (%i). (Got %i)"
                % (self.ndim, edge_map.ndim)
            )
        edge_center = tuple(i // 2 for i in edge_map.shape)
        edge_map[edge_center] = 0
        edge_map[edge_map < 0] = 0
        edge_nz = edge_map.nonzero()
        edge_costs = edge_map[edge_nz]
        edge_array = np.transpose(edge_nz)
        edge_array -= edge_center
        for edge, edge_cost in zip(edge_array, edge_costs):
            edge = tuple(edge)
            self.add_edge(edge, edge_cost, cost=cost, condition=condition)

    def set_heuristic(
        self, *, cardinal: int = 0, diagonal: int = 0, z: int = 0, w: int = 0
    ) -> None:
        """Sets a pathfinder heuristic so that pathfinding can done with A*.

        `cardinal`, `diagonal`, `z, and `w` are the lower-bound cost of
        movement in those directions.  Values above the lower-bound can be
        used to create a greedy heuristic, which will be faster at the cost of
        accuracy.

        Example::

            >>> import numpy as np
            >>> import tcod
            >>> graph = tcod.path.CustomGraph((5, 5))
            >>> cost = np.ones((5, 5), dtype=np.int8)
            >>> EUCLIDEAN = [[99, 70, 99], [70, 0, 70], [99, 70, 99]]
            >>> graph.add_edges(edge_map=EUCLIDEAN, cost=cost)
            >>> graph.set_heuristic(cardinal=70, diagonal=99)
            >>> pf = tcod.path.Pathfinder(graph)
            >>> pf.add_root((0, 0))
            >>> pf.path_to((4, 4))
            array([[0, 0],
                   [1, 1],
                   [2, 2],
                   [3, 3],
                   [4, 4]]...)
            >>> pf.distance
            array([[         0,         70,        198, 2147483647, 2147483647],
                   [        70,         99,        169,        297, 2147483647],
                   [       198,        169,        198,        268,        396],
                   [2147483647,        297,        268,        297,        367],
                   [2147483647, 2147483647,        396,        367,        396]]...)
            >>> pf.path_to((2, 0))
            array([[0, 0],
                   [1, 0],
                   [2, 0]]...)
            >>> pf.distance
            array([[         0,         70,        198, 2147483647, 2147483647],
                   [        70,         99,        169,        297, 2147483647],
                   [       140,        169,        198,        268,        396],
                   [       210,        239,        268,        297,        367],
                   [2147483647, 2147483647,        396,        367,        396]]...)

        Without a heuristic the above example would need to evaluate the entire
        array to reach the opposite side of it.
        With a heuristic several nodes can be skipped, which will process
        faster.  Some of the distances in the above example look incorrect,
        that's because those nodes are only partially evaluated, but
        pathfinding to those nodes will work correctly as long as the heuristic
        isn't greedy.
        """  # noqa: E501
        if 0 == cardinal == diagonal == z == w:
            self._heuristic = None
        if diagonal and cardinal > diagonal:
            raise ValueError(
                "Diagonal parameter can not be lower than cardinal."
            )
        if cardinal < 0 or diagonal < 0 or z < 0 or w < 0:
            raise ValueError("Parameters can not be set to negative values..")
        self._heuristic = (cardinal, diagonal, z, w)

    def _compile_rules(self) -> Any:
        """Compile this graph into a C struct array."""
        if not self._edge_rules_p:
            self._edge_rules_keep_alive = []
            rules = []
            for rule_ in self._graph.values():
                rule = rule_.copy()
                rule["edge_count"] = len(rule["edge_list"])
                # Edge rule format: [i, j, cost, ...] etc.
                edge_obj = ffi.new(
                    "int[]", len(rule["edge_list"]) * (self._ndim + 1)
                )
                edge_obj[0 : len(edge_obj)] = itertools.chain(
                    *rule["edge_list"]
                )
                self._edge_rules_keep_alive.append(edge_obj)
                rule["edge_array"] = edge_obj
                self._edge_rules_keep_alive.append(rule["cost"])
                rule["cost"] = _export_dict(rule["cost"])
                if "condition" in rule:
                    self._edge_rules_keep_alive.append(rule["condition"])
                    rule["condition"] = _export_dict(rule["condition"])
                del rule["edge_list"]
                rules.append(rule)
            self._edge_rules_p = ffi.new("struct PathfinderRule[]", rules)
        return self._edge_rules_p, self._edge_rules_keep_alive

    def _resolve(self, pathfinder: "Pathfinder") -> None:
        """Run the pathfinding algorithm for this graph."""
        rules, keep_alive = self._compile_rules()
        _check(
            lib.path_compute(
                pathfinder._frontier_p,
                pathfinder._distance_p,
                pathfinder._travel_p,
                len(rules),
                rules,
                pathfinder._heuristic_p,
            )
        )


class Pathfinder:
    """A generic modular pathfinder.

    How the pathfinder functions depends on the graph provided. see
    :any:`CustomGraph` for how to set these up.

    .. versionadded:: 11.13
    """

    def __init__(self, graph: CustomGraph):
        self._graph = graph
        self._frontier_p = ffi.gc(
            lib.TCOD_frontier_new(self._graph._ndim), lib.TCOD_frontier_delete
        )
        self._distance = maxarray(self._graph._shape)
        self._travel = _world_array(self._graph._shape)
        assert self._travel.flags["C_CONTIGUOUS"]
        self._distance_p = _export(self._distance)
        self._travel_p = _export(self._travel)
        self._heuristic = (
            None
        )  # type: Optional[Tuple[int, int, int, int, Tuple[int, ...]]]
        self._heuristic_p = ffi.NULL  # type: Any

    @property
    def distance(self) -> np.ndarray:
        """The distance values of the pathfinder.

        This array is stored in row-major "C" order.

        Unreachable or unresolved points will be at their maximum values.
        You can use :any:`numpy.iinfo` if you need to check for these.

        Example::

            pf  # Resolved Pathfinder instance.
            reachable = pf.distance != numpy.iinfo(pf.distance.dtype).max
            reachable  # A boolean array of reachable area.

        You may edit this array manually, but the pathfinder won't know of
        your changes until :any:`rebuild_frontier` is called.
        """
        return self._distance

    @property
    def traversal(self) -> np.ndarray:
        """An array used to generate paths from any point to the nearest root.

        This array is stored in row-major "C" order.  It has an extra
        dimension which includes the index of the next path.

        Example::

            # This example demonstrates the purpose of the traversal array.
            # In real code Pathfinder.path_from(...) should be used instead.
            pf  # Resolved 2D Pathfinder instance.
            i, j = (3, 3)  # Starting index.
            path = [(i, j)]  # List of nodes from the start to the root.
            while not (pf.traversal[i, j] == (i, j)).all():
                i, j = pf.traversal[i, j]
                path.append((i, j))

        The above example is slow and will not detect infinite loops.  Use
        :any:`path_from` or :any:`path_to` when you need to get a path.

        As the pathfinder is resolved this array is filled
        """
        return self._travel

    def clear(self) -> None:
        """Reset the pathfinder to its initial state.

        This sets all values on the :any:`distance` array to their maximum
        value.
        """
        self._distance[...] = np.iinfo(self._distance.dtype).max
        self._travel = _world_array(self._graph._shape)
        lib.TCOD_frontier_clear(self._frontier_p)

    def add_root(self, index: Tuple[int, ...], value: int = 0) -> None:
        """Add a root node and insert it into the pathfinder frontier.

        `index` is the root point to insert.  The length of `index` must match
        the dimensions of the graph.  `index` must also be in 'ij' order.

        `value` is the distance to use for this root.  Zero is typical, but
        if multiple roots are added they can be given different weights.
        """
        index_ = tuple(index)
        assert len(index_) == self._distance.ndim
        self._distance[index_] = value
        self._update_heuristic(None)
        lib.TCOD_frontier_push(self._frontier_p, index_, value, value)

    def _update_heuristic(self, goal: Optional[Tuple[int, ...]]) -> bool:
        """Update the active heuristic.  Return True if the heuristic changed.
        """
        if goal is None:
            heuristic = None
        elif self._graph._heuristic is None:
            heuristic = (0, 0, 0, 0, goal)
        else:
            heuristic = (*self._graph._heuristic, goal)
        if self._heuristic == heuristic:
            return False  # Frontier does not need updating.
        self._heuristic = heuristic
        if heuristic is None:
            self._heuristic_p = ffi.NULL
        else:
            self._heuristic_p = ffi.new(
                "struct PathfinderHeuristic*", heuristic
            )
        lib.update_frontier_heuristic(self._frontier_p, self._heuristic_p)
        return True  # Frontier was updated.

    def rebuild_frontier(self) -> None:
        """Reconstruct the frontier using the current distance array.

        This is needed if the :any:`distance` array is changed manually.
        After you are finished editing :any:`distance` you must call this
        function before calling :any:`resolve`, :any:`path_from`, etc.
        """
        lib.TCOD_frontier_clear(self._frontier_p)
        self._update_heuristic(None)
        _check(
            lib.rebuild_frontier_from_distance(
                self._frontier_p, self._distance_p
            )
        )

    def resolve(self, goal: Optional[Tuple[int, ...]] = None) -> None:
        """Manually run the pathfinder algorithm."""
        if goal is not None:
            assert len(goal) == self._distance.ndim
            if self._distance[goal] != np.iinfo(self._distance.dtype).max:
                if not lib.frontier_has_index(self._frontier_p, goal):
                    return
        self._update_heuristic(goal)
        self._graph._resolve(self)

    def path_from(self, index: Tuple[int, ...]) -> np.ndarray:
        """Return the shortest path from `index` to the nearest root.

        The return value is inclusive, including both the starting and ending
        points on the path.  If the root point is unreachable or `index` is
        already at a root then `index` will be the only point returned.

        This automatically calls :any:`resolve` if the pathfinder has not
        yet reached `index`.

        A common usage is to slice off the starting point and convert the array
        into a list.
        """
        self.resolve(index)
        assert len(index) == self._graph._ndim
        length = _check(
            lib.get_travel_path(
                self._graph._ndim, self._travel_p, index, ffi.NULL,
            )
        )
        path = np.ndarray((length, self._graph._ndim), dtype=np.intc)
        _check(
            lib.get_travel_path(
                self._graph._ndim,
                self._travel_p,
                index,
                ffi.cast("int*", path.ctypes.data),
            )
        )
        return path

    def path_to(self, index: Tuple[int, ...]) -> np.ndarray:
        """Return the shortest path from the nearest root to `index`.

        This is an alias for ``path_from(...)[::-1]``.
        """
        return self.path_from(index)[::-1]
