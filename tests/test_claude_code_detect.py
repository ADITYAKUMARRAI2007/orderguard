"""Detecting already-connected Claude Code MCP servers via `claude mcp list`
— read-only status parsing, never credential extraction. Subprocess calls
are mocked so this suite stays offline like everything else in this project.
"""

import subprocess
from unittest.mock import patch

from orderguard.agent.claude_code_detect import detect_claude_code_connectors

_REAL_SAMPLE_OUTPUT = """Checking MCP server health…

claude.ai Kiwi.com: https://mcp.kiwi.com - ✘ Failed to connect
claude.ai Razorpay: https://mcp.razorpay.com/mcp - ! Connected · tools fetch failed
swiggy-instamart: https://mcp.swiggy.com/im (HTTP) - ✔ Connected
swiggy-food: https://mcp.swiggy.com/food (HTTP) - ✔ Connected
swiggy-dineout: https://mcp.swiggy.com/dineout (HTTP) - ✔ Connected
"""


def _run(stdout="", returncode=0, side_effect=None):
    if side_effect:
        return patch("subprocess.run", side_effect=side_effect)
    result = subprocess.CompletedProcess(args=["claude", "mcp", "list"], returncode=returncode, stdout=stdout, stderr="")
    return patch("subprocess.run", return_value=result)


def test_parses_real_sample_output_correctly():
    with _run(stdout=_REAL_SAMPLE_OUTPUT):
        connectors, error = detect_claude_code_connectors()
    assert error == ""
    by_name = {c.name: c for c in connectors}
    assert by_name["swiggy-instamart"].connected is True
    assert by_name["swiggy-instamart"].cli_managed is True
    assert by_name["claude.ai Kiwi.com"].connected is False
    assert by_name["claude.ai Kiwi.com"].cli_managed is False
    assert by_name["claude.ai Razorpay"].connected is False  # "!" is not "connected"


def test_inference_auth_vars_are_stripped_before_calling_the_cli():
    """Regression: the CLI itself refuses to list claude.ai connectors when
    ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL/CLAUDE_CODE_OAUTH_TOKEN are present
    in its environment — a real incident where a live backend silently lost
    8 of 11 detected connectors because it inherited one of these from its
    own process env (e.g. a BYOK session)."""
    captured = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=_REAL_SAMPLE_OUTPUT, stderr="")

    with patch.dict(
        "os.environ",
        {
            "CLAUDE_CODE_OAUTH_TOKEN": "some-token",
            "ANTHROPIC_API_KEY": "sk-ant-something",
            "ANTHROPIC_AUTH_TOKEN": "also-something",
            "ANTHROPIC_BASE_URL": "https://proxy.example.com",
        },
    ):
        with patch("subprocess.run", side_effect=fake_run):
            detect_claude_code_connectors()

    for var in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        assert var not in captured["env"]


def test_missing_cli_is_reported_not_silently_empty():
    with _run(side_effect=FileNotFoundError()):
        connectors, error = detect_claude_code_connectors()
    assert connectors == []
    assert "PATH" in error


def test_timeout_is_reported():
    with _run(side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=15)):
        connectors, error = detect_claude_code_connectors()
    assert connectors == []
    assert "timed out" in error


def test_nonzero_exit_is_reported():
    with _run(returncode=1):
        connectors, error = detect_claude_code_connectors()
    assert connectors == []
    assert "exited 1" in error


def test_unparseable_lines_are_skipped_not_fatal():
    with _run(stdout="Checking MCP server health…\nsome garbage line\n"):
        connectors, error = detect_claude_code_connectors()
    assert connectors == []
    assert error == ""
