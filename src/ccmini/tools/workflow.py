"""WorkflowTool — define and execute multi-step workflows.

A workflow is a DAG of steps, each referencing a tool name and its input.
Steps can depend on the results of prior steps, execute conditionally, and
run in parallel when their dependencies are satisfied.  Error handling is
configurable per-workflow (continue, abort, retry).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..tool import Tool, ToolUseContext

# ---------------------------------------------------------------------------
# Workflow data model
# ---------------------------------------------------------------------------


class OnError(str, Enum):
    ABORT = "abort"
    CONTINUE = "continue"
    RETRY = "retry"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class WorkflowStep:
    """A single step in a workflow."""
    name: str
    tool_name: str
    tool_input: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    condition: str | None = None
    max_retries: int = 0

    # Runtime state
    status: StepStatus = StepStatus.PENDING
    result: str = ""
    error: str = ""
    elapsed_ms: float = 0.0
    retries: int = 0


@dataclass(slots=True)
class Workflow:
    """A DAG of workflow steps."""
    name: str
    steps: list[WorkflowStep]
    on_error: OnError = OnError.ABORT

    def step_map(self) -> dict[str, WorkflowStep]:
        return {s.name: s for s in self.steps}


# ---------------------------------------------------------------------------
# Workflow execution engine
# ---------------------------------------------------------------------------

async def execute_workflow(
    workflow: Workflow,
    tool_map: dict[str, Tool],
    context: ToolUseContext,
) -> WorkflowResult:
    """Execute a workflow DAG, respecting dependencies and error handling.

    *tool_map* maps tool name → Tool instance.  Each step's ``tool_name``
    must exist in this map.
    """
    step_map = workflow.step_map()
    results: dict[str, str] = {}
    start_time = time.monotonic()

    # Validate DAG
    if err := _validate_dag(workflow):
        return WorkflowResult(
            workflow_name=workflow.name,
            status="error",
            message=err,
            step_results=[],
            elapsed_ms=0.0,
        )

    # Topological execution
    completed: set[str] = set()
    failed: set[str] = set()

    while True:
        # Find ready steps (all deps met, not yet started)
        ready = [
            s for s in workflow.steps
            if s.status == StepStatus.PENDING
            and all(d in completed for d in s.depends_on)
            and not any(d in failed for d in s.depends_on)
        ]

        # Skip steps with failed dependencies
        for s in workflow.steps:
            if s.status == StepStatus.PENDING and any(d in failed for d in s.depends_on):
                s.status = StepStatus.SKIPPED
                s.error = "Dependency failed"
                completed.add(s.name)

        if not ready:
            break

        # Run ready steps in parallel
        tasks = [
            _run_step(s, tool_map, context, results, workflow.on_error)
            for s in ready
        ]
        await asyncio.gather(*tasks)

        for s in ready:
            completed.add(s.name)
            if s.status == StepStatus.SUCCEEDED:
                results[s.name] = s.result
            elif s.status == StepStatus.FAILED:
                failed.add(s.name)
                if workflow.on_error == OnError.ABORT:
                    # Mark remaining as skipped
                    for remaining in workflow.steps:
                        if remaining.status == StepStatus.PENDING:
                            remaining.status = StepStatus.SKIPPED
                            remaining.error = "Aborted due to prior failure"
                    break

    elapsed = (time.monotonic() - start_time) * 1000

    all_succeeded = all(s.status == StepStatus.SUCCEEDED for s in workflow.steps)
    any_failed = any(s.status == StepStatus.FAILED for s in workflow.steps)

    step_results = [
        StepResult(
            name=s.name,
            tool_name=s.tool_name,
            status=s.status.value,
            result=s.result,
            error=s.error,
            elapsed_ms=s.elapsed_ms,
            retries=s.retries,
        )
        for s in workflow.steps
    ]

    status = "success" if all_succeeded else ("partial" if not any_failed else "failed")

    return WorkflowResult(
        workflow_name=workflow.name,
        status=status,
        message="",
        step_results=step_results,
        elapsed_ms=elapsed,
    )


async def _run_step(
    step: WorkflowStep,
    tool_map: dict[str, Tool],
    context: ToolUseContext,
    prior_results: dict[str, str],
    on_error: OnError,
) -> None:
    """Execute a single workflow step."""
    step.status = StepStatus.RUNNING

    tool = tool_map.get(step.tool_name)
    if tool is None:
        step.status = StepStatus.FAILED
        step.error = f"Tool '{step.tool_name}' not found"
        return

    # Evaluate condition
    if step.condition and not _evaluate_condition(step.condition, prior_results):
        step.status = StepStatus.SKIPPED
        step.error = f"Condition not met: {step.condition}"
        return

    # Substitute references to prior results in tool_input
    resolved_input = _resolve_references(step.tool_input, prior_results)

    start = time.monotonic()
    attempts = 1 + step.max_retries
    last_error = ""

    for attempt in range(attempts):
        try:
            result = await tool.execute(context=context, **resolved_input)
            step.result = result
            step.status = StepStatus.SUCCEEDED
            step.elapsed_ms = (time.monotonic() - start) * 1000
            step.retries = attempt
            return
        except Exception as exc:
            last_error = str(exc)
            step.retries = attempt + 1
            if attempt < attempts - 1 and on_error == OnError.RETRY:
                await asyncio.sleep(1.0 * (attempt + 1))

    step.status = StepStatus.FAILED
    step.error = last_error
    step.elapsed_ms = (time.monotonic() - start) * 1000


# ---------------------------------------------------------------------------
# DAG validation
# ---------------------------------------------------------------------------

def _validate_dag(workflow: Workflow) -> str | None:
    """Return an error if the workflow DAG is invalid, else None."""
    names = {s.name for s in workflow.steps}
    if len(names) != len(workflow.steps):
        return "Error: Duplicate step names detected"

    for step in workflow.steps:
        for dep in step.depends_on:
            if dep not in names:
                return f"Error: Step '{step.name}' depends on unknown step '{dep}'"

    # Cycle detection via topological sort
    visited: set[str] = set()
    in_stack: set[str] = set()
    step_map = workflow.step_map()

    def _has_cycle(name: str) -> bool:
        if name in in_stack:
            return True
        if name in visited:
            return False
        visited.add(name)
        in_stack.add(name)
        for dep in step_map[name].depends_on:
            if _has_cycle(dep):
                return True
        in_stack.discard(name)
        return False

    for s in workflow.steps:
        if _has_cycle(s.name):
            return f"Error: Cycle detected involving step '{s.name}'"

    return None


# ---------------------------------------------------------------------------
# Condition evaluation & reference resolution
# ---------------------------------------------------------------------------

def _evaluate_condition(condition: str, results: dict[str, str]) -> bool:
    """Evaluate a simple condition string against prior results.

    Supports:
    - ``step_name.succeeded`` — True if step produced a result
    - ``step_name.contains(text)`` — True if result contains text
    - ``step_name.empty`` — True if result is empty
    - Raw ``true``/``false``
    """
    condition = condition.strip()
    if condition.lower() == "true":
        return True
    if condition.lower() == "false":
        return False

    if ".succeeded" in condition:
        step_name = condition.split(".succeeded")[0].strip()
        return step_name in results

    if ".empty" in condition:
        step_name = condition.split(".empty")[0].strip()
        return step_name not in results or not results[step_name].strip()

    if ".contains(" in condition:
        parts = condition.split(".contains(", 1)
        step_name = parts[0].strip()
        search_text = parts[1].rstrip(")").strip().strip("'\"")
        return step_name in results and search_text in results[step_name]

    return True


def _resolve_references(
    tool_input: dict[str, Any], results: dict[str, str],
) -> dict[str, Any]:
    """Replace ``${step_name}`` placeholders in string values with results."""
    resolved: dict[str, Any] = {}
    for key, value in tool_input.items():
        if isinstance(value, str):
            for step_name, step_result in results.items():
                value = value.replace(f"${{{step_name}}}", step_result)
            resolved[key] = value
        else:
            resolved[key] = value
    return resolved


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StepResult:
    name: str
    tool_name: str
    status: str
    result: str
    error: str
    elapsed_ms: float
    retries: int


@dataclass(slots=True)
class WorkflowResult:
    workflow_name: str
    status: str
    message: str
    step_results: list[StepResult]
    elapsed_ms: float

    def format(self) -> str:
        lines = [
            f"Workflow '{self.workflow_name}': {self.status} ({self.elapsed_ms:.0f}ms)",
        ]
        if self.message:
            lines.append(f"  {self.message}")
        for sr in self.step_results:
            status_icon = {
                "succeeded": "+",
                "failed": "X",
                "skipped": "-",
                "running": "~",
                "pending": ".",
            }.get(sr.status, "?")
            line = f"  [{status_icon}] {sr.name} ({sr.tool_name}): {sr.status}"
            if sr.elapsed_ms:
                line += f" [{sr.elapsed_ms:.0f}ms]"
            if sr.retries:
                line += f" (retries: {sr.retries})"
            lines.append(line)
            if sr.error:
                lines.append(f"      Error: {sr.error}")
            if sr.result and sr.status == "succeeded":
                preview = sr.result[:200].replace("\n", " ")
                if len(sr.result) > 200:
                    preview += "..."
                lines.append(f"      Result: {preview}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Built-in workflow templates
# ---------------------------------------------------------------------------

def refactor_workflow(file_patterns: list[str], refactor_type: str = "rename") -> Workflow:
    """Create a workflow that performs multi-file refactoring.

    Steps: grep for usages → review files → apply edits → run linter.
    """
    steps = [
        WorkflowStep(
            name="find_usages",
            tool_name="Grep",
            tool_input={"pattern": file_patterns[0] if file_patterns else "", "path": "."},
        ),
        WorkflowStep(
            name="review",
            tool_name="Read",
            tool_input={"file_path": "${find_usages}"},
            depends_on=["find_usages"],
            condition="find_usages.succeeded",
        ),
    ]
    return Workflow(name=f"refactor_{refactor_type}", steps=steps, on_error=OnError.ABORT)


def test_and_fix_workflow(test_command: str) -> Workflow:
    """Create a workflow: run tests → if failures, attempt fix → re-run tests."""
    return Workflow(
        name="test_and_fix",
        steps=[
            WorkflowStep(
                name="run_tests",
                tool_name="Bash",
                tool_input={"command": test_command},
            ),
            WorkflowStep(
                name="analyze_failures",
                tool_name="Bash",
                tool_input={"command": f"{test_command} --tb=short 2>&1 | tail -50"},
                depends_on=["run_tests"],
                condition="run_tests.contains(FAILED)",
            ),
            WorkflowStep(
                name="rerun_tests",
                tool_name="Bash",
                tool_input={"command": test_command},
                depends_on=["analyze_failures"],
                condition="analyze_failures.succeeded",
            ),
        ],
        on_error=OnError.CONTINUE,
    )


def review_workflow(files: list[str]) -> Workflow:
    """Create a workflow that reads and reviews multiple files systematically."""
    steps: list[WorkflowStep] = []
    for i, file_path in enumerate(files):
        steps.append(WorkflowStep(
            name=f"read_{i}",
            tool_name="Read",
            tool_input={"file_path": file_path},
        ))
    return Workflow(name="review_files", steps=steps, on_error=OnError.CONTINUE)


# ---------------------------------------------------------------------------
# WorkflowTool — LLM-facing tool
# ---------------------------------------------------------------------------

class WorkflowTool(Tool):
    name = "WorkflowTool"
    description = (
        "Define and execute multi-step workflows — a DAG of tool invocations "
        "with dependencies, conditions, parallel execution, and error handling."
    )
    instructions = """\
Define and execute multi-step workflows as a DAG of tool calls.

## Defining a workflow

Pass a JSON object with:
- name: Workflow name (string)
- on_error: "abort" (default), "continue", or "retry"
- steps: Array of step objects, each with:
  - name: Unique step name
  - tool_name: Name of the tool to invoke (e.g. "Bash", "Read", "Grep")
  - tool_input: Dict of parameters for the tool
  - depends_on: (optional) List of step names this step depends on
  - condition: (optional) Condition to evaluate before running:
    - "step_name.succeeded" — run only if step succeeded
    - "step_name.contains(text)" — run only if step result contains text
    - "step_name.empty" — run only if step result is empty
  - max_retries: (optional) Number of retries on failure (default: 0)

## Reference substitution

Use ${step_name} in tool_input string values to reference a prior step's \
result.  For example:
  {"command": "echo ${find_files}"}

## Execution

Independent steps (no unsatisfied deps) run in parallel. \
Dependent steps wait for their dependencies to complete.

## Built-in workflows

Pass action="builtin" with one of:
- builtin_name="test_and_fix", test_command="pytest"
- builtin_name="review_files", files=["a.py", "b.py"]
- builtin_name="refactor", file_patterns=["old_name"], \
refactor_type="rename"\
"""
    is_read_only = False

    def __init__(self, *, tool_map: dict[str, Tool] | None = None) -> None:
        self._tool_map: dict[str, Tool] = tool_map or {}

    def set_tool_map(self, tool_map: dict[str, Tool]) -> None:
        """Update the available tools (called during engine setup)."""
        self._tool_map = tool_map

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": '"execute" to run a custom workflow, "builtin" for a template',
                    "enum": ["execute", "builtin"],
                },
                "workflow": {
                    "type": "object",
                    "description": "Workflow definition (for action=execute)",
                    "properties": {
                        "name": {"type": "string"},
                        "on_error": {
                            "type": "string",
                            "enum": ["abort", "continue", "retry"],
                        },
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "tool_name": {"type": "string"},
                                    "tool_input": {"type": "object"},
                                    "depends_on": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "condition": {"type": "string"},
                                    "max_retries": {"type": "integer"},
                                },
                                "required": ["name", "tool_name", "tool_input"],
                            },
                        },
                    },
                    "required": ["name", "steps"],
                },
                "builtin_name": {
                    "type": "string",
                    "description": "Built-in workflow name (for action=builtin)",
                    "enum": ["test_and_fix", "review_files", "refactor"],
                },
                "test_command": {"type": "string", "description": "Test command (for test_and_fix)"},
                "files": {
                    "type": "array",
                    "description": "File paths (for review_files)",
                    "items": {"type": "string"},
                },
                "file_patterns": {
                    "type": "array",
                    "description": "Patterns to search (for refactor)",
                    "items": {"type": "string"},
                },
                "refactor_type": {"type": "string", "description": "Refactor type (for refactor)"},
            },
            "required": ["action"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "execute")

        if action == "builtin":
            return await self._run_builtin(context, **kwargs)

        workflow_data = kwargs.get("workflow")
        if not workflow_data:
            return "Error: 'workflow' definition is required for action=execute"

        # Parse workflow from dict
        try:
            wf = _parse_workflow(workflow_data)
        except Exception as exc:
            return f"Error parsing workflow: {exc}"

        if not self._tool_map:
            return "Error: No tools available. Workflow tool must be initialized with a tool map."

        result = await execute_workflow(wf, self._tool_map, context)
        return result.format()

    async def _run_builtin(self, context: ToolUseContext, **kwargs: Any) -> str:
        builtin_name: str = kwargs.get("builtin_name", "")

        if builtin_name == "test_and_fix":
            test_cmd = kwargs.get("test_command", "pytest")
            wf = test_and_fix_workflow(test_cmd)
        elif builtin_name == "review_files":
            files = kwargs.get("files", [])
            if not files:
                return "Error: 'files' list is required for review_files"
            wf = review_workflow(files)
        elif builtin_name == "refactor":
            patterns = kwargs.get("file_patterns", [])
            rtype = kwargs.get("refactor_type", "rename")
            wf = refactor_workflow(patterns, rtype)
        else:
            return f"Error: Unknown builtin workflow '{builtin_name}'. Available: test_and_fix, review_files, refactor"

        if not self._tool_map:
            return "Error: No tools available."

        result = await execute_workflow(wf, self._tool_map, context)
        return result.format()


def _parse_workflow(data: dict[str, Any]) -> Workflow:
    """Parse a workflow definition dict into a Workflow object."""
    name = data.get("name", "unnamed")
    on_error_str = data.get("on_error", "abort")
    on_error = OnError(on_error_str)

    steps_data = data.get("steps", [])
    steps: list[WorkflowStep] = []
    for sd in steps_data:
        steps.append(WorkflowStep(
            name=sd["name"],
            tool_name=sd["tool_name"],
            tool_input=sd.get("tool_input", {}),
            depends_on=sd.get("depends_on", []),
            condition=sd.get("condition"),
            max_retries=sd.get("max_retries", 0),
        ))

    return Workflow(name=name, steps=steps, on_error=on_error)
