"""Alternative-graph model of the junction (Mascis & Pacciarelli).

Real-time railway rescheduling is a job-shop scheduling problem with BLOCKING:
each train (a job) needs a sequence of resources (machines) - junctions, block
sections, platform roads - and cannot release one until it has acquired the next.

    nodes            one per (train, resource) pass
    fixed arcs       train i must spend at least its running time on a resource
                     before it can reach the next one
    alternative arcs a PAIR for every two trains contending for the same
                     resource: either A goes before B, or B goes before A

Choosing one arc from every pair is a passing order for the whole junction. A
selection is feasible exactly when the resulting graph has NO POSITIVE CYCLE - a
cycle would mean a train must precede itself. The makespan, and every train's
delay, is then the longest path through the graph.

This replaces per-conflict greedy resolution. Solving all contended resources
JOINTLY is the whole point: clearing one conflict by holding a train usually
creates another one further along, and a per-conflict solver cannot see that.

`solve_amcc` is the AMCC heuristic (Avoid Maximum Current Cmax), used as the
fast fallback whenever the exact CP-SAT solver runs out of its time budget.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

INF = float("inf")


@dataclass(frozen=True)
class Node:
    train_id: str
    resource_id: str
    index: int              # position in this train's resource sequence

    def __str__(self) -> str:
        return f"{self.train_id}@{self.resource_id}"


@dataclass
class AltGraph:
    """Nodes, fixed arcs and alternative arc pairs for one horizon."""
    nodes: list[Node] = field(default_factory=list)
    # fixed[u] = [(v, weight)] : start(v) >= start(u) + weight
    fixed: dict[Node, list[tuple[Node, float]]] = field(default_factory=lambda: defaultdict(list))
    # release[node] = earliest possible start (train is not there before this)
    release: dict[Node, float] = field(default_factory=dict)
    # each pair is ((u, v, w_uv), (x, y, w_xy)) - choose exactly one arc
    pairs: list[tuple[tuple[Node, Node, float], tuple[Node, Node, float]]] = field(default_factory=list)
    # bookkeeping for cost attribution
    train_of: dict[Node, str] = field(default_factory=dict)
    due: dict[str, float] = field(default_factory=dict)

    def add_fixed(self, u: Node, v: Node, weight: float) -> None:
        self.fixed[u].append((v, weight))


def build(plans: dict[str, list], headway_of, blocked: set[str] | None = None) -> AltGraph:
    """Build the graph from projected per-train resource plans.

    `plans[train_id]` is the ordered list of PassWindow objects from predict();
    `headway_of(resource_id)` gives the minimum separation over that resource.
    """
    blocked = blocked or set()
    g = AltGraph()
    by_resource: dict[str, list[Node]] = defaultdict(list)
    occupancy: dict[Node, float] = {}

    for tid, plan in plans.items():
        prev: Node | None = None
        prev_exit = 0.0
        for i, w in enumerate(plan):
            node = Node(tid, w.resource_id, i)
            g.nodes.append(node)
            g.train_of[node] = tid
            g.release[node] = (86_400.0 if w.resource_id in blocked
                               else max(0.0, w.enter))
            occupancy[node] = max(1.0, w.exit - w.enter)
            if prev is not None:
                # Blocking: the train cannot reach this resource before it has
                # finished with the previous one and run the distance between.
                g.add_fixed(prev, node, max(1.0, w.enter - prev_exit) + occupancy[prev])
            by_resource[w.resource_id].append(node)
            prev, prev_exit = node, w.exit
        if plan:
            g.due[tid] = plan[-1].exit

    # One alternative pair per contending couple on each shared resource.
    for rid, nodes in by_resource.items():
        headway = headway_of(rid)
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                u, v = nodes[i], nodes[j]
                if u.train_id == v.train_id:
                    continue
                g.pairs.append((
                    (u, v, occupancy[u] + headway),   # u before v
                    (v, u, occupancy[v] + headway),   # v before u
                ))
    g.occupancy = occupancy  # type: ignore[attr-defined]
    return g


def longest_paths(g: AltGraph, selected: list[tuple[Node, Node, float]]
                  ) -> dict[Node, float] | None:
    """Earliest feasible start time per node, or None if a positive cycle exists.

    Bellman-Ford style relaxation: a cycle of positive length means some train
    must run before itself, which is exactly an infeasible passing order.
    """
    adj: dict[Node, list[tuple[Node, float]]] = defaultdict(list)
    for u, targets in g.fixed.items():
        for v, w in targets:
            adj[u].append((v, w))
    for u, v, w in selected:
        adj[u].append((v, w))

    start = dict(g.release)
    n = len(g.nodes)
    for iteration in range(n + 1):
        changed = False
        for u in g.nodes:
            su = start.get(u, 0.0)
            for v, w in adj.get(u, ()):
                if su + w > start.get(v, 0.0) + 1e-9:
                    start[v] = su + w
                    changed = True
        if not changed:
            return start
        if iteration == n:
            return None       # still relaxing after |V| passes -> positive cycle
    return start


def total_cost(g: AltGraph, start: dict[Node, float], weight_of) -> float:
    """Weighted lateness implied by a schedule: how much later each train
    finishes than its conflict-free projection, times what that train is worth."""
    finish: dict[str, float] = {}
    occupancy = getattr(g, "occupancy", {})
    for node, t in start.items():
        end = t + occupancy.get(node, 0.0)
        tid = g.train_of[node]
        if end > finish.get(tid, 0.0):
            finish[tid] = end
    return sum(max(0.0, finish[tid] - g.due.get(tid, 0.0)) * weight_of(tid)
               for tid in finish)


def solve_amcc(g: AltGraph, weight_of, max_iterations: int = 400
               ) -> tuple[list[tuple[Node, Node, float]], float] | None:
    """AMCC: greedily fix the arc whose ALTERNATIVE would hurt most.

    At each step, for every unfixed pair, look at the makespan each of the two
    arcs would force. Pick the pair with the largest gap and commit to its
    cheaper arc - i.e. avoid the maximum current Cmax. Fast, deterministic, and
    the standard heuristic for this model.
    """
    selected: list[tuple[Node, Node, float]] = []
    remaining = list(g.pairs)
    start = longest_paths(g, selected)
    if start is None:
        return None

    for _ in range(min(max_iterations, len(g.pairs))):
        if not remaining:
            break
        best = None
        for pair in remaining:
            a, b = pair
            sa = longest_paths(g, selected + [a])
            sb = longest_paths(g, selected + [b])
            ca = total_cost(g, sa, weight_of) if sa is not None else INF
            cb = total_cost(g, sb, weight_of) if sb is not None else INF
            if ca is INF and cb is INF:
                return None                     # this pair cannot be satisfied
            gap = abs(ca - cb) if (ca < INF and cb < INF) else INF
            choice = a if ca <= cb else b
            cost = min(ca, cb)
            if best is None or gap > best[0]:
                best = (gap, pair, choice, cost)
        if best is None:
            break
        _, pair, choice, _ = best
        selected.append(choice)
        remaining.remove(pair)

    start = longest_paths(g, selected)
    if start is None:
        return None
    return selected, total_cost(g, start, weight_of)


def natural_order(g: AltGraph) -> list[tuple[Node, Node, float]]:
    """The passing order the timetable already implies - first come, first served.

    This is the do-nothing schedule the optimiser has to beat, and it is also the
    FCFS dispatcher baseline.
    """
    selected: list[tuple[Node, Node, float]] = []
    for a, b in g.pairs:
        u, v, _ = a
        selected.append(a if g.release.get(u, 0.0) <= g.release.get(v, 0.0) else b)
    return selected
