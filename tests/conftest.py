"""Test-session-wide fixtures. No test suite in this repo depends on
``.env``; anything an endpoint test genuinely needs is set here, explicitly,
as a value that is fine to be public because it only ever encrypts data that
lives inside this same test run's throwaway SQLite files.
"""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _connector_token_key():
    # A real Fernet key, generated once for this repo's test suite. Not a
    # secret: it never protects anything outside an in-process test run.
    os.environ.setdefault("CONNECTOR_TOKEN_KEY", "OHQ3bx-K7JqW9ucN2Mc-abwboaI9SRlQ2JuUVLmsYxc=")
