"""Exercise 5: Streamlit approval UI. Run: uv run streamlit run app.py"""

from __future__ import annotations

import asyncio
import os
import uuid

import streamlit as st
from dotenv import load_dotenv

from audit.replay import recent_threads
from common.db import replay_events
from common.github import parse_pr_url
from exercises.exercise_4_audit import drive_graph
from exercises.review_helpers import validate_answers, validate_approval


def render_approval_card(payload: dict) -> dict | None:
    title = "Confirm refined review" if payload.get("after_escalation") else "Approval requested"
    st.subheader(title)
    st.metric("Review confidence", f"{payload['confidence']:.0%}")
    st.caption(payload["confidence_reasoning"])
    st.markdown(payload["summary"])
    for risk in payload.get("risk_factors", []):
        st.warning(risk)
    for c in payload.get("comments", []):
        st.markdown(f"**[{c['severity']}]** `{c['file']}:{c.get('line') or '?'}` — {c['body']}")
    with st.expander("View PR diff"):
        st.code(payload.get("diff_preview", ""), language="diff")
    suffix = f"{st.session_state.thread_id}-{payload.get('revision', 0)}"
    feedback = st.text_area("Feedback / edit instructions", key=f"feedback-{suffix}")
    col1, col2, col3 = st.columns(3)
    choice = None
    if col1.button("Approve", type="primary", key=f"approve-{suffix}"):
        choice = "approve"
    if col2.button("Reject", key=f"reject-{suffix}"):
        choice = "reject"
    if col3.button("Edit", key=f"edit-{suffix}"):
        choice = "edit"
    if choice:
        answer = {"choice": choice, "feedback": feedback}
        try:
            validate_approval(answer)
        except ValueError as exc:
            st.error(str(exc))
        else:
            return answer
    return None


def render_escalation_card(payload: dict) -> dict | None:
    st.subheader("Strong escalation — reviewer context needed")
    st.metric("Review confidence", f"{payload['confidence']:.0%}")
    st.caption(payload["confidence_reasoning"])
    st.markdown(payload["summary"])
    for risk in payload.get("risk_factors", []):
        st.error(risk)
    with st.form(f"escalation-{st.session_state.thread_id}"):
        answers = {q: st.text_area(q, key=f"answer-{st.session_state.thread_id}-{index}")
                   for index, q in enumerate(payload["questions"])}
        submitted = st.form_submit_button("Submit answers", type="primary")
    if submitted:
        try:
            return validate_answers(answers, payload["questions"])
        except ValueError as exc:
            st.error(str(exc))
    return None


async def run_graph(pr_url: str | None, thread_id: str, resume_value=None, *, mode=None,
                    demo_mode=False, reviewer_id=None):
    return await drive_graph(thread_id, mode=mode or ("resume" if resume_value is not None else "start"),
                             pr_url=pr_url, demo_mode=demo_mode,
                             resume_value=resume_value, reviewer_id=reviewer_id)


def apply_action(mode: str, **kwargs) -> bool:
    try:
        with st.spinner("Review in progress..."):
            st.session_state.result = asyncio.run(drive_graph(
                st.session_state.thread_id, mode=mode, **kwargs))
        st.session_state.last_error = None
        return True
    except Exception as exc:
        st.session_state.last_error = f"{type(exc).__name__}: {exc}"
        # Preserve any checkpoint made before the failure for a later retry.
        try:
            st.session_state.result = asyncio.run(drive_graph(st.session_state.thread_id))
        except Exception:
            pass
        return False


def open_session(thread_id: str):
    st.session_state.thread_id = thread_id
    st.session_state.result = None
    st.query_params["thread"] = thread_id
    apply_action("load")


def main():
    load_dotenv()
    st.set_page_config(page_title="HITL PR Review", page_icon="🔎", layout="wide")
    for key, default in {"thread_id": None, "result": None, "last_error": None}.items():
        if key not in st.session_state:
            st.session_state[key] = default

    # Query parameters survive a browser refresh; state is reconstructed from SQLite.
    query_thread = st.query_params.get("thread")
    if query_thread and query_thread != st.session_state.thread_id:
        open_session(query_thread)

    with st.sidebar:
        st.title("Review workspace")
        reviewer = st.text_input("Reviewer GitHub username", value=os.environ.get("GITHUB_USER", ""))
        st.caption("Recorded in the audit trail for your review actions.")
        st.divider()
        st.subheader("Recent sessions")
        try:
            rows = asyncio.run(recent_threads())
        except Exception as exc:
            rows = []
            st.error(f"Cannot read sessions: {exc}")
        if rows:
            mapping = {row["thread_id"]: row for row in rows}
            selected = st.selectbox("Saved review", list(mapping), format_func=lambda key:
                                    f"{key[:8]} · {mapping[key]['worst_risk']} · {mapping[key]['pr_url'].split('/')[-1]}")
            if st.button("Open session", width="stretch"):
                open_session(selected)
                st.rerun()
            st.dataframe([{k: row[k] for k in ("thread_id", "pr_url", "worst_risk", "last_event")}
                          for row in rows], hide_index=True, width="stretch")
        else:
            st.caption("No review sessions yet.")
        manual_thread = st.text_input("Open by thread ID")
        if st.button("Load thread") and manual_thread.strip():
            open_session(manual_thread.strip())
            st.rerun()

    st.title("HITL PR Review Agent")
    st.caption("Analyze a pull request, review the findings, and keep a replayable decision trail.")
    with st.form("start"):
        pr_url = st.text_input("PR URL", value="https://github.com/VinUni-AI20k/PR-Demo/pull/1")
        demo = st.checkbox("Offline demo — sample data, no API calls or GitHub posts", value=True)
        st.caption("Demo: PR #1 → approval · #2 → escalation · #3 → automatic review.")
        submitted = st.form_submit_button("Run review", type="primary")
    if submitted:
        try:
            parse_pr_url(pr_url.strip())
            if not demo:
                missing = [key for key in ("OPENROUTER_API_KEY", "GITHUB_TOKEN")
                           if not os.environ.get(key) or os.environ[key].endswith("...")]
                if missing:
                    raise ValueError("Set these values in .env first: " + ", ".join(missing))
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.result = None
            st.query_params["thread"] = st.session_state.thread_id
            apply_action("start", pr_url=pr_url.strip(), demo_mode=demo, reviewer_id=reviewer.strip() or None)
            st.rerun()

    if st.session_state.last_error:
        st.error(st.session_state.last_error)
    result = st.session_state.result
    if not result:
        st.info("Start a review above, or open a saved session in the sidebar.")
        return
    st.divider()
    if result.get("demo_mode"):
        st.info("OFFLINE DEMO: results use local sample data. No comment is posted to GitHub.")
    st.caption(f"Session: {st.session_state.thread_id}")
    st.markdown(f"PR: [{result['pr_url']}]({result['pr_url']})")
    if result.get("__interrupt__"):
        payload = result["__interrupt__"][0].value
        answer = (render_approval_card(payload) if payload["kind"] == "approval_request"
                  else render_escalation_card(payload))
        if answer is not None:
            apply_action("resume", resume_value=answer, reviewer_id=reviewer.strip() or None)
            st.rerun()
    elif result.get("__pending__"):
        st.warning("This session stopped before completion. Continue from its saved checkpoint.")
        if st.button("Continue saved session"):
            apply_action("continue")
            st.rerun()
    else:
        action = result.get("final_action")
        if action in {"committed", "auto_committed"}:
            st.success("Review comment posted to GitHub.")
            if result.get("posted_comment_url"):
                st.link_button("View comment on GitHub", result["posted_comment_url"])
        elif action in {"demo_committed", "demo_auto_committed"}:
            st.success("Demo review complete — posting simulated.")
        elif action == "rejected":
            st.warning("Review rejected. No comment posted.")
        elif action == "commit_failed":
            st.error("GitHub comment could not be posted: " + (result.get("error") or "Unknown error"))
        a = result.get("analysis")
        if a:
            st.metric("Final confidence", f"{a.confidence:.0%}")
            st.markdown(a.summary)
        if result.get("posted_comment_body"):
            with st.expander("Review comment", expanded=True):
                st.markdown(result["posted_comment_body"])
    with st.expander("Session audit trail"):
        st.dataframe(asyncio.run(replay_events(st.session_state.thread_id)),
                     hide_index=True, width="stretch")


if __name__ == "__main__":
    main()
