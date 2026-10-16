"""Exercise 4: durable HITL graph with structured audit and crash recovery.

Request events live in separate nodes before interrupts: resuming an interrupt
replays its node, so an audit before interrupt() in that node would duplicate rows.
Both terminal and Streamlit clients use drive_graph() to reopen checkpoints.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid

from dotenv import load_dotenv
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from rich.console import Console

from common.db import db_path, write_audit_event
from common.github import parse_pr_url, post_review_comment
from common.schemas import AuditEntry, risk_level_for
from common.terminal import create_console
from exercises.exercise_3_escalation import handle_interrupt
from exercises.review_helpers import (
    LabState, analyze_review_async, approval_payload, escalation_payload,
    fetch_review_pr, render_comment_body, route_decision,
    validate_answers, validate_approval,
)


console = create_console()
AGENT_ID = "pr-review-agent@v0.1"


async def audit(state: LabState, entry: AuditEntry) -> None:
    await write_audit_event(thread_id=state["thread_id"], pr_url=state["pr_url"], entry=entry)


async def log_event(state, action, decision, t0, *, reason=None, analysis=None, human=False):
    a = analysis or state.get("analysis")
    confidence = a.confidence if a else 0.0
    await audit(state, AuditEntry(
        agent_id=AGENT_ID, action=action, confidence=confidence,
        risk_level=risk_level_for(confidence) if a else "med",
        reviewer_id=(state.get("reviewer_id") or os.environ.get("GITHUB_USER")) if human else None,
        decision=decision, reason=reason,
        execution_time_ms=int((time.monotonic() - t0) * 1000),
    ))


async def node_fetch_pr(state):
    t0 = time.monotonic()
    pr = await asyncio.to_thread(fetch_review_pr, state)
    await log_event(state, "fetch_pr", "pending", t0,
                    reason=f"Fetched {len(pr.files_changed)} files, head={pr.head_sha[:7]}"
                           + (" (offline demo)" if state.get("demo_mode") else ""))
    return {"pr_title": pr.title, "pr_author": pr.author, "pr_diff": pr.diff,
            "pr_files": pr.files_changed, "pr_head_sha": pr.head_sha}


async def node_analyze(state):
    t0 = time.monotonic()
    a = await analyze_review_async(state)
    await log_event(state, "analyze", "pending", t0, analysis=a, reason=a.confidence_reasoning)
    return {"analysis": a, "revision": 0}


async def node_route(state):
    t0 = time.monotonic()
    decision = route_decision(state["analysis"].confidence)
    audit_decision = {"auto_approve": "auto", "human_approval": "pending", "escalate": "escalate"}[decision]
    await log_event(state, "route", audit_decision, t0,
                    reason=f"{decision}: {state['analysis'].confidence:.2%}")
    return {"decision": decision}


async def node_request_approval(state):
    await log_event(state, "human_approval_requested", "pending", time.monotonic(),
                    reason=f"Awaiting approval of revision {state.get('revision', 0)}")
    return {}


async def node_human_approval(state):
    t0 = time.monotonic()
    update = validate_approval(interrupt(approval_payload(state)))
    await log_event(state, "human_approval", update["human_choice"], t0,
                    reason=update["human_feedback"] or f"Reviewer chose {update['human_choice']}.", human=True)
    return update


async def node_edit_review(state):
    t0 = time.monotonic()
    a = await analyze_review_async(state, "edit")
    await log_event(state, "edit_review", "edit", t0, analysis=a,
                    reason=state["human_feedback"], human=True)
    return {"analysis": a, "human_choice": None, "revision": state.get("revision", 0) + 1}


async def node_request_escalation(state):
    await log_event(state, "escalation_requested", "pending", time.monotonic(),
                    reason=json.dumps(escalation_payload(state)["questions"], ensure_ascii=False))
    return {}


async def node_escalate(state):
    t0 = time.monotonic()
    payload = escalation_payload(state)
    answers = validate_answers(interrupt(payload), payload["questions"])
    await log_event(state, "escalate", "escalate", t0,
                    reason=json.dumps(answers, ensure_ascii=False), human=True)
    return {"escalation_answers": answers}


async def node_synthesize(state):
    t0 = time.monotonic()
    a = await analyze_review_async(state, "synthesize")
    await log_event(state, "synthesize", "pending", t0, analysis=a, reason=a.confidence_reasoning)
    return {"analysis": a, "human_choice": None, "revision": state.get("revision", 0) + 1}


async def post_review(state):
    body = render_comment_body(state)
    if state.get("demo_mode"):
        return {"final_action": "demo_committed", "posted_comment_body": body,
                "posted_comment_url": None, "error": None}
    try:
        url = await asyncio.to_thread(
            post_review_comment, state["pr_url"], body,
            idempotency_key=f"{state['thread_id']}:{state.get('revision', 0)}",
        )
    except Exception as exc:
        return {"final_action": "commit_failed", "posted_comment_body": None,
                "posted_comment_url": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"final_action": "committed", "posted_comment_body": body,
            "posted_comment_url": url, "error": None}


async def node_commit(state):
    t0 = time.monotonic()
    if state.get("human_choice") == "approve":
        result = await post_review(state)
        decision = "failed" if result["final_action"] == "commit_failed" else "approve"
    else:
        result = {"final_action": "rejected", "posted_comment_body": None,
                  "posted_comment_url": None, "error": None}
        decision = "reject"
    await log_event(state, "commit", decision, t0,
                    reason=result.get("error") or result["final_action"], human=True)
    return result


async def node_auto_approve(state):
    t0 = time.monotonic()
    result = await post_review(state)
    if result["final_action"] == "committed":
        result["final_action"] = "auto_committed"
    elif result["final_action"] == "demo_committed":
        result["final_action"] = "demo_auto_committed"
    await log_event(state, "auto_approve",
                    "failed" if result["final_action"] == "commit_failed" else "auto", t0,
                    reason=result.get("error") or result["final_action"])
    return result


def build_graph(checkpointer):
    g = StateGraph(LabState)
    nodes = {
        "fetch_pr": node_fetch_pr, "analyze": node_analyze, "route": node_route,
        "auto_approve": node_auto_approve, "request_approval": node_request_approval,
        "human_approval": node_human_approval, "edit_review": node_edit_review,
        "request_escalation": node_request_escalation, "escalate": node_escalate,
        "synthesize": node_synthesize, "commit": node_commit,
    }
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_edge(START, "fetch_pr")
    g.add_edge("fetch_pr", "analyze")
    g.add_edge("analyze", "route")
    g.add_conditional_edges("route", lambda s: s["decision"], {
        "auto_approve": "auto_approve", "human_approval": "request_approval",
        "escalate": "request_escalation",
    })
    g.add_edge("request_approval", "human_approval")
    g.add_conditional_edges("human_approval",
                            lambda s: "edit_review" if s["human_choice"] == "edit" else "commit")
    g.add_edge("edit_review", "request_approval")
    g.add_edge("request_escalation", "escalate")
    g.add_edge("escalate", "synthesize")
    g.add_edge("synthesize", "request_approval")
    g.add_edge("commit", END)
    g.add_edge("auto_approve", END)
    return g.compile(checkpointer=checkpointer)


def snapshot_result(snapshot) -> dict:
    result = dict(snapshot.values)
    interrupts = tuple(i for task in snapshot.tasks for i in task.interrupts)
    if interrupts:
        result["__interrupt__"] = interrupts
    result["__pending__"] = tuple(snapshot.next)
    return result


async def drive_graph(thread_id: str, *, mode="load", pr_url=None, demo_mode=False,
                      resume_value=None, reviewer_id=None) -> dict:
    """Start, inspect, resume or continue a saved session without restarting it."""
    serde = JsonPlusSerializer(allowed_msgpack_modules=[("common.schemas", "PRAnalysis"),
                                                       ("common.schemas", "ReviewComment")])
    async with AsyncSqliteSaver.from_conn_string(db_path()) as cp:
        cp.serde = serde
        await cp.setup()
        app = build_graph(cp)
        cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
        snapshot = await app.aget_state(cfg)
        previous = snapshot_result(snapshot)
        if mode == "start":
            if snapshot.values:
                raise ValueError("This thread already exists. Open it instead of restarting it.")
            parse_pr_url(pr_url or "")
            await app.ainvoke({"pr_url": pr_url, "thread_id": thread_id,
                               "demo_mode": bool(demo_mode), "reviewer_id": reviewer_id}, cfg)
        elif not snapshot.values:
            raise ValueError("No saved session exists for this thread.")
        elif mode == "resume":
            if not previous.get("__interrupt__"):
                raise ValueError("This session is not waiting for a response. Reopen its latest state.")
            payload = previous["__interrupt__"][0].value
            if payload["kind"] == "approval_request":
                validate_approval(resume_value)
            else:
                validate_answers(resume_value, payload["questions"])
            await app.ainvoke(Command(resume=resume_value,
                                      update={"reviewer_id": reviewer_id or snapshot.values.get("reviewer_id")}), cfg)
        elif mode == "continue":
            if previous.get("__interrupt__"):
                raise ValueError("Answer the pending review request before continuing.")
            if snapshot.next:
                await app.ainvoke(None, cfg)
        elif mode != "load":
            raise ValueError(f"Unknown session action: {mode}")
        return snapshot_result(await app.aget_state(cfg))


async def run(pr_url: str | None, thread_id: str | None, demo_mode=False):
    existing = thread_id is not None
    thread_id = thread_id or str(uuid.uuid4())
    console.print(f"thread_id = {thread_id}")
    result = await drive_graph(thread_id, mode="load" if existing else "start",
                               pr_url=pr_url, demo_mode=demo_mode,
                               reviewer_id=os.environ.get("GITHUB_USER"))
    while result.get("__pending__"):
        if result.get("__interrupt__"):
            answer = handle_interrupt(result["__interrupt__"][0].value)
            result = await drive_graph(thread_id, mode="resume", resume_value=answer,
                                       reviewer_id=os.environ.get("GITHUB_USER"))
        else:
            result = await drive_graph(thread_id, mode="continue")
    console.print(f"final_action = {result.get('final_action')}")
    if result.get("error"):
        console.print(result["error"], markup=False)
    console.print(f"Replay: uv run python -m audit.replay --thread {thread_id}")
    return result


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", help="PR URL for a new session")
    parser.add_argument("--thread", help="Reopen an existing session (no new PR fetch)")
    parser.add_argument("--demo", action="store_true", help="Offline fixtures; no API calls")
    args = parser.parse_args()
    if not args.pr and not args.thread:
        parser.error("Use --pr for a new review or --thread to resume.")
    asyncio.run(run(args.pr, args.thread, args.demo))


if __name__ == "__main__":
    main()
