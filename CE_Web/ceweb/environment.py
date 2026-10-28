from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from random import Random
from typing import Any, Protocol

from .models import Grounding, PolicyState, Task, Trial


class Environment(Protocol):
    name: str
    revision: str

    def grounding(self, contract: Any) -> Grounding | None: ...

    def execute(self, task: Task, policy: PolicyState, intervention_name: str) -> Trial: ...


class InMemoryWebEnvironment:
    def __init__(self, name: str, definition: dict[str, Any]):
        self.name = name
        self.definition = deepcopy(definition)
        self.revision = str(definition["revision"])

    def grounding(self, contract: Any) -> Grounding | None:
        role = self.definition["entity_roles"].get(contract.entity_role)
        operation = self.definition["operation_bindings"].get(contract.operation)
        if role is None or operation is None:
            return None
        if isinstance(operation, dict):
            if operation.get("entity_role") != contract.entity_role:
                return None
            binding = operation.get("action")
        else:
            binding = operation
        if not binding:
            return None
        from .predicates import referenced_paths

        required = set().union(
            referenced_paths(contract.precondition),
            referenced_paths(contract.event),
            referenced_paths(contract.postcondition),
            *(referenced_paths(p) for p in contract.optional_preconditions),
        )
        if not required.issubset(set(self.definition.get("observable_fields", []))):
            return None
        allowed = set(self.definition["authorized_interventions"])
        candidates = [
            (name, intervention)
            for name, intervention in self.definition["interventions"].items()
            if name in allowed and intervention.get("type") == contract.intervention_type
        ]
        if not candidates:
            return None
        name, definition = candidates[0]
        if not definition.get("recoverable", False):
            return None
        if not self.definition.get("independent_verifier", False):
            return None
        return Grounding(
            contract_id=contract.id,
            site=self.name,
            site_revision=self.revision,
            intervention_name=name,
            operation_binding=str(binding),
            role_type=str(role),
        )

    def execute(self, task: Task, policy: PolicyState, intervention_name: str) -> Trial:
        if task.site != self.name:
            raise ValueError("Task and site mismatch")
        if task.operation not in self.definition["operation_bindings"]:
            raise ValueError("Unsupported operation")
        if intervention_name not in self.definition["authorized_interventions"]:
            raise PermissionError(f"Intervention not authorized: {intervention_name}")
        intervention = self.definition["interventions"][intervention_name]
        if not intervention.get("recoverable", False):
            raise ValueError("Nonrecoverable intervention")
        actual = task.initial_value
        original = task.initial_value
        drop_writes = int(intervention.get("drop_first_writes", 0))
        stochastic_probability = float(intervention.get("drop_probability", 1.0))
        seed = int.from_bytes(sha256(f"{self.revision}:{intervention_name}:{task.seed}".encode()).digest()[:8], "big")
        if Random(seed).random() > stochastic_probability:
            drop_writes = 0
        stale_reads = int(intervention.get("stale_first_reads", 0))
        acknowledge_dropped = bool(intervention.get("acknowledge_dropped", False))
        visible_ack = False
        writes = 0
        reads = 0
        observations: list[dict[str, Any]] = []
        verified_read = False
        for attempt in range(policy.max_retries + 1):
            writes += 1
            discarded = writes <= drop_writes
            if not discarded:
                actual = task.target_value
            ack = not discarded or acknowledge_dropped
            visible_ack = visible_ack or ack
            observations.append({"action": "write", "attempt": attempt, "acknowledged": ack})
            if not policy.verify_after_write:
                break
            for _ in range(policy.confirmation_reads):
                reads += 1
                read_value = original if reads <= stale_reads else actual
                observations.append({"action": "read", "value": read_value})
                if read_value == task.target_value:
                    verified_read = True
                    break
            if verified_read:
                break
        claim = int(verified_read if policy.verify_after_write else (visible_ack and policy.claim_on_ack))
        initial = {"record": {"value": original, "exists": True}, "permission": {"write": True}}
        final = {"record": {"value": actual, "exists": True}}
        trace = {
            "acknowledged": visible_ack,
            "completion_claim": bool(claim),
            "verified_read": verified_read,
            "write_attempts": writes,
            "read_attempts": reads,
            "observations": observations,
        }
        reward = int(actual == task.target_value)
        return Trial(
            task=task,
            policy=policy,
            initial=initial,
            final=final,
            trace=trace,
            reward=reward,
            completion_claim=claim,
            steps=writes + reads,
            intervention_name=intervention_name,
        )


def build_environments(config: dict[str, Any]) -> dict[str, Environment]:
    from .http_environment import HTTPWebEnvironment

    factory = {"memory": InMemoryWebEnvironment, "http": HTTPWebEnvironment}
    return {name: factory[spec.get("driver", "memory")](name, spec) for name, spec in config["sites"].items()}
