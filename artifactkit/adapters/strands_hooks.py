"""Strands hook providers that connect artifactkit to a Strands agent's
lifecycle, verified against the actual strands-agents 1.55.1 API
(HookProvider.register_hooks(registry) + registry.add_callback(...),
not the older per-event-method pattern seen in some examples).

Three independent providers, each solving a different problem. Use any
combination, or none -- none of this is required for artifactkit's
tool functions (strands_tools.py) to work on their own.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from strands.hooks import (
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from artifactkit.core.destinations import OutputDestination
from artifactkit.core.models import FileBundleSpec
from artifactkit.core.observability import new_operation_id
from artifactkit.core.service import ArtifactService

logger = logging.getLogger("artifactkit.strands_hooks")

ARTIFACT_TOOL_NAMES = frozenset(
    {"create_docx", "create_pptx", "create_xlsx", "create_pdf", "deliver_files"}
)


class ArtifactPolicyHookProvider(HookProvider):
    """Enforces deployment-wide rules on every artifactkit tool call
    before it executes: a forced destination prefix and/or a required
    theme, regardless of what the agent asked for.

    This intentionally lives here, in the Strands adapter, rather than
    in artifactkit's core: it's a deployment-specific policy decision
    (which bucket, which brand theme), not something the harness
    itself should assume or enforce generically.

    Verified mechanism: BeforeToolCallEvent explicitly allows writing
    `tool_use` (confirmed against strands-agents' _can_write, and by
    round-tripping a mutation through HookRegistry.invoke_callbacks),
    so rewriting the tool's input here genuinely changes what gets
    executed, not just what gets logged.
    """

    def __init__(
        self,
        *,
        destination_prefix: str | None = None,
        required_theme: str | None = None,
        tool_names: frozenset[str] = ARTIFACT_TOOL_NAMES,
    ):
        if destination_prefix is None and required_theme is None:
            raise ValueError(
                "ArtifactPolicyHookProvider requires at least one of "
                "destination_prefix or required_theme"
            )
        self._destination_prefix = destination_prefix
        self._required_theme = required_theme
        self._tool_names = tool_names

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool_call)

    def _on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] not in self._tool_names:
            return

        new_input = dict(event.tool_use["input"])
        changed = False

        if self._destination_prefix is not None:
            new_input["destination_uri"] = self._rewrite_destination(
                new_input.get("destination_uri", "")
            )
            changed = True

        spec = new_input.get("spec")
        if self._required_theme is not None and isinstance(spec, dict):
            new_input["spec"] = {**spec, "theme": self._required_theme}
            changed = True

        if changed:
            event.tool_use = {**event.tool_use, "input": new_input}
            logger.info(
                "artifact.policy.rewrote_call",
                extra={"tool_name": event.tool_use["name"]},
            )

    def _rewrite_destination(self, original_uri: str) -> str:
        # Keep whatever path segment the agent chose (usually meaningful
        # to the task), just force the prefix it lands under.
        suffix = original_uri.rsplit("/", 1)[-1] if original_uri else ""
        return f"{self._destination_prefix.rstrip('/')}/{suffix}" if suffix else self._destination_prefix


class ArtifactTracingHookProvider(HookProvider):
    """Correlates a Strands invocation with artifactkit's own
    operation_id, so one trace ties Strands' telemetry to artifactkit's
    structured logs (docs/observability.md) instead of the two living
    in separate systems correlated only by timestamp guessing.

    Verified mechanism: invocation_state is one shared dict object
    threaded from BeforeInvocationEvent through every tool-call event
    within that invocation (confirmed against strands-agents' tool
    executor source, which reads invocation_state = context.invocation_state
    and passes that same reference into every BeforeToolCallEvent/
    AfterToolCallEvent it constructs). Writing to it here is visible
    to callbacks registered later in the same invocation.
    """

    def __init__(self, tool_names: frozenset[str] = ARTIFACT_TOOL_NAMES):
        self._tool_names = tool_names

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeInvocationEvent, self._on_before_invocation)
        registry.add_callback(AfterToolCallEvent, self._on_after_tool_call)

    def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        event.invocation_state.setdefault("strands_invocation_id", new_operation_id())

    def _on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        if event.tool_use["name"] not in self._tool_names:
            return
        artifact_operation_id = self._extract_operation_id(event.result)
        if artifact_operation_id is None:
            return
        logger.info(
            "strands.artifact_tool_call",
            extra={
                "strands_invocation_id": event.invocation_state.get("strands_invocation_id"),
                "artifactkit_operation_id": artifact_operation_id,
                "tool_name": event.tool_use["name"],
            },
        )

    def _extract_operation_id(self, result) -> str | None:
        # A @tool-decorated function's plain dict return value is
        # JSON-serialized by Strands into result["content"][0]["text"]
        # (confirmed by actually invoking a tool through Strands'
        # .stream() interface, not assumed from the type signature).
        try:
            payload = json.loads(result["content"][0]["text"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return None
        return payload.get("operation_id") if isinstance(payload, dict) else None


class ArtifactAutoDeliveryHookProvider(HookProvider):
    """After a designated code-execution tool call finishes, delivers
    whatever it wrote to output_subdir via ArtifactService.create_files().
    Closes the gap where an agent runs code that produces files and
    something has to remember to call deliver_files afterward.

    output_dir_extractor is required, not defaulted, because "where did
    this tool's code write its output" is specific to whichever
    code-execution tool you're using (AgentCore Code Interpreter, a
    subprocess shell tool, something else) and this provider has no way
    to guess correctly. Verify your tool's actual result shape before
    wiring this up, the same way this module's own mechanisms were
    verified against real strands-agents source rather than assumed.

    Only files under output_dir/output_subdir are delivered, not
    everything the code run happened to leave behind -- debug files,
    temp files, and partial output shouldn't ship just because they
    existed in the same directory.

    Delivery failures are logged and swallowed, not raised: a failed
    side-delivery of generated artifacts shouldn't abort the agent's
    turn the way an unhandled exception in a hook callback would.
    """

    def __init__(
        self,
        service: ArtifactService,
        destination: OutputDestination,
        code_tool_names: frozenset[str],
        output_dir_extractor: Callable[[dict], str | None],
        output_subdir: str = "outputs",
        zip_threshold: int = 5,
    ):
        self._service = service
        self._destination = destination
        self._code_tool_names = code_tool_names
        self._output_dir_extractor = output_dir_extractor
        self._output_subdir = output_subdir
        self._zip_threshold = zip_threshold

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(AfterToolCallEvent, self._on_after_tool_call)

    def _on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        if event.tool_use["name"] not in self._code_tool_names:
            return
        if event.exception is not None:
            return  # the code run failed; nothing to deliver

        try:
            output_dir = self._output_dir_extractor(event.result)
            if not output_dir:
                return
            target_dir = Path(output_dir) / self._output_subdir
            if not target_dir.is_dir() or not any(target_dir.iterdir()):
                return
            bundle = FileBundleSpec.from_directory(target_dir)
            result = self._service.create_files(
                bundle, self._destination, zip_threshold=self._zip_threshold
            )
            logger.info(
                "artifact.auto_delivery.success",
                extra={"operation_id": result.operation_id, "file_count": result.file_count},
            )
        except Exception as exc:  # noqa: BLE001 - best-effort, must not abort the agent turn
            logger.warning("artifact.auto_delivery.failed", extra={"error": str(exc)})
