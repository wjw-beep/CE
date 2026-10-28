from __future__ import annotations

from dataclasses import asdict
from itertools import combinations, product
from math import floor
from random import Random
from typing import Any

from .environment import Environment
from .models import CandidateScore, Contract, GateResult, PolicyState, Task


def verified_rate(environment: Environment, tasks: list[Task], policy: PolicyState, intervention: str) -> float:
    if not tasks:
        raise ValueError("Evaluation set must not be empty")
    return sum(environment.execute(task, policy, intervention).reward for task in tasks) / len(tasks)


def policy_cost(policy: PolicyState, config: dict[str, Any]) -> float:
    weights = config["mutation"]["cost_weights"]
    return (
        float(weights["verify_after_write"]) * int(policy.verify_after_write)
        + float(weights["max_retries"]) * policy.max_retries
        + float(weights["confirmation_reads"]) * (policy.confirmation_reads - 1)
        + float(weights["claim_on_ack_off"]) * int(not policy.claim_on_ack)
    )


class MutationOperator:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def propose(self, current: PolicyState) -> list[PolicyState]:
        options = self.config["mutation"]["allowed_values"]
        fields = sorted(options)
        candidates = []
        seen: set[tuple[Any, ...]] = set()
        maximum = min(int(self.config["mutation"]["max_changed_fields"]), len(fields))
        for number in range(1, maximum + 1):
            for field_set in combinations(fields, number):
                alternatives = [[v for v in options[field] if v != getattr(current, field)] for field in field_set]
                for values in product(*alternatives):
                    candidate = current.evolve(dict(zip(field_set, values)))
                    signature = tuple(asdict(candidate).values())
                    if signature not in seen:
                        candidates.append(candidate)
                        seen.add(signature)
        return candidates[: int(self.config["mutation"]["max_candidates"])]


class CandidateFitter:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        if config["mutation"].get("strategy", "enumerate") == "llm":
            from .llm import OpenAICompatibleMutator

            self.mutation = OpenAICompatibleMutator(config)
        else:
            self.mutation = MutationOperator(config)

    def fit(self, environment: Environment, current: PolicyState, intervention: str, challenge: list[Task], regression: list[Task], clean_intervention: str, local_failure: dict[str, Any] | None = None) -> tuple[CandidateScore | None, list[CandidateScore], int]:
        if isinstance(self.mutation, MutationOperator):
            candidates = self.mutation.propose(current)
        else:
            candidates = self.mutation.propose(current, local_failure or {})
        if not candidates:
            return None, [], 0
        reference = verified_rate(environment, regression, current, clean_intervention)
        scores = []
        for candidate in candidates:
            challenge_rate = verified_rate(environment, challenge, candidate, intervention)
            regression_rate = verified_rate(environment, regression, candidate, clean_intervention)
            loss = max(0.0, reference - regression_rate)
            complexity = policy_cost(candidate, self.config)
            objective = (
                challenge_rate
                - self.config["mutation"]["regression_weight"] * loss
                - self.config["mutation"]["cost_weight"] * complexity
            )
            scores.append(CandidateScore(candidate, challenge_rate, regression_rate, objective, complexity))
        scores.sort(key=lambda item: (item.objective, item.fit_challenge, -item.complexity_cost), reverse=True)
        steps = len(regression) + len(candidates) * (len(challenge) + len(regression))
        return scores[0], scores, steps


def bootstrap_lcb(differences: list[int], alpha: float, repetitions: int, seed: int) -> float:
    if not differences:
        raise ValueError("Empty paired data")
    if any(value not in (-1, 0, 1) for value in differences):
        raise ValueError("Paired differences must be in {-1, 0, 1}")
    rng = Random(seed)
    n = len(differences)
    means = [sum(differences[rng.randrange(n)] for _ in range(n)) / n for _ in range(repetitions)]
    means.sort()
    return means[max(0, min(repetitions - 1, floor(alpha * repetitions)))]


class IndependentAdmission:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def _paired(self, environment: Environment, tasks: list[Task], candidate: PolicyState, baseline: PolicyState, intervention: str) -> list[int]:
        pairs = []
        for task in tasks:
            before = environment.execute(task, baseline, intervention).reward
            after = environment.execute(task, candidate, intervention).reward
            pairs.append(after - before)
        return pairs

    def assess(self, environment: Environment, baseline: PolicyState, candidate: PolicyState, challenge: list[Task], regression: list[Task], intervention: str, clean_intervention: str, seed: int) -> GateResult:
        if set(t.id for t in challenge) & set(t.id for t in regression):
            raise ValueError("Challenge and regression gating must be disjoint")
        q_pairs = self._paired(environment, challenge, candidate, baseline, intervention)
        b_pairs = self._paired(environment, regression, candidate, baseline, clean_intervention)
        gate = self.config["acceptance"]
        repair_lcb = bootstrap_lcb(q_pairs, gate["alpha"], gate["bootstrap_samples"], seed)
        regression_lcb = bootstrap_lcb(b_pairs, gate["alpha"], gate["bootstrap_samples"], seed + 1)
        repair_pass = repair_lcb > gate["repair_margin"]
        regression_pass = regression_lcb >= -gate["regression_tolerance"]
        return GateResult(
            admitted=repair_pass and regression_pass,
            repair_delta=sum(q_pairs) / len(q_pairs),
            regression_delta=sum(b_pairs) / len(b_pairs),
            repair_lcb=repair_lcb,
            regression_lcb=regression_lcb,
            repair_pass=repair_pass,
            regression_pass=regression_pass,
        )
