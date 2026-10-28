from __future__ import annotations

from typing import Any


_MISSING = object()


def get_path(context: dict[str, Any], path: str) -> Any:
    current: Any = context
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, (list, tuple)) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return _MISSING
    return current


def evaluate(predicate: dict[str, Any], context: dict[str, Any]) -> bool:
    if len(predicate) != 1:
        raise ValueError("Predicates must contain exactly one operator")
    operator, argument = next(iter(predicate.items()))
    if operator == "all":
        return all(evaluate(item, context) for item in argument)
    if operator == "any":
        return any(evaluate(item, context) for item in argument)
    if operator == "not":
        return not evaluate(argument, context)
    if operator == "exists":
        return get_path(context, str(argument)) is not _MISSING
    if operator in ("eq", "ne", "ge", "gt", "le", "lt", "contains"):
        value = get_path(context, argument["path"])
        target = argument.get("value", _MISSING)
        if "ref" in argument:
            target = get_path(context, argument["ref"])
        if value is _MISSING or target is _MISSING:
            return False
        if operator == "eq":
            return value == target
        if operator == "ne":
            return value != target
        if operator == "contains":
            return target in value
        if operator == "ge":
            return value >= target
        if operator == "gt":
            return value > target
        if operator == "le":
            return value <= target
        return value < target
    raise ValueError(f"Unsupported predicate operator: {operator}")


def violation(contract: Any, trial: Any) -> int:
    context = {
        "initial": trial.initial,
        "final": trial.final,
        "trace": trial.trace,
        "task": {"target_value": trial.task.target_value, "record_id": trial.task.record_id},
    }
    return int(
        evaluate(contract.precondition, context)
        and evaluate(contract.event, context)
        and not evaluate(contract.postcondition, context)
    )


def referenced_paths(predicate: dict[str, Any]) -> set[str]:
    if len(predicate) != 1:
        raise ValueError("Predicates must contain exactly one operator")
    operator, argument = next(iter(predicate.items()))
    if operator in ("all", "any"):
        return set().union(*(referenced_paths(item) for item in argument))
    if operator == "not":
        return referenced_paths(argument)
    if operator == "exists":
        return {str(argument)}
    if operator in ("eq", "ne", "ge", "gt", "le", "lt", "contains"):
        paths = {str(argument["path"])}
        if "ref" in argument:
            paths.add(str(argument["ref"]))
        return paths
    raise ValueError(f"Unsupported predicate operator: {operator}")
