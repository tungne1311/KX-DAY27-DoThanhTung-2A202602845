"""Exercise the real Streamlit widgets without starting external API calls."""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from streamlit.testing.v1 import AppTest
from common.schemas import PRAnalysis
from exercises.exercise_4_audit import drive_graph
from exercises.review_helpers import fetch_review_pr


APP = Path(__file__).resolve().parents[1] / "app.py"
PR = "https://github.com/VinUni-AI20k/PR-Demo/pull/"


class ApprovalUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"HITL_DB_PATH": str(Path(self.temp.name) / "ui.db")})
        self.env.start()
        self.network = patch.object(httpx.Client, "send", side_effect=AssertionError("Unexpected network call"))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.env.stop()
        self.temp.cleanup()

    def click(self, app, label):
        next(button for button in app.button if button.label == label).click().run()
        self.assertFalse(app.exception, str(app.exception))

    def start(self, number=1):
        app = AppTest.from_file(APP, default_timeout=30).run()
        self.assertFalse(app.exception, str(app.exception))
        next(item for item in app.text_input if item.label == "PR URL").set_value(PR + str(number))
        self.click(app, "Run review")
        return app

    def test_approve_and_refresh_restore_final_session(self):
        app = self.start()
        self.assertIn("Approval requested", [item.value for item in app.subheader])
        thread = app.session_state["thread_id"]
        self.click(app, "Approve")
        self.assertEqual(app.session_state["result"]["final_action"], "demo_committed")
        fresh = AppTest.from_file(APP, default_timeout=30)
        fresh.query_params["thread"] = thread
        fresh.run()
        self.assertFalse(fresh.exception, str(fresh.exception))
        self.assertEqual(fresh.session_state["result"]["final_action"], "demo_committed")

    def test_edit_validation_revision_then_reject(self):
        app = self.start()
        self.click(app, "Edit")
        self.assertTrue(any("requires instructions" in item.value for item in app.error))
        next(item for item in app.text_area if item.label == "Feedback / edit instructions").set_value("Add migration tests")
        self.click(app, "Edit")
        self.assertEqual(app.session_state["result"]["revision"], 1)
        self.assertIn("Add migration tests", app.session_state["result"]["analysis"].summary)
        self.click(app, "Reject")
        self.assertEqual(app.session_state["result"]["final_action"], "rejected")

    def test_refresh_pending_session_restores_approval(self):
        app = self.start()
        fresh = AppTest.from_file(APP, default_timeout=30)
        fresh.query_params["thread"] = app.session_state["thread_id"]
        fresh.run()
        self.assertFalse(fresh.exception, str(fresh.exception))
        self.assertIn("Approval requested", [item.value for item in fresh.subheader])
        self.click(fresh, "Reject")
        self.assertEqual(fresh.session_state["result"]["final_action"], "rejected")

    def test_sidebar_reopens_a_different_pending_session(self):
        app = self.start()
        first_thread = app.session_state["thread_id"]
        next(item for item in app.text_input if item.label == "PR URL").set_value(PR + "2")
        self.click(app, "Run review")
        next(item for item in app.selectbox if item.label == "Saved review").set_value(first_thread)
        self.click(app, "Open session")
        self.assertEqual(app.session_state["thread_id"], first_thread)
        self.assertIn("Approval requested", [item.value for item in app.subheader])

    def test_failed_github_post_displays_error_without_success(self):
        a = PRAnalysis(summary="Typo", confidence=0.9, confidence_reasoning="Mechanical change")
        pr = fetch_review_pr({"pr_url": PR + "3", "demo_mode": True})
        with patch("exercises.exercise_4_audit.fetch_review_pr", return_value=pr), \
             patch("exercises.exercise_4_audit.analyze_review_async", new=AsyncMock(return_value=a)), \
             patch("exercises.exercise_4_audit.post_review_comment", side_effect=RuntimeError("GitHub unavailable")):
            asyncio.run(drive_graph("failed-post", mode="start", pr_url=PR + "3"))
        app = AppTest.from_file(APP, default_timeout=30)
        app.query_params["thread"] = "failed-post"
        app.run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any("could not be posted" in item.value for item in app.error))
        self.assertFalse(app.success)

    def test_escalation_answers_and_final_confirmation(self):
        app = self.start(2)
        self.click(app, "Submit answers")
        self.assertTrue(any("every escalation question" in item.value for item in app.error))
        for item in app.text_area:
            item.set_value("We will add a migration and tests before release")
        self.click(app, "Submit answers")
        self.assertIn("Confirm refined review", [item.value for item in app.subheader])
        self.assertIsNone(app.session_state["result"].get("final_action"))
        self.click(app, "Approve")
        self.assertEqual(app.session_state["result"]["final_action"], "demo_committed")

    def test_auto_review_has_no_approval_buttons(self):
        app = self.start(3)
        self.assertEqual(app.session_state["result"]["final_action"], "demo_auto_committed")
        self.assertNotIn("Approve", [button.label for button in app.button])


if __name__ == "__main__":
    unittest.main()
