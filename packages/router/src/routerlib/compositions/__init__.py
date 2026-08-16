"""Compositions: several routers on one board, not several candidates for it.

The tournament measured nine families independently and the answer was that
*picking* one buys almost nothing — the oracle of nine is two nets better than
always running ``pathfinder-negotiated``, while the union of what the nine
route is 378 of 380 nets. All the headroom is in getting more than one of them
onto the same board.

Each module here is one way to do that, and each is measured on the same
benchmark against the same ruler as a single family:

* :mod:`routerlib.compositions.netclass` — route by net class in dependency
  order, each class to the expert for it. The only composition that can
  *delete* work rather than move it: where a layer is poured, the ground net
  stops being a routing problem.
* :mod:`routerlib.compositions.spatial` — partition the board, route each
  region with its best expert, after fixing the crossings.

:mod:`routerlib.compositions.registry` is the shared part: a composition takes
its name → factory map as an argument rather than importing one, which is what
makes an individual stage swappable.

``routerlib.portfolio`` holds the relay (lead router, then followers get only
the still-unconnected nets), which was the first composition built and is the
number to beat.
"""

__all__: list[str] = []
