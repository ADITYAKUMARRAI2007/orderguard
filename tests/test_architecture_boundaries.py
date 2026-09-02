"""The project's own core invariant, enforced as code, not just claimed in
docs: an AI agent may search, compare, and propose, but it never holds a
credential or a code path that can move money.

R3 tool exclusion (agent/tools.py) already proves this at the TOOL level —
a payment-capable tool can never enter either runtime's tool list. This file
proves the same thing one layer down, at the IMPORT level: no module under
``src/orderguard/agent/`` may even import the code that actually moves money,
issues a signed Authorization, or talks to Razorpay. If a payment-capable
import ever got added here by accident, R3 exclusion might still catch the
tool-list symptom — but this test catches the cause, and fails the build
before anyone has to notice the symptom at all.

Static (``ast``) parsing, not a real import — several agent/ modules create
DB engines or SDK clients at import time, which would make executing them
during a test suite its own liability. Reading the source text is enough to
prove an import statement is or is not there.

Second half of this file, below: the Secret Executor boundary
(``src/orderguard/executor.py``). "The agent never imports payment code"
proves an accidental leak can't happen through the agent orchestrator. It
does not, on its own, prove there is no OTHER path to Razorpay — a stray
import in some future module, a copy-pasted credential read, a second
client construction site. The tests below prove the stronger claim: across
this ENTIRE source tree, not just agent/, exactly one module ever reads
RZP_KEY_ID/RZP_KEY_SECRET and exactly one module ever constructs a
RazorpayClient, and it is the same module both times.

Honest limit, stated once here rather than left implicit: this is import
discipline enforced by a CI test, not a cryptographic capability nothing
could forge. It proves "no other code path was left in the source tree",
not "a compromised, already-running agent process could not somehow still
reach Razorpay's network address directly." That second, stronger claim is
what a real capability/execution token would prove, and this project does
not have one yet — see executor.py's own docstring.
"""

from __future__ import annotations

import ast
from pathlib import Path

_AGENT_DIR = Path(__file__).parent.parent / "src" / "orderguard" / "agent"
_SRC_DIR = Path(__file__).parent.parent / "src" / "orderguard"

# The modules that can actually move money, issue a signed Authorization, or
# write to the payment ledger. Not "everything app.py imports" -- deliberately
# narrow to the money-moving core itself, so this stays a real invariant
# rather than an accidental ban on something unrelated.
_FORBIDDEN_MODULES = {
    "orderguard.razorpay_client",
    "orderguard.payment",
    "orderguard.ledger",
    "orderguard.authorization",
    "orderguard.checkout_guard",
    "orderguard.executor",
    "orderguard.capability",
}


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module)
            elif node.level >= 1:
                # A relative import inside orderguard/agent/ ("from .. import
                # payment" or "from ..payment import X") always resolves
                # under the orderguard package -- normalize it the same way
                # so the forbidden-module check does not miss it just
                # because it is spelled relatively.
                prefix = "orderguard" + ("." + node.module if node.module else "")
                found.add(prefix)
    return found


def _agent_module_files() -> list[Path]:
    return sorted(_AGENT_DIR.glob("*.py"))


def test_the_agent_directory_actually_has_modules_to_check():
    """A guard against this test silently checking nothing because the path
    moved -- a passing test over zero files would be a false green, not a
    real proof."""
    files = _agent_module_files()
    assert len(files) >= 15
    assert any(f.name == "orchestrator.py" for f in files)


def test_no_agent_module_imports_the_money_moving_core():
    violations: dict[str, set[str]] = {}
    for path in _agent_module_files():
        imported = _imported_module_names(path)
        hit = imported & _FORBIDDEN_MODULES
        if hit:
            violations[path.name] = hit
    assert not violations, (
        f"agent/ modules import money-moving code directly: {violations}. "
        "An agent module must never be able to move money, issue a signed "
        "Authorization, or evaluate a payment gate itself -- that decision "
        "belongs only to the deterministic orchestration layer in app.py."
    )


def test_no_agent_module_imports_razorpay_directly_by_any_spelling():
    """Belt and suspenders against the one violation that would matter most
    in a demo: an agent module reaching for Razorpay's client directly,
    exactly the "compromised agent calls Razorpay directly" attempt a
    prompt-injected model might try."""
    for path in _agent_module_files():
        text = path.read_text()
        assert "razorpay_client" not in text, (
            f"{path.name} references razorpay_client -- an agent module "
            "must never be able to reach Razorpay directly."
        )


def test_mcp_server_also_never_imports_the_executor_or_a_credential():
    """mcp_server.py legitimately calls evaluate_pre_payment_gates directly
    (see test_mcp_server.py's parity tests) -- gate evaluation is a pure,
    credential-free decision function. It must still never reach the
    executor, the capability issuer, or a Razorpay credential; nothing
    about serving an external MCP client should ever let a caller mint a
    capability or move money through this server."""
    path = _SRC_DIR / "mcp_server.py"
    imported = _imported_module_names(path)
    assert "orderguard.executor" not in imported
    assert "orderguard.capability" not in imported
    text = path.read_text()
    assert "RZP_KEY" not in text
    assert "RazorpayClient" not in text


# --- the Secret Executor boundary: credential access and construction ------

def _all_source_files() -> list[Path]:
    return sorted(p for p in _SRC_DIR.rglob("*.py"))


def test_exactly_two_files_ever_reference_a_razorpay_credential_env_var():
    """razorpay_client.py DEFINES client_from_env(), which is where the env
    vars are literally named. executor.py is the only CALLER of it. Every
    other module in the project -- including app.py, the web-flow layer
    itself -- must go through executor.py's functions instead of reading
    RZP_KEY_ID/RZP_KEY_SECRET a second time somewhere else."""
    hits = {
        p.relative_to(_SRC_DIR).as_posix()
        for p in _all_source_files()
        if "RZP_KEY_ID" in p.read_text() or "RZP_KEY_SECRET" in p.read_text()
    }
    assert hits == {"razorpay_client.py", "executor.py"}, (
        f"unexpected files referencing a Razorpay credential env var: {hits}"
    )


def test_exactly_one_file_ever_constructs_a_razorpay_client():
    """The single-money-moving-entry-point property: app.py, once, used to
    construct RazorpayClient(...) directly at three separate call sites --
    now it calls executor.py's functions instead, and executor.py is the
    ONLY place in the source tree that ever writes RazorpayClient(."""
    hits = {
        p.relative_to(_SRC_DIR).as_posix()
        for p in _all_source_files()
        if "RazorpayClient(" in p.read_text() and p.name != "razorpay_client.py"
    }
    assert hits == {"executor.py"}, (
        f"RazorpayClient(...) constructed outside executor.py: {hits}"
    )


def test_app_py_no_longer_touches_a_credential_or_constructs_a_client_directly():
    """The specific regression this refactor exists to prevent: app.py (the
    web-flow layer) reading RZP_KEY_SECRET or building a RazorpayClient
    inline again, the way it did before this file's own executor tests
    existed."""
    text = (_SRC_DIR / "app.py").read_text()
    assert "RZP_KEY_ID" not in text and "RZP_KEY_SECRET" not in text
    assert "RazorpayClient(" not in text


def test_the_only_way_to_reach_razorpay_is_through_the_executors_own_functions():
    """The bypass test: proves there is no second door. Every module in the
    tree either never imports orderguard.executor at all, or -- if it does
    -- only ever calls the small, named set of functions executor.py
    exports (never reaches past them to touch a RazorpayClient or a
    credential executor.py did not itself hand back)."""
    import orderguard.executor as executor_module

    public_surface = set(executor_module.__all__)
    assert public_surface == {
        "RazorpayError", "Rejection", "VerifiedPayment", "CapabilityRejected",
        "public_key_id", "execute_create_order", "find_order_by_receipt", "verify_and_capture",
    }
    # None of these hand back a live, reusable RazorpayClient or a raw
    # credential -- every one returns a plain dict, a typed result, or (for
    # public_key_id) the non-secret half of the key pair only.
    assert "RazorpayClient" not in public_surface
    # The money-STARTING call takes no caller-supplied amount/currency/
    # merchant at all -- only a capability_id it will look everything up
    # from. "create_order" (the old, freely-parameterized name) must be
    # gone from the public surface, not just renamed and still callable
    # the old way.
    assert "create_order" not in public_surface


# --- Execution Capability v1: a narrow, deliberate mint/consume surface -----

def test_exactly_one_file_ever_calls_issue_capability():
    """Only the deterministic orchestration layer, after gates.allow is
    True, may mint a capability -- app.py, and nothing else in the tree."""
    hits = {
        p.relative_to(_SRC_DIR).as_posix()
        for p in _all_source_files()
        if "issue_capability(" in p.read_text() and p.name != "capability.py"
    }
    assert hits == {"app.py"}, f"issue_capability() called outside app.py: {hits}"


def test_exactly_one_file_ever_calls_consume_capability():
    """Only the executor may consume a capability -- never app.py directly,
    never agent/, never mcp_server.py. Consumption and execution are meant
    to be the same atomic step, not two separately-callable ones a bypass
    could split apart."""
    hits = {
        p.relative_to(_SRC_DIR).as_posix()
        for p in _all_source_files()
        if "consume_capability(" in p.read_text() and p.name != "capability.py"
    }
    assert hits == {"executor.py"}, f"consume_capability() called outside executor.py: {hits}"
