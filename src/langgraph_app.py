"""LangGraph application for Telegram crypto listener"""
from langgraph.graph import StateGraph, START, END
from typing import TypedDict


class State(TypedDict):
    """Application state"""
    message: str


def process_message(state: State) -> State:
    """Process incoming message"""
    return {"message": state.get("message", "")}


# Create the graph
workflow = StateGraph(State)
workflow.add_node("process", process_message)
workflow.add_edge(START, "process")
workflow.add_edge("process", END)

graph = workflow.compile()
