"""A user-paste-a-URL feature is a textbook SSRF vector. These are the real
attack payloads a custom-connector registration must survive, not just the
happy path.

DNS resolution is mocked for the two tests that would otherwise depend on
live network — matching this project's own rule (`make test-offline`) that
the suite passes with no network at all. Every other test here resolves a
literal IP address directly (no DNS query involved) or is a pure string
check, so they need no mock.
"""

import socket
from unittest.mock import patch

import pytest

from orderguard.agent.ssrf_guard import SSRFRejected, assert_no_cross_host_redirect, assert_safe_url


def test_a_normal_https_url_is_accepted():
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, None, None, "", ("140.82.112.1", 0))]):
        assert_safe_url("https://api.githubcopilot.com/mcp/")


def test_plain_http_is_rejected():
    with pytest.raises(SSRFRejected):
        assert_safe_url("http://example.com/mcp")


def test_localhost_is_rejected_for_a_user_pasted_url():
    with pytest.raises(SSRFRejected):
        assert_safe_url("https://localhost/mcp")


def test_loopback_ip_is_rejected():
    with pytest.raises(SSRFRejected):
        assert_safe_url("https://127.0.0.1/mcp")


def test_private_rfc1918_address_is_rejected():
    with pytest.raises(SSRFRejected):
        assert_safe_url("https://10.0.0.5/mcp")


def test_link_local_address_is_rejected():
    with pytest.raises(SSRFRejected):
        assert_safe_url("https://169.254.169.254/mcp")  # cloud metadata endpoint


def test_localhost_dev_exception_only_applies_when_explicitly_requested():
    assert_safe_url("http://localhost:8000/api/connectors/swiggy/callback", allow_localhost_dev=True)


def test_unresolvable_host_is_rejected():
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("mocked: name resolution failed")):
        with pytest.raises(SSRFRejected):
            assert_safe_url("https://this-domain-does-not-exist-orderguard-test.invalid/mcp")


def test_cross_host_redirect_is_rejected():
    with pytest.raises(SSRFRejected):
        assert_no_cross_host_redirect("https://good.example.com/mcp", "https://evil.example.com/mcp")


def test_same_host_redirect_is_allowed():
    assert_no_cross_host_redirect("https://good.example.com/mcp", "https://good.example.com/mcp?v=2")
