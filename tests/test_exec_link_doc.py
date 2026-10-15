"""Issue #109: exec(action=link) promised inline rc+output, but a command
taking ~5-10 s silently returned a 'job #N running' placeholder and its
result arrived on the NEXT turn as a job event -- an undocumented contract
switch (the caller learned the threshold only from the placeholder text).
The tool description now states the inline deadline and how the fallback
arrives, and drops the stale 15s timeout it no longer matches."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import jobs  # noqa: E402
from membrane.mcp_server import TOOLS  # noqa: E402


def _desc(name):
    return next(t["description"] for t in TOOLS if t["name"] == name)


def test_entity_link_docs_the_job_fallback():
    d = _desc("entity_link")
    # the slow-run fallback is named, with its inline deadline matching the
    # jobs constant (the threshold the caller previously had to infer)
    assert "job" in d
    assert f"{int(jobs.DEADLINE_S)}s" in d
    # the per-command run budget (EXEC_TIMEOUT_S) is stated, not the stale 15s
    assert "20s" in d
    assert "15s" not in d


def test_entity_link_still_promises_inline_for_fast_commands():
    d = _desc("entity_link")
    assert "inline" in d
    assert "SAME tool response" in d
