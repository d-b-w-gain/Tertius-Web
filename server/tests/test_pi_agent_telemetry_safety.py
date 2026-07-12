import ast
import re
from pathlib import Path


FILES = (
    Path("server/core/nats_client.py"),
    Path("server/core/telemetry.py"),
    Path("server/core/pi_agent_telemetry.py"),
    Path("server/core/pi_agent_prompt.py"),
    Path("server/core/pi_agent_conversation.py"),
    Path("server/workflows/intus/intus_server.py"),
    Path("server/workflows/intus/pi_agent_job.py"),
    Path("server/workflows/intus/pi_agent_result_consumer.py"),
)
PI_FILES = {str(path) for path in FILES if "pi_agent" in path.name or path.name == "intus_server.py"}
METRIC_CALLS = {"counter_add", "histogram_record", "up_down_counter_add"}
APPROVED_LABELS = {
    "operation",
    "provider",
    "model",
    "status",
    "failure_category",
    "retryable",
}
FORBIDDEN_KEYS = {
    "conversation",
    "history",
    "assistant_summary",
    "rolling_summary",
    "system_prompt",
    "system_prompt_sha256",
    "prompt",
    "source",
    "content",
    "filename",
    "auth_token",
    "access_token",
    "refresh_token",
    "csrf_token",
    "authorization",
    "tenant_id",
    "user_id",
    "project_id",
    "job_id",
}


def is_sensitive_name(value: str) -> bool:
    snake_case = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", snake_case).strip("_").lower()
    if normalized in FORBIDDEN_KEYS:
        return True
    parts = set(normalized.split("_"))
    if "prompt" in parts and parts & {"hash", "sha", "sha256", "digest"}:
        return True
    if parts & {"conversation", "history"}:
        return True
    return "recent" in parts and bool(parts & {"turn", "turns"})


def sensitive_names(values: set[str]) -> set[str]:
    return {value for value in values if is_sensitive_name(value)}


def telemetry_safety_violations(source: str, *, filename: str) -> list[str]:
    tree = ast.parse(source)
    assignments: dict[str, ast.AST] = {}
    function_returns: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assignments[node.targets[0].id] = node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assignments[node.target.id] = node.value
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            returns = [item.value for item in ast.walk(node) if isinstance(item, ast.Return) and item.value]
            if len(returns) == 1:
                function_returns[node.name] = returns[0]

    def keys(expression: ast.AST, seen: frozenset[str] = frozenset()) -> set[str] | None:
        if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
            return {expression.value}
        if isinstance(expression, ast.Dict):
            resolved: set[str] = set()
            for key, value in zip(expression.keys, expression.values, strict=True):
                if key is None:
                    nested = keys(value, seen)
                    if nested is None:
                        return None
                    resolved |= nested
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    resolved.add(key.value)
                else:
                    return None
            return resolved
        if isinstance(expression, ast.Name) and expression.id not in seen:
            target = assignments.get(expression.id)
            return keys(target, seen | {expression.id}) if target is not None else None
        if isinstance(expression, ast.Call):
            name = getattr(expression.func, "id", None) or getattr(expression.func, "attr", None)
            if name == "pi_agent_metric_attributes":
                return set(APPROVED_LABELS)
            target = function_returns.get(str(name))
            return keys(target, seen | {str(name)}) if target is not None and name not in seen else None
        return None

    def references(expression: ast.AST, seen: frozenset[str] = frozenset()) -> set[str]:
        if isinstance(expression, ast.Constant):
            return {expression.value} if isinstance(expression.value, str) else set()
        if isinstance(expression, ast.Name):
            resolved = {expression.id}
            if expression.id not in seen and expression.id in assignments:
                resolved |= references(assignments[expression.id], seen | {expression.id})
            return resolved
        if isinstance(expression, ast.Attribute):
            return {expression.attr} | references(expression.value, seen)
        if isinstance(expression, ast.Subscript):
            return references(expression.value, seen) | references(expression.slice, seen)
        if isinstance(expression, ast.Call):
            resolved = set().union(
                *(references(argument, seen) for argument in expression.args),
                *(references(keyword.value, seen) for keyword in expression.keywords),
            )
            name = getattr(expression.func, "id", None) or getattr(expression.func, "attr", None)
            if name in function_returns and name not in seen:
                resolved |= references(function_returns[name], seen | {str(name)})
            return resolved
        return set().union(
            *(references(child, seen) for child in ast.iter_child_nodes(expression))
        )

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        attribute_expressions: list[ast.AST] = []
        if name in METRIC_CALLS and len(node.args) >= 3:
            attribute_expressions.append(node.args[2])
        if name in METRIC_CALLS:
            attribute_expressions.extend(
                keyword.value for keyword in node.keywords if keyword.arg == "attributes"
            )
        if name in {"set_attribute", "set_attributes"}:
            attribute_expressions.extend(node.args[:1])
        if name == "set_attribute":
            attribute_expressions.extend(
                keyword.value for keyword in node.keywords if keyword.arg == "key"
            )
        if name == "set_attributes":
            attribute_expressions.extend(
                keyword.value for keyword in node.keywords if keyword.arg == "attributes"
            )
        if name == "start_as_current_span":
            attribute_expressions.extend(
                keyword.value for keyword in node.keywords if keyword.arg == "attributes"
            )
        for expression in attribute_expressions:
            resolved = keys(expression)
            if resolved is None:
                violations.append(f"{filename}:{node.lineno}: unresolved dynamic attributes")
                continue
            forbidden = sensitive_names(resolved)
            if forbidden:
                violations.append(f"{filename}:{node.lineno}: forbidden attributes {sorted(forbidden)}")
            pi_metric = (
                name in METRIC_CALLS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and str(node.args[0].value).startswith("tertius.pi_agent.")
            )
            if filename in PI_FILES and pi_metric and not resolved <= APPROVED_LABELS:
                violations.append(
                    f"{filename}:{node.lineno}: unapproved labels {sorted(resolved - APPROVED_LABELS)}"
                )
        sensitive_attribute_expressions = list(attribute_expressions)
        if name == "set_attribute":
            sensitive_attribute_expressions.extend(node.args[1:2])
            sensitive_attribute_expressions.extend(
                keyword.value for keyword in node.keywords if keyword.arg == "value"
            )
        for expression in sensitive_attribute_expressions:
            sensitive_values = sensitive_names(references(expression))
            if sensitive_values:
                violations.append(
                    f"{filename}:{node.lineno}: sensitive attribute values "
                    f"{sorted(sensitive_values)}"
                )
        if str(name) in {"debug", "info", "warning", "error", "exception", "critical"}:
            logger_expressions = list(node.args)
            logger_expressions.extend(keyword.value for keyword in node.keywords)
            for argument in logger_expressions:
                if sensitive_names(references(argument)):
                    violations.append(
                        f"{filename}:{node.lineno}: sensitive logger argument"
                    )
                resolved = keys(argument)
                if resolved is not None and sensitive_names(resolved):
                    violations.append(
                        f"{filename}:{node.lineno}: sensitive logger extra"
                    )
    return violations


def test_pi_agent_telemetry_and_logs_are_statically_safe():
    violations = [
        violation
        for path in FILES
        for violation in telemetry_safety_violations(path.read_text(), filename=str(path))
    ]
    assert violations == []


def test_safety_scan_rejects_sensitive_and_dynamic_mutations():
    mutations = (
        'logger.info(f"prompt {prompt}")',
        'logger.info("failed", extra={"job_id": job_id})',
        'counter_add("tertius.pi_agent.x", 1, attributes={"job_id": job_id})',
        'histogram_record("tertius.pi_agent.x", 1, attributes={"tenant_id": tenant_id})',
        'span.start_as_current_span("x", attributes={"job_id": raw_id})',
        'logger.info("file %s", filename)',
        'logger.info("request %s", command.prompt)',
        'set_attribute("system_prompt_sha256", prompt_hash)',
        'logger.info("prompt hash %s", system_prompt_sha256)',
        'logger.info("prompt hash %s", prompt_hash)',
        'logger.info("conversation %s", context.recent_turns)',
        'span.set_attribute("prompt_hash", prompt_hash)',
        'logger.info("turns %s", payload["conversation_history"])',
        'safe_alias = payload["prompt_hash"]\nlogger.info("%s", safe_alias)',
        'safe_alias = context.conversation\nlogger.info("%s", safe_alias)',
        'safe_alias = payload["history"]\nlogger.info("%s", safe_alias)',
        'safe_alias = context.recent_turns\nlogger.info("%s", safe_alias)',
        'safe_alias: str = payload["recent_turns"]\nlogger.info("%s", safe_alias)',
        'span.set_attribute("status", payload["conversation_history"])',
        'safe_alias = context.recent_turns\nspan.set_attribute("status", safe_alias)',
        'span.set_attribute("status", value=payload["conversation_history"])',
        'span.set_attribute(key="status", value=payload["conversation_history"])',
        'labels = make_runtime_labels()\ncounter_add("x", 1, labels)',
        'counter_add("tertius.pi_agent.x", 1, {"region": "unbounded"})',
    )
    for mutation in mutations:
        assert telemetry_safety_violations(
            mutation, filename="server/workflows/intus/pi_agent_job.py"
        ), mutation


def test_safety_scan_allows_generic_trace_context_names():
    source = 'logger.info("trace context %s", trace_context)'
    assert telemetry_safety_violations(
        source, filename="server/workflows/intus/pi_agent_job.py"
    ) == []
