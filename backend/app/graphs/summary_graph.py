from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from backend.app.graphs.summary_item_graph import build_summary_item_graph
from backend.app.graphs.summary_nodes import (
    bootstrap_run,
    finalize_cancelled_run,
    finalize_completed_run,
    pick_next_item,
)
from backend.app.graphs.summary_state import SummaryGraphState


def _route_after_pick(state: SummaryGraphState) -> str:
    stage = str(state.get("stage", "summarizing"))
    if stage == "completed":
        return "finalize_completed_run"
    if stage == "cancelled":
        return "finalize_cancelled_run"
    return "run_item_subgraph"


def build_summary_graph(db, config):
    builder = StateGraph(SummaryGraphState)
    item_subgraph = build_summary_item_graph(db)
    builder.add_node("bootstrap_run", lambda state: bootstrap_run(state, db=db))
    builder.add_node("pick_next_item", lambda state: pick_next_item(state, db=db))
    builder.add_node("run_item_subgraph", item_subgraph)
    builder.add_node(
        "finalize_completed_run",
        lambda state: finalize_completed_run(state, db=db),
    )
    builder.add_node(
        "finalize_cancelled_run",
        lambda state: finalize_cancelled_run(state, db=db),
    )
    builder.add_edge(START, "bootstrap_run")
    builder.add_edge("bootstrap_run", "pick_next_item")
    builder.add_conditional_edges(
        "pick_next_item",
        _route_after_pick,
        {
            "run_item_subgraph": "run_item_subgraph",
            "finalize_completed_run": "finalize_completed_run",
            "finalize_cancelled_run": "finalize_cancelled_run",
        },
    )
    builder.add_edge("run_item_subgraph", "pick_next_item")
    builder.add_edge("finalize_completed_run", END)
    builder.add_edge("finalize_cancelled_run", END)
    return builder.compile()
