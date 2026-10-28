from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class PolicyState:
    verify_after_write: bool = False
    max_retries: int = 0
    confirmation_reads: int = 1
    claim_on_ack: bool = True

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> PolicyState:
        allowed = set(cls.__dataclass_fields__)
        unexpected = set(data) - allowed
        if unexpected:
            raise ValueError(f"Unexpected policy fields: {sorted(unexpected)}")
        result = cls(**data)
        if result.max_retries < 0 or result.confirmation_reads < 1:
            raise ValueError("Invalid retry or read counts")
        return result

    def evolve(self, changes: dict[str, Any]) -> PolicyState:
        return self.from_mapping({**asdict(self), **changes})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Task:
    id: str
    site: str
    seed: int
    record_id: str
    initial_value: str
    target_value: str
    operation: str
    partition: str


@dataclass(frozen=True)
class Contract:
    id: str
    entity_role: str
    operation: str
    intervention_type: str
    precondition: dict[str, Any]
    event: dict[str, Any]
    postcondition: dict[str, Any]
    source_site: str
    donor_id: str
    optional_preconditions: tuple[dict[str, Any], ...] = ()
    certificate: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_certificate(self, certificate: dict[str, Any]) -> Contract:
        return replace(self, certificate=certificate)


@dataclass(frozen=True)
class Grounding:
    contract_id: str
    site: str
    site_revision: str
    intervention_name: str
    operation_binding: str
    role_type: str


@dataclass(frozen=True)
class Trial:
    task: Task
    policy: PolicyState
    initial: dict[str, Any]
    final: dict[str, Any]
    trace: dict[str, Any]
    reward: int
    completion_claim: int
    steps: int
    intervention_name: str

    @property
    def false_completion(self) -> int:
        return self.completion_claim * (1 - self.reward)


@dataclass(frozen=True)
class CandidateScore:
    policy: PolicyState
    fit_challenge: float
    fit_regression: float
    objective: float
    complexity_cost: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GateResult:
    admitted: bool
    repair_delta: float
    regression_delta: float
    repair_lcb: float
    regression_lcb: float
    repair_pass: bool
    regression_pass: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Attempt:
    recipient: str
    contract: str
    website: str
    stage: str
    reproduced: bool
    accepted: bool
    gain: float
    cost: float
    policy_before: dict[str, Any]
    policy_after: dict[str, Any]
    gate: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
