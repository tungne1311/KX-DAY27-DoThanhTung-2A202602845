# Validation — 16/10/2026

Environment: Windows, Python 3.12.10, LangGraph 1.2.0, Streamlit 1.66.0.

| Check | Result |
|---|---|
| `python -m uv sync --locked` | Passed; 79 packages checked |
| `.venv\Scripts\python.exe -m unittest discover -s tests -v` | **26 tests passed** |
| `python -m uv build --wheel --out-dir scratch\dist` | Passed; required modules and `audit/schema.sql` included |
| Compile Python sources | Passed |
| Exercise 1 direct script invocation with `--demo` | Human approval at 65% |
| SQLite audit list and replay CLI | Passed |
| Streamlit HTTP `/_stcore/health` | `ok` on `127.0.0.1:8501` |
| `git diff --check` | Passed |

Tests cover graph routing including boundary values, approve/reject/edit,
escalation questions and final approval, reopening checkpoints in a new Python
process, audit events, provider prompt context, mocked GitHub success/failure,
comment deduplication across paginated responses and seven Streamlit UI tests.
Additional submission checks cover continuing after analysis failure without
fetching the PR again, refined-review approval, all PR file pages, refreshing a
pending review, sidebar session switching and displaying GitHub errors correctly.

The local `.env` now reuses the OpenRouter credential from the Day19 lab and the
existing authenticated GitHub CLI session. GitHub `/user` and OpenRouter `/key`
both returned HTTP 200. Real PR #1 and PR #2 were fetched and reviewed through
`openai/gpt-4o-mini` with structured `PRAnalysis` output. No review comments were
published during validation. Offline fixtures remain separate from live mode.

After enabling the real credentials, the offline integration/UI suite passed
again, including all 26 tests in the final submission run. Public PR review output was used to refine the prompt's evidence and
severity calibration; live confidence values remain model-dependent.

Latest live run after calibration: PR #1 returned confidence 0.65 and
`human_approval`; PR #2 returned confidence 0.45 and `escalate` with four questions.

Three local demo sessions are available in the sidebar:

- `demo-approval-a5d6a80b`: approval request at 65%.
- `demo-escalation-adf384e1`: escalation questions at 45%.
- `demo-auto-41011512`: simulated automatic completion at 94%.

The database and local logs are git-ignored. The suite uses temporary databases.
The Streamlit width deprecations have been removed. AppTest emits a bare-mode
context notice; it does not cause application or test failures. Minimum package
versions now reflect the checkpoint serialization and Streamlit APIs used.
