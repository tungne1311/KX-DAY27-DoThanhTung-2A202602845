"""Exercise 3 — Escalation branch with reviewer Q&A.

When confidence < 58%, the agent doesn't ask approve/reject — it asks specific
clarifying questions and then synthesizes a refined review from the answers.
"""

from __future__ import annotations

import argparse
import uuid

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from rich.console import Console
from rich.panel import Panel

from common.github import post_review_comment
from common.terminal import create_console
from exercises.review_helpers import (
    LabState, analyze_review, approval_payload, escalation_payload, fetch_review_pr,
    validate_answers, validate_approval,
)
from exercises.exercise_2_hitl import node_edit_review, prompt_human
from common.schemas import (
    AUTO_APPROVE_THRESHOLD,
    ESCALATE_THRESHOLD,
    PRAnalysis,
    ReviewState,
)


console = create_console()


def node_fetch_pr(state):
    console.print("[cyan]→ fetch_pr[/cyan]")
    with console.status("[dim]Fetching PR from GitHub...[/dim]"):
        pr = fetch_review_pr(state)
    console.print(f"  [green]✓[/green] {len(pr.files_changed)} files, head {pr.head_sha[:7]}")
    return {"pr_title": pr.title, "pr_diff": pr.diff, "pr_files": pr.files_changed, "pr_head_sha": pr.head_sha}


def node_analyze(state):
    console.print("[cyan]→ analyze[/cyan]")
    with console.status("[dim]LLM reviewing the diff...[/dim]"):
        analysis = analyze_review(state)
    console.print(f"  [green]✓[/green] confidence={analysis.confidence:.0%}, {len(analysis.escalation_questions)} question(s)")
    return {"analysis": analysis}


def node_route(state):
    console.print("[cyan]→ route[/cyan]")
    c = state["analysis"].confidence
    if c >= AUTO_APPROVE_THRESHOLD: decision = "auto_approve"
    elif c < ESCALATE_THRESHOLD:    decision = "escalate"
    else:                           decision = "human_approval"
    console.print(f"  [green]✓[/green] decision=[bold]{decision}[/bold] (confidence={c:.0%})")
    return {"decision": decision}


def node_escalate(state: LabState) -> dict:
    """Ask the reviewer specific questions; return their answers in state."""
    payload = escalation_payload(state)
    answers = interrupt(payload)
    return {"escalation_answers": validate_answers(answers, payload["questions"])}


def node_synthesize(state: LabState) -> dict:
    """Re-prompt LLM with the reviewer's answers and produce a refined review."""
    refined = analyze_review(state, "synthesize")
    return {"analysis": refined, "human_choice": None,
            "revision": state.get("revision", 0) + 1}


def node_human_approval(state):
    return validate_approval(interrupt(approval_payload(state)))


def _render_comment_body(state) -> str:
    a = state["analysis"]
    lines = [f"### Automated review (confidence {a.confidence:.0%})", "", a.summary, ""]
    for c in a.comments:
        lines.append(f"- **[{c.severity}]** `{c.file}:{c.line or '?'}` — {c.body}")
    if state.get("human_feedback"):
        lines.append(f"\n_Reviewer note: {state['human_feedback']}_")
    if state.get("escalation_answers"):
        lines.append("\n_Reviewer answered escalation questions:_")
        for q, ans in state["escalation_answers"].items():
            lines.append(f"> **{q}** {ans}")
    return "\n".join(lines)


def _post(state, label: str) -> str:
    try:
        if state.get("demo_mode"):
            console.print("[yellow]OFFLINE DEMO: comment simulated; nothing posted.[/yellow]")
            return f"demo_{label}"
        post_review_comment(state["pr_url"], _render_comment_body(state))
        console.print(f"  [green]✓[/green] posted comment to {state['pr_url']}")
        return label
    except Exception as e:
        console.print(f"  [red]✗[/red] post failed: {e}")
        return "commit_failed"


def node_commit(state):
    console.print("[cyan]→ commit[/cyan]")
    if state.get("human_choice") == "approve":
        label = "committed_after_escalation" if state.get("escalation_answers") else "committed"
        return {"final_action": _post(state, label)}
    console.print(f"  [yellow]·[/yellow] skipping comment (choice={state.get('human_choice')})")
    return {"final_action": "rejected"}


def node_auto_approve(state):
    console.print("[cyan]→ auto_approve[/cyan]  [dim]high confidence — posting directly[/dim]")
    return {"final_action": _post(state, "auto_approved")}


def build_graph():
    g = StateGraph(LabState)
    for name, fn in [
        ("fetch_pr", node_fetch_pr), ("analyze", node_analyze), ("route", node_route),
        ("auto_approve", node_auto_approve), ("human_approval", node_human_approval),
        ("commit", node_commit), ("escalate", node_escalate), ("synthesize", node_synthesize),
        ("edit_review", node_edit_review),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "fetch_pr")
    g.add_edge("fetch_pr", "analyze")
    g.add_edge("analyze", "route")
    g.add_conditional_edges(
        "route", lambda s: s["decision"],
        {"auto_approve": "auto_approve", "human_approval": "human_approval", "escalate": "escalate"},
    )
    g.add_edge("auto_approve", END)
    g.add_conditional_edges("human_approval",
                            lambda state: "edit_review" if state["human_choice"] == "edit" else "commit")
    g.add_edge("edit_review", "human_approval")
    g.add_edge("commit", END)
    g.add_edge("escalate", "synthesize")
    g.add_edge("synthesize", "human_approval")
    return g.compile(checkpointer=MemorySaver())


def handle_interrupt(payload):
    kind = payload["kind"]
    if kind == "approval_request":
        return prompt_human(payload)
    if kind == "escalation":
        console.print(Panel.fit(
            payload["summary"],
            title=f"Escalation conf={payload['confidence']:.0%}",
            border_style="yellow",
        ))
        console.print(payload["confidence_reasoning"], markup=False)
        for risk in payload.get("risk_factors", []):
            console.print(f"Risk: {risk}", markup=False)
        answers = {}
        for q in payload["questions"]:
            answer = ""
            while not answer:
                answer = console.input(f"Q: {q}\nA: ").strip()
            answers[q] = answer
        return answers
    raise ValueError(kind)


def main():
    load_dotenv()
    p = argparse.ArgumentParser(); p.add_argument("--pr", required=True)
    p.add_argument("--demo", action="store_true", help="Offline fixtures; no API calls")
    args = p.parse_args()

    console.rule("[bold]Exercise 3 — escalation with reviewer Q&A[/bold]")
    console.print(f"[dim]PR: {args.pr}[/dim]\n")

    app = build_graph()
    thread_id = str(uuid.uuid4())
    cfg = {"configurable": {"thread_id": thread_id}}
    console.print(f"[dim]thread_id = {thread_id}[/dim]\n")

    result = app.invoke({"pr_url": args.pr, "thread_id": thread_id, "demo_mode": args.demo}, cfg)
    while result.get("__interrupt__"):
        result = app.invoke(Command(resume=handle_interrupt(result["__interrupt__"][0].value)), cfg)

    console.rule("Final")
    console.print(f"final_action = {result.get('final_action')}")
    if "analysis" in result:
        console.print(f"final confidence = {result['analysis'].confidence:.0%}")


if __name__ == "__main__":
    main()
