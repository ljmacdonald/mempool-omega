"""Wallet clustering & transfer hygiene with NetworkX.

* Filter internal transfers (exchange -> same exchange cluster), known OTC desks, bridges.
* Detect wash rings (short directed cycles carrying most of the volume).
* Produce a ``graph_cleanliness`` score in [0,1] for on-chain signals.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import yaml

KNOWN_PATH = Path(__file__).with_name("known_wallets.yaml")


@lru_cache(maxsize=1)
def load_known_wallets() -> dict[str, list[str]]:
    try:
        d = yaml.safe_load(KNOWN_PATH.read_text()) or {}
    except OSError:
        d = {}
    return {k: [a.lower() for a in (d.get(k) or [])] for k in ("exchange", "otc", "bridges")}


def build_graph(transfers: pd.DataFrame) -> nx.DiGraph:
    g = nx.DiGraph()
    for frm, to, val in transfers[["from", "to", "value"]].itertuples(index=False):
        frm, to = str(frm).lower(), str(to).lower()
        if g.has_edge(frm, to):
            g[frm][to]["value"] += float(val)
            g[frm][to]["n"] += 1
        else:
            g.add_edge(frm, to, value=float(val), n=1)
    return g


def cluster_wallets(g: nx.DiGraph, seed: int = 0) -> dict[str, int]:
    """Louvain communities on the undirected, value-weighted graph."""
    if g.number_of_nodes() == 0:
        return {}
    ug = g.to_undirected()
    for _u, _v, d in ug.edges(data=True):
        d["weight"] = np.log1p(d.get("value", 1.0))
    comms = nx.community.louvain_communities(ug, weight="weight", seed=seed)
    return {node: i for i, c in enumerate(comms) for node in c}


def filter_transfers(transfers: pd.DataFrame, clusters: dict[str, int] | None = None) -> pd.DataFrame:
    """Drop exchange-internal, OTC and bridge transfers that would masquerade as 'flow'."""
    known = load_known_wallets()
    ex, otc, br = set(known["exchange"]), set(known["otc"]), set(known["bridges"])
    t = transfers.copy()
    t["from"], t["to"] = t["from"].str.lower(), t["to"].str.lower()
    internal = t["from"].isin(ex) & t["to"].isin(ex)
    noise = t["from"].isin(otc | br) | t["to"].isin(otc | br)
    if clusters:
        same = t["from"].map(clusters).eq(t["to"].map(clusters)) & (t["from"].isin(ex) | t["to"].isin(ex))
        internal |= same
    return t[~(internal | noise)]


def wash_cycle_share(g: nx.DiGraph, max_len: int = 4) -> float:
    """Share of total transferred value on edges that belong to short directed cycles."""
    total = sum(d["value"] for _, _, d in g.edges(data=True))
    if total <= 0:
        return 0.0
    cyc_edges: set[tuple[str, str]] = set()
    try:
        cycles = nx.simple_cycles(g, length_bound=max_len)
    except TypeError:  # networkx < 3.1
        cycles = (c for c in nx.simple_cycles(g) if len(c) <= max_len)
    for k, c in enumerate(cycles):
        if k > 5000:
            break
        for i in range(len(c)):
            cyc_edges.add((c[i], c[(i + 1) % len(c)]))
    cyc_val = sum(g[u][v]["value"] for u, v in cyc_edges)
    return float(cyc_val / total)


def sender_concentration(transfers: pd.DataFrame) -> float:
    """Herfindahl index of senders by value (1 = one wallet is the whole 'flow')."""
    if transfers.empty:
        return 0.0
    w = transfers.groupby(transfers["from"].str.lower())["value"].sum()
    p = w / w.sum()
    return float((p**2).sum())


def graph_cleanliness(transfers: pd.DataFrame | None) -> float:
    """1 = organic flow; ->0 = wash rings / single-actor flow."""
    if transfers is None or len(transfers) == 0:
        return 0.7  # unknown -> neutral prior
    g = build_graph(transfers)
    wash = wash_cycle_share(g)
    hhi = sender_concentration(filter_transfers(transfers, cluster_wallets(g)))
    return float(np.clip(1 - 0.8 * wash - 0.5 * max(hhi - 0.1, 0), 0, 1))
