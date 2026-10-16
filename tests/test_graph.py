"""Offline integration tests: real LangGraph, interrupts, SQLite and mocked APIs."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from langgraph.types import Command

from audit.replay import recent_threads
from common.db import replay_events
from common.github import fetch_pr, parse_pr_url, post_review_comment
from common.schemas import PRAnalysis
from exercises import exercise_1_confidence as ex1
from exercises import exercise_2_hitl as ex2
from exercises import exercise_3_escalation as ex3
from exercises.exercise_4_audit import drive_graph
from exercises.review_helpers import analyze_review_async, fetch_review_pr


PR = "https://github.com/VinUni-AI20k/PR-Demo/pull/"


class EarlyExerciseTests(unittest.TestCase):
    def test_exercise_1_routes_all_three_prs(self):
        for number, expected in [(1, "human_approval"), (2, "escalate"), (3, "auto_approve")]:
            with self.subTest(number=number):
                result = ex1.build_graph().invoke({"pr_url": PR + str(number), "demo_mode": True})
                self.assertEqual(result["decision"], expected)

    def test_exact_thresholds_in_compiled_graph(self):
        for confidence, expected in [(0.5799, "escalate"), (0.58, "human_approval"),
                                     (0.7299, "human_approval"), (0.73, "auto_approve")]:
            analysis = PRAnalysis(summary="review", confidence=confidence, confidence_reasoning="test")
            with self.subTest(confidence=confidence), patch.object(ex1, "analyze_review", return_value=analysis):
                result = ex1.build_graph().invoke({"pr_url": PR + "1", "demo_mode": True})
                self.assertEqual(result["decision"], expected)

    def test_exercise_2_interrupt_edit_then_approve(self):
        app = ex2.build_graph()
        cfg = {"configurable": {"thread_id": "ex2"}}
        result = app.invoke({"pr_url": PR + "1", "demo_mode": True}, cfg)
        self.assertEqual(result["__interrupt__"][0].value["kind"], "approval_request")
        result = app.invoke(Command(resume={"choice": "edit", "feedback": "Mention migration tests"}), cfg)
        self.assertIn("Mention migration tests", result["analysis"].summary)
        self.assertTrue(result.get("__interrupt__"))
        result = app.invoke(Command(resume={"choice": "approve"}), cfg)
        self.assertEqual(result["final_action"], "demo_committed")

    def test_exercise_3_requires_final_approval(self):
        app = ex3.build_graph()
        cfg = {"configurable": {"thread_id": "ex3"}}
        result = app.invoke({"pr_url": PR + "2", "demo_mode": True}, cfg)
        payload = result["__interrupt__"][0].value
        self.assertEqual(payload["kind"], "escalation")
        result = app.invoke(Command(resume={q: "Add tests before release" for q in payload["questions"]}), cfg)
        self.assertEqual(result["__interrupt__"][0].value["kind"], "approval_request")
        result = app.invoke(Command(resume={"choice": "reject"}), cfg)
        self.assertEqual(result["final_action"], "rejected")


class DurableGraphTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"HITL_DB_PATH": str(Path(self.temp.name) / "audit.db")})
        self.env.start()
        # Any accidental network call fails the offline suite immediately.
        self.network = patch.object(httpx.Client, "send", side_effect=AssertionError("Unexpected network call"))
        self.network.start()

    async def asyncTearDown(self):
        self.network.stop()
        self.env.stop()
        self.temp.cleanup()

    async def start(self, thread="test", number=1):
        return await drive_graph(thread, mode="start", pr_url=PR + str(number), demo_mode=True)

    async def test_checkpoint_reopen_and_audit_no_duplicate_requests(self):
        result = await self.start()
        initial = await replay_events("test")
        for _ in range(2):
            result = await drive_graph("test")
            self.assertEqual(result["__interrupt__"][0].value["kind"], "approval_request")
        self.assertEqual(len(await replay_events("test")), len(initial))
        result = await drive_graph("test", mode="resume", resume_value={"choice": "approve"}, reviewer_id="tung")
        self.assertEqual(result["final_action"], "demo_committed")
        self.assertFalse(result["__pending__"])
        events = await replay_events("test")
        self.assertEqual(sum(e["action"] == "human_approval_requested" for e in events), 1)
        human = next(e for e in events if e["action"] == "human_approval")
        self.assertEqual(human["reviewer_id"], "tung")
        self.assertEqual(human["decision"], "approve")
        final = await drive_graph("test")
        self.assertEqual(final["final_action"], "demo_committed")
        self.assertEqual(len(await replay_events("test")), len(events))

    async def test_reject_never_posts(self):
        await self.start()
        result = await drive_graph("test", mode="resume", resume_value={"choice": "reject", "feedback": "Need tests"})
        self.assertEqual(result["final_action"], "rejected")
        self.assertIsNone(result["posted_comment_body"])

    async def test_continue_after_analysis_failure_keeps_fetch_checkpoint(self):
        with patch("exercises.exercise_4_audit.analyze_review_async",
                   new=AsyncMock(side_effect=RuntimeError("Provider interrupted"))):
            with self.assertRaises(RuntimeError):
                await self.start()
        saved = await drive_graph("test")
        self.assertEqual(saved["__pending__"], ("analyze",))
        self.assertNotIn("__interrupt__", saved)
        with patch("exercises.exercise_4_audit.fetch_review_pr",
                   side_effect=AssertionError("Fetch must not restart")):
            result = await drive_graph("test", mode="continue")
        self.assertEqual(result["__interrupt__"][0].value["kind"], "approval_request")
        self.assertEqual(sum(e["action"] == "fetch_pr" for e in await replay_events("test")), 1)

    async def test_escalation_approve_uses_refined_analysis_and_audit(self):
        result = await self.start(number=2)
        questions = result["__interrupt__"][0].value["questions"]
        await drive_graph("test", mode="resume", resume_value={q: "Migration and tests before release" for q in questions})
        result = await drive_graph("test", mode="resume", resume_value={"choice": "approve"}, reviewer_id="reviewer")
        self.assertEqual(result["final_action"], "demo_committed")
        self.assertIn("Reviewer context received", result["posted_comment_body"])
        self.assertIn("Migration and tests before release", result["posted_comment_body"])
        events = await replay_events("test")
        synthesis = next(e for e in events if e["action"] == "synthesize")
        self.assertEqual(synthesis["confidence"], result["analysis"].confidence)
        self.assertEqual(events[-1]["decision"], "approve")
        self.assertEqual(events[-1]["reviewer_id"], "reviewer")

    async def test_edit_needs_instructions_then_requires_approval(self):
        await self.start()
        with self.assertRaises(ValueError):
            await drive_graph("test", mode="resume", resume_value={"choice": "edit", "feedback": ""})
        result = await drive_graph("test", mode="resume", resume_value={"choice": "edit", "feedback": "Add migration test"})
        self.assertIn("Add migration test", result["analysis"].summary)
        self.assertEqual(result["revision"], 1)
        self.assertTrue(result.get("__interrupt__"))
        self.assertIsNone(result.get("final_action"))
        result = await drive_graph("test", mode="resume", resume_value={"choice": "approve"})
        self.assertEqual(result["final_action"], "demo_committed")

    async def test_escalation_q_and_a_then_final_rejection(self):
        result = await self.start(number=2)
        questions = result["__interrupt__"][0].value["questions"]
        with self.assertRaises(ValueError):
            await drive_graph("test", mode="resume", resume_value={})
        result = await drive_graph("test", mode="resume", resume_value={q: "Context supplied" for q in questions})
        self.assertEqual(result["__interrupt__"][0].value["kind"], "approval_request")
        self.assertIsNone(result.get("final_action"))
        result = await drive_graph("test", mode="resume", resume_value={"choice": "reject"})
        self.assertEqual(result["final_action"], "rejected")
        events = await replay_events("test")
        self.assertEqual(sum(e["action"] == "escalation_requested" for e in events), 1)
        rows = await recent_threads()
        self.assertEqual(rows[0]["worst_risk"], "high")

    async def test_auto_branch_and_stale_resume_do_not_repost(self):
        result = await self.start(number=3)
        self.assertEqual(result["final_action"], "demo_auto_committed")
        with self.assertRaises(ValueError):
            await drive_graph("test", mode="resume", resume_value={"choice": "approve"})
        with self.assertRaises(ValueError):
            await self.start()

    async def test_live_api_failure_is_recorded_as_failed(self):
        # Real (non-demo) graph path with fake provider responses.
        pr = fetch_review_pr({"pr_url": PR + "3", "demo_mode": True})
        a = PRAnalysis(summary="Typo", confidence=0.9, confidence_reasoning="Mechanical change")
        with patch("exercises.exercise_4_audit.fetch_review_pr", return_value=pr), \
             patch("exercises.exercise_4_audit.analyze_review_async", new=AsyncMock(return_value=a)), \
             patch("exercises.exercise_4_audit.post_review_comment", side_effect=RuntimeError("GitHub unavailable")):
            result = await drive_graph("test", mode="start", pr_url=PR + "3")
        self.assertEqual(result["final_action"], "commit_failed")
        self.assertIn("GitHub unavailable", result["error"])
        self.assertEqual((await replay_events("test"))[-1]["decision"], "failed")

    async def test_live_success_has_direct_comment_link(self):
        pr = fetch_review_pr({"pr_url": PR + "1", "demo_mode": True})
        a = PRAnalysis(summary="Priority", confidence=0.65, confidence_reasoning="Missing migration")
        with patch("exercises.exercise_4_audit.fetch_review_pr", return_value=pr), \
             patch("exercises.exercise_4_audit.analyze_review_async", new=AsyncMock(return_value=a)), \
             patch("exercises.exercise_4_audit.post_review_comment", return_value=PR + "1#issuecomment-123") as post:
            await drive_graph("test", mode="start", pr_url=PR + "1")
            result = await drive_graph("test", mode="resume", resume_value={"choice": "approve"})
        self.assertEqual(result["final_action"], "committed")
        self.assertEqual(result["posted_comment_url"], PR + "1#issuecomment-123")
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["idempotency_key"], "test:0")

    async def test_checkpoint_survives_new_python_process(self):
        await self.start()
        script = (
            "import asyncio; from exercises.exercise_4_audit import drive_graph; "
            "r=asyncio.run(drive_graph('test', mode='resume', resume_value={'choice':'approve'})); "
            "print(r['final_action'])"
        )
        proc = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script],
                                       capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("demo_committed", proc.stdout)
        self.assertEqual((await drive_graph("test"))["final_action"], "demo_committed")

    async def test_structured_provider_receives_diff_and_qa(self):
        a = PRAnalysis(summary="Review", confidence=0.65, confidence_reasoning="Tests needed")
        model = MagicMock()
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=a)
        state = {"pr_title": "Task", "pr_diff": "sample diff", "analysis": a,
                 "escalation_answers": {"Migration?": "Backfill first"}}
        with patch("exercises.review_helpers.get_llm", return_value=model):
            await analyze_review_async(state, "synthesize")
        model.with_structured_output.assert_called_once_with(PRAnalysis)
        messages = model.with_structured_output.return_value.ainvoke.call_args.args[0]
        self.assertIn("sample diff", messages[1]["content"])
        self.assertIn("Backfill first", messages[1]["content"])


class GitHubTests(unittest.TestCase):
    def test_fetch_reads_all_file_pages_and_diff(self):
        base = "https://api.github.com/repos/VinUni-AI20k/PR-Demo/pulls/1"

        def handler(request):
            url = str(request.url)
            if "/files" in url:
                if "page=2" in url:
                    return httpx.Response(200, json=[{"filename": "b.py"}])
                return httpx.Response(200, json=[{"filename": "a.py"}],
                                      headers={"Link": f'<{base}/files?page=2>; rel="next"'})
            if request.headers["Accept"] == "application/vnd.github.v3.diff":
                return httpx.Response(200, text="diff --git a/a.py b/a.py")
            return httpx.Response(200, json={"title": "Task priority", "user": {"login": "author"},
                                            "base": {"ref": "main"}, "head": {"ref": "feature", "sha": "a" * 40}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-test-token"}), \
             patch("common.github.httpx.Client", return_value=client):
            pr = fetch_pr(PR + "1")
        self.assertEqual(pr.files_changed, ["a.py", "b.py"])
        self.assertIn("diff --git", pr.diff)
        self.assertEqual(pr.head_sha, "a" * 40)

    def test_post_retry_reuses_comment_from_later_page(self):
        marker = hashlib.sha256(b"thread:0").hexdigest()
        comments_url = "https://api.github.com/repos/VinUni-AI20k/PR-Demo/issues/1/comments"
        requests = []

        def handler(request):
            requests.append(request)
            if "page=2" in str(request.url):
                return httpx.Response(200, json=[{"body": f"<!-- hitl-review:{marker} -->", "html_url": "https://github.com/comment/1"}])
            return httpx.Response(200, json=[], headers={"Link": f'<{comments_url}?page=2>; rel="next"'})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-test-token"}), \
             patch("common.github.httpx.Client", return_value=client):
            url = post_review_comment(PR + "1", "body", idempotency_key="thread:0")
        self.assertEqual(url, "https://github.com/comment/1")
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request.method == "GET" for request in requests))

    def test_post_returns_new_comment_url_and_stable_marker(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200 if request.method == "GET" else 201,
                                  json=[] if request.method == "GET" else {"html_url": "https://github.com/comment/2"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-test-token"}), \
             patch("common.github.httpx.Client", return_value=client):
            url = post_review_comment(PR + "1", "review body", idempotency_key="thread:0")
        self.assertEqual(url, "https://github.com/comment/2")
        self.assertIn("hitl-review:", json.loads(requests[-1].content)["body"])

    def test_pr_url_validation(self):
        self.assertEqual(parse_pr_url(PR + "1/files?x=1")[2], 1)
        for value in ["invalid", "https://example.com/github.com/a/b/pull/1", PR + "0", PR + "1abc"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_pr_url(value)


if __name__ == "__main__":
    unittest.main()
