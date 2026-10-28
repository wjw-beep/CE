from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from math import sqrt
from typing import Any

from .models import Attempt, Contract


class EvidenceGraph:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.attempts: list[Attempt] = []
        self.grounding_observations: set[tuple[str, str, str, str]] = set()

    def node(self, kind: str, identifier: str, **attributes: Any) -> str:
        name = f"{kind}:{identifier}"
        if name not in self.nodes:
            self.nodes[name] = {"kind": kind, "id": identifier, **attributes}
        return name

    def edge(self, source: str, target: str, kind: str, outcome: Any, protocol: str, website_revision: str, **details: Any) -> None:
        self.edges.append(
            {
                "source": source,
                "target": target,
                "kind": kind,
                "outcome": outcome,
                "protocol": protocol,
                "website_revision": website_revision,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **details,
            }
        )

    def add_certificate(self, contract: Contract) -> None:
        agent = self.node("agent", contract.donor_id)
        revision = contract.certificate["source_revision"]
        site = self.node("website", f"{contract.source_site}@{revision}", website=contract.source_site, revision=revision)
        obj = self.node("contract", contract.id, source_revision=contract.certificate["source_revision"])
        self.edge(agent, obj, "donated", True, "source_witness", contract.certificate["source_revision"])
        self.edge(site, obj, "certified", True, contract.certificate["protocol"], contract.certificate["source_revision"], replay_rate=contract.certificate["replay_rate"])

    def record_grounding(self, recipient: str, contract_id: str, website: str, revision: str, success: bool) -> None:
        signature = (recipient, contract_id, website, revision)
        if signature in self.grounding_observations:
            return
        self.grounding_observations.add(signature)
        agent = self.node("agent", recipient)
        site = self.node("website", f"{website}@{revision}", website=website, revision=revision)
        contract = self.node("contract", contract_id)
        self.edge(contract, site, "grounding_checked", success, "typed_authorized_observable", revision, recipient=recipient)
        self.edge(agent, contract, "considered", success, "eligible_pool", revision, website=website)

    def add_attempt(self, attempt: Attempt, revision: str) -> None:
        self.attempts.append(attempt)
        agent = self.node("agent", attempt.recipient)
        site = self.node("website", f"{attempt.website}@{revision}", website=attempt.website, revision=revision)
        contract = self.node("contract", attempt.contract)
        policy = self.node("policy", f"{attempt.recipient}:{len(self.attempts)}", parameters=attempt.policy_after)
        self.edge(agent, site, "grounded", True, "typed_grounding", revision, contract=attempt.contract)
        self.edge(contract, site, "reproduced", attempt.reproduced, "local_independent_verifier", revision, cost=attempt.cost)
        self.edge(site, policy, "repair_admitted", attempt.accepted, "fresh_paired_gates", revision, gate=attempt.gate, gain=attempt.gain)

    def attempted(self, recipient: str, contract: str) -> list[Attempt]:
        return [a for a in self.attempts if a.recipient == recipient and a.contract == contract]

    def _posterior(self, recipient: str, contract: Contract) -> tuple[float, float, float, float]:
        selection = self.config["selection"]
        history = self.attempted(recipient, contract.id)
        strength = float(selection["reproduction_prior_strength"])
        source = float(contract.certificate["replay_rate"])
        alpha = strength * source + sum(a.reproduced for a in history)
        beta = strength * (1 - source) + sum(not a.reproduced for a in history)
        probability = alpha / (alpha + beta)
        eligible = [a.gain for a in history if a.reproduced]
        if eligible:
            gain = (sum(eligible) + selection["gain_prior"] * selection["gain_prior_strength"]) / (len(eligible) + selection["gain_prior_strength"])
            variance = sum((v - gain) ** 2 for v in eligible) / max(1, len(eligible))
            sigma = sqrt((variance + selection["uncertainty_prior"]) / (len(eligible) + 1))
        else:
            gain = float(selection["gain_prior"])
            sigma = float(selection["uncertainty_prior"])
        costs = [a.cost for a in history]
        cost = (sum(costs) + selection["cost_prior"]) / (len(costs) + 1)
        return probability, gain, sigma, cost

    def score(self, recipient: str, contract: Contract) -> float:
        selection = self.config["selection"]
        p, g, s, k = self._posterior(recipient, contract)
        count = len(self.attempted(recipient, contract.id))
        redundancy = min(1.0, count / float(selection["redundancy_scale"]))
        return p * (g + selection["uncertainty_weight"] * s) / (k + selection["cost_epsilon"]) - selection["redundancy_weight"] * redundancy

    def choose(self, recipient: str, contracts: list[Contract]) -> Contract | None:
        limit = int(self.config["selection"]["max_attempts_per_contract"])
        pool = [c for c in contracts if len(self.attempted(recipient, c.id)) < limit]
        if not pool:
            return None
        return max(pool, key=lambda item: (self.score(recipient, item), item.id))

    def export(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "attempts": [a.to_dict() for a in self.attempts],
            "grounding_observations": sorted([list(item) for item in self.grounding_observations]),
        }
