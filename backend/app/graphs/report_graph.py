from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from backend.app.graphs.report_nodes import (
    bootstrap_report_run,
    build_report_artifacts,
    load_report_inputs,
    persist_report_run,
)
from backend.app.graphs.report_state import ReportGraphState


def build_report_graph(db):
    builder = StateGraph(ReportGraphState)
    builder.add_node("bootstrap_report_run", bootstrap_report_run)
    builder.add_node("load_report_inputs", lambda state: load_report_inputs(state, db=db))
    builder.add_node("build_report_artifacts", build_report_artifacts)
    builder.add_node("persist_report_run", lambda state: persist_report_run(state, db=db))
    builder.add_edge(START, "bootstrap_report_run")
    builder.add_edge("bootstrap_report_run", "load_report_inputs")
    builder.add_edge("load_report_inputs", "build_report_artifacts")
    builder.add_edge("build_report_artifacts", "persist_report_run")
    builder.add_edge("persist_report_run", END)
    return builder.compile()
