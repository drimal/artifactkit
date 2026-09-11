"""Tests for artifactkit.adapters.strands_hooks. Requires the
[strands] extra -- skipped cleanly if strands-agents isn't installed."""

import json
import logging

import pytest

strands = pytest.importorskip("strands", reason="requires artifactkit[strands]")

from strands.hooks import (  # noqa: E402
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookRegistry,
)

from artifactkit import ArtifactService, RawFile, FileBundleSpec, destination_from_uri  # noqa: E402
from artifactkit.adapters.strands_hooks import (  # noqa: E402
    ArtifactAutoDeliveryHookProvider,
    ArtifactPolicyHookProvider,
    ArtifactTracingHookProvider,
)


def _tool_use(name: str, **input_kwargs) -> dict:
    return {"name": name, "toolUseId": "t1", "input": input_kwargs}


def _tool_result(payload: dict) -> dict:
    """Mirrors exactly what Strands produces for a @tool function that
    returns a plain dict -- confirmed by actually invoking a tool
    through Strands' .stream() interface, not assumed."""
    return {"toolUseId": "t1", "status": "success", "content": [{"text": json.dumps(payload)}]}


class TestArtifactPolicyHookProvider:
    def test_requires_at_least_one_policy(self):
        with pytest.raises(ValueError, match="at least one"):
            ArtifactPolicyHookProvider()

    def test_rewrites_destination_uri_for_artifact_tool(self):
        hook = ArtifactPolicyHookProvider(destination_prefix="s3://forced-bucket/prefix")
        registry = HookRegistry()
        registry.add_hook(hook)

        event = BeforeToolCallEvent(
            agent=None,
            selected_tool=None,
            tool_use=_tool_use("create_docx", destination_uri="s3://whatever-agent-chose/x", filename="out.docx"),
            invocation_state={},
        )
        mutated, _ = registry.invoke_callbacks(event)
        assert mutated.tool_use["input"]["destination_uri"] == "s3://forced-bucket/prefix/x"

    def test_injects_required_theme_into_spec(self):
        hook = ArtifactPolicyHookProvider(required_theme="corporate")
        registry = HookRegistry()
        registry.add_hook(hook)

        event = BeforeToolCallEvent(
            agent=None,
            selected_tool=None,
            tool_use=_tool_use("create_pptx", spec={"title": "x"}, destination_uri="s3://b/x"),
            invocation_state={},
        )
        mutated, _ = registry.invoke_callbacks(event)
        assert mutated.tool_use["input"]["spec"]["theme"] == "corporate"

    def test_ignores_non_artifact_tools(self):
        hook = ArtifactPolicyHookProvider(destination_prefix="s3://forced/prefix")
        registry = HookRegistry()
        registry.add_hook(hook)

        original_input = {"query": "weather in Kathmandu"}
        event = BeforeToolCallEvent(
            agent=None, selected_tool=None,
            tool_use=_tool_use("web_search", **original_input),
            invocation_state={},
        )
        mutated, _ = registry.invoke_callbacks(event)
        assert mutated.tool_use["input"] == original_input

    def test_deliver_files_has_no_spec_so_theme_injection_is_a_noop(self):
        hook = ArtifactPolicyHookProvider(required_theme="vibrant")
        registry = HookRegistry()
        registry.add_hook(hook)

        event = BeforeToolCallEvent(
            agent=None, selected_tool=None,
            tool_use=_tool_use("deliver_files", files=[], destination_uri="s3://b/x"),
            invocation_state={},
        )
        mutated, _ = registry.invoke_callbacks(event)
        assert "spec" not in mutated.tool_use["input"]  # nothing to inject theme into


class TestArtifactTracingHookProvider:
    def test_stashes_invocation_id_once_per_invocation(self):
        hook = ArtifactTracingHookProvider()
        registry = HookRegistry()
        registry.add_hook(hook)

        state = {}
        event = BeforeInvocationEvent(agent=None, invocation_state=state)
        registry.invoke_callbacks(event)
        assert "strands_invocation_id" in state
        first_id = state["strands_invocation_id"]

        # A second BeforeInvocationEvent on the SAME state dict should not
        # overwrite an existing id (setdefault semantics).
        registry.invoke_callbacks(BeforeInvocationEvent(agent=None, invocation_state=state))
        assert state["strands_invocation_id"] == first_id

    def test_logs_correlation_for_artifact_tool_call(self, caplog):
        caplog.set_level(logging.INFO, logger="artifactkit.strands_hooks")
        hook = ArtifactTracingHookProvider()
        registry = HookRegistry()
        registry.add_hook(hook)

        state = {"strands_invocation_id": "strands-abc"}
        event = AfterToolCallEvent(
            agent=None, selected_tool=None,
            tool_use=_tool_use("create_docx", destination_uri="x", filename="y.docx"),
            invocation_state=state,
            result=_tool_result({"location": "y.docx", "operation_id": "artifact-xyz"}),
        )
        registry.invoke_callbacks(event)

        records = [r for r in caplog.records if r.message == "strands.artifact_tool_call"]
        assert len(records) == 1
        assert records[0].strands_invocation_id == "strands-abc"
        assert records[0].artifactkit_operation_id == "artifact-xyz"

    def test_ignores_non_artifact_tool_calls(self, caplog):
        caplog.set_level(logging.INFO, logger="artifactkit.strands_hooks")
        hook = ArtifactTracingHookProvider()
        registry = HookRegistry()
        registry.add_hook(hook)

        event = AfterToolCallEvent(
            agent=None, selected_tool=None,
            tool_use=_tool_use("web_search", query="x"),
            invocation_state={},
            result=_tool_result({"results": []}),
        )
        registry.invoke_callbacks(event)
        assert not [r for r in caplog.records if r.message == "strands.artifact_tool_call"]

    def test_malformed_result_does_not_crash(self):
        hook = ArtifactTracingHookProvider()
        registry = HookRegistry()
        registry.add_hook(hook)

        event = AfterToolCallEvent(
            agent=None, selected_tool=None,
            tool_use=_tool_use("create_docx", destination_uri="x"),
            invocation_state={},
            result={"toolUseId": "t1", "status": "error", "content": []},  # no text content
        )
        registry.invoke_callbacks(event)  # must not raise


class TestArtifactAutoDeliveryHookProvider:
    def test_delivers_files_from_output_subdir(self, tmp_path):
        run_dir = tmp_path / "run"
        (run_dir / "outputs").mkdir(parents=True)
        (run_dir / "outputs" / "result.csv").write_bytes(b"a,b\n1,2\n")
        (run_dir / "scratch.tmp").write_bytes(b"should not be delivered")  # outside outputs/

        dest_dir = tmp_path / "delivered"
        service = ArtifactService()
        hook = ArtifactAutoDeliveryHookProvider(
            service=service,
            destination=destination_from_uri(str(dest_dir)),
            code_tool_names=frozenset({"execute_code"}),
            output_dir_extractor=lambda result: str(run_dir),
        )
        registry = HookRegistry()
        registry.add_hook(hook)

        event = AfterToolCallEvent(
            agent=None, selected_tool=None,
            tool_use=_tool_use("execute_code", code="print(1)"),
            invocation_state={},
            result=_tool_result({"status": "ok"}),
        )
        registry.invoke_callbacks(event)

        delivered = list(dest_dir.glob("*"))
        assert [p.name for p in delivered] == ["result.csv"]  # only the outputs/ file

    def test_skips_delivery_when_tool_call_failed(self, tmp_path):
        run_dir = tmp_path / "run"
        (run_dir / "outputs").mkdir(parents=True)
        (run_dir / "outputs" / "result.csv").write_bytes(b"x")
        dest_dir = tmp_path / "delivered"

        service = ArtifactService()
        hook = ArtifactAutoDeliveryHookProvider(
            service=service,
            destination=destination_from_uri(str(dest_dir)),
            code_tool_names=frozenset({"execute_code"}),
            output_dir_extractor=lambda result: str(run_dir),
        )
        registry = HookRegistry()
        registry.add_hook(hook)

        event = AfterToolCallEvent(
            agent=None, selected_tool=None,
            tool_use=_tool_use("execute_code", code="raise ValueError()"),
            invocation_state={},
            result=_tool_result({"status": "error"}),
            exception=ValueError("boom"),
        )
        registry.invoke_callbacks(event)
        assert list(dest_dir.glob("*")) == []  # never even attempted

    def test_skips_when_outputs_subdir_missing_or_empty(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()  # no outputs/ subdir at all
        dest_dir = tmp_path / "delivered"

        service = ArtifactService()
        hook = ArtifactAutoDeliveryHookProvider(
            service=service,
            destination=destination_from_uri(str(dest_dir)),
            code_tool_names=frozenset({"execute_code"}),
            output_dir_extractor=lambda result: str(run_dir),
        )
        registry = HookRegistry()
        registry.add_hook(hook)

        event = AfterToolCallEvent(
            agent=None, selected_tool=None,
            tool_use=_tool_use("execute_code", code="pass"),
            invocation_state={},
            result=_tool_result({"status": "ok"}),
        )
        registry.invoke_callbacks(event)  # must not raise
        assert list(dest_dir.glob("*")) == []

    def test_ignores_non_code_tools(self, tmp_path):
        dest_dir = tmp_path / "delivered"
        service = ArtifactService()
        hook = ArtifactAutoDeliveryHookProvider(
            service=service,
            destination=destination_from_uri(str(dest_dir)),
            code_tool_names=frozenset({"execute_code"}),
            output_dir_extractor=lambda result: str(tmp_path),
        )
        registry = HookRegistry()
        registry.add_hook(hook)

        event = AfterToolCallEvent(
            agent=None, selected_tool=None,
            tool_use=_tool_use("web_search", query="x"),
            invocation_state={},
            result=_tool_result({"results": []}),
        )
        registry.invoke_callbacks(event)
        assert list(dest_dir.glob("*")) == []

    def test_delivery_failure_is_logged_not_raised(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING, logger="artifactkit.strands_hooks")
        run_dir = tmp_path / "run"
        (run_dir / "outputs").mkdir(parents=True)
        (run_dir / "outputs" / "f.txt").write_bytes(b"x")

        class BrokenDestination:
            def write(self, local_path, filename):
                raise ConnectionError("simulated failure")
            def verify(self, write_result, local_path):
                return True
            def is_retryable(self, exc):
                return False
            def presign(self, key):
                return None

        service = ArtifactService()
        hook = ArtifactAutoDeliveryHookProvider(
            service=service,
            destination=BrokenDestination(),
            code_tool_names=frozenset({"execute_code"}),
            output_dir_extractor=lambda result: str(run_dir),
        )
        registry = HookRegistry()
        registry.add_hook(hook)

        event = AfterToolCallEvent(
            agent=None, selected_tool=None,
            tool_use=_tool_use("execute_code", code="pass"),
            invocation_state={},
            result=_tool_result({"status": "ok"}),
        )
        registry.invoke_callbacks(event)  # must NOT raise, despite the destination failing

        failure_logs = [r for r in caplog.records if r.message == "artifact.auto_delivery.failed"]
        assert len(failure_logs) == 1
