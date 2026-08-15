from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from backend.app.graphs.summary_nodes import (
    generate_summary_artifact,
    load_current_item,
    persist_current_item,
    retrieve_prompt_context,
    retrieve_relations,
)
from backend.app.graphs.summary_state import SummaryGraphState


def build_summary_item_graph(db):
    builder = StateGraph(SummaryGraphState)
    builder.add_node("load_current_item", lambda state: load_current_item(state, db=db))
    builder.add_node(
        "retrieve_prompt_context",
        lambda state: retrieve_prompt_context(state, db=db),
    )
    builder.add_node(
        "generate_summary_artifact",
        lambda state: generate_summary_artifact(state, db=db),
    )
    builder.add_node(
        "retrieve_relations",
        lambda state: retrieve_relations(state, db=db),
    )
    builder.add_node(
        "persist_current_item",
        lambda state: persist_current_item(state, db=db),
    )
    builder.add_edge(START, "load_current_item")
    builder.add_edge("load_current_item", "retrieve_prompt_context")
    builder.add_edge("retrieve_prompt_context", "generate_summary_artifact")
    builder.add_edge("generate_summary_artifact", "retrieve_relations")
    builder.add_edge("retrieve_relations", "persist_current_item")
    builder.add_edge("persist_current_item", END)
    return builder.compile()
