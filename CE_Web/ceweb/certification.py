from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .environment import Environment
from .models import Contract, PolicyState
from .predicates import violation
from .tasks import TaskFactory


class CertificationError(RuntimeError):
    pass


def build_contract(template: dict[str, Any]) -> Contract:
    return Contract(
        id=template["id"],
        entity_role=template["entity_role"],
        operation=template["operation"],
        intervention_type=template["intervention_type"],
        precondition=template["precondition"],
        event=template["event"],
        postcondition=template["postcondition"],
        source_site=template["source_site"],
        donor_id=template["donor_id"],
        optional_preconditions=tuple(template.get("optional_preconditions", [])),
    )


class SourceCertifier:
    def __init__(self, config: dict[str, Any], task_factory: TaskFactory):
        self.config = config
        self.tasks = task_factory

    def replay(self, contract: Contract, site: Environment, donor: PolicyState, count: int) -> tuple[float, list[int], list[int]]:
        grounding = site.grounding(contract)
        if grounding is None:
            raise CertificationError(f"Invalid source grounding: {contract.id}")
        tasks = self.tasks.make(site.name, contract.operation, "source_replay", count)
        results = [site.execute(task, donor, grounding.intervention_name) for task in tasks]
        scores = [violation(contract, trial) for trial in results]
        return sum(scores) / count, scores, [trial.reward for trial in results]

    def ensure_recoverability(self, contract: Contract, site: Environment) -> None:
        grounding = site.grounding(contract)
        if grounding is None:
            raise CertificationError(f"Grounding rejected for {contract.id}")
        oracle = PolicyState.from_mapping(self.config["certification"]["oracle_policy"])
        tasks = self.tasks.make(site.name, contract.operation, "source_oracle", self.config["certification"]["oracle_trials"])
        if not all(site.execute(task, oracle, grounding.intervention_name).reward for task in tasks):
            raise CertificationError(f"Source challenge is not recoverable: {contract.id}")

    def certify(self, template: dict[str, Any], site: Environment, donor: PolicyState) -> Contract:
        contract = build_contract(template)
        if contract.source_site != site.name:
            raise CertificationError("Source site mismatch")
        if not getattr(site, "revision", ""):
            raise CertificationError("Source revision missing")
        self.ensure_recoverability(contract, site)
        witness_task = self.tasks.make(site.name, contract.operation, "source_witness", 1)[0]
        grounding = site.grounding(contract)
        if grounding is None:
            raise CertificationError("Missing source grounding")
        witness = site.execute(witness_task, donor, grounding.intervention_name)
        if witness.reward != 0 or not violation(contract, witness):
            raise CertificationError(f"No independently confirmed witness: {contract.id}")
        count = int(self.config["certification"]["replay_trials"])
        minimum = float(self.config["certification"]["min_replay_rate"])
        rate, outcomes, rewards = self.replay(contract, site, donor, count)
        if rate < minimum or any(rewards):
            raise CertificationError(f"Replay certification failed for {contract.id}: {rate:.3f}")
        retained = []
        for precondition in contract.optional_preconditions:
            candidate = Contract(
                **{**contract.to_dict(), "optional_preconditions": tuple(retained + [precondition])}
            )
            candidate = self._with_preconditions(candidate)
            candidate_rate, _, _ = self.replay(
                candidate, site, donor, int(self.config["certification"]["minimization_trials"])
            )
            if candidate_rate < minimum:
                retained.append(precondition)
        final = Contract(
            **{**contract.to_dict(), "optional_preconditions": tuple(retained)}
        )
        final = self._with_preconditions(final)
        final_rate, final_outcomes, final_rewards = self.replay(final, site, donor, count)
        if final_rate < minimum or any(final_rewards):
            raise CertificationError("Final minimized contract did not pass source revalidation")
        certificate = {
            "source_revision": site.revision,
            "verifier": f"{site.name}:independent_state_verifier",
            "witness_task_id": witness_task.id,
            "source_witness": {"claim": witness.completion_claim, "reward": witness.reward},
            "replay_rate": final_rate,
            "replay_outcomes": final_outcomes,
            "replay_count": count,
            "initial_replay_rate": rate,
            "initial_replay_outcomes": outcomes,
            "retained_optional_preconditions": len(retained),
            "certified_at": datetime.now(timezone.utc).isoformat(),
            "protocol": "source_fresh_reset_independent_verifier",
        }
        return final.with_certificate(certificate)

    @staticmethod
    def _with_preconditions(contract: Contract) -> Contract:
        if not contract.optional_preconditions:
            return contract
        return Contract(
            **{
                **contract.to_dict(),
                "precondition": {"all": [contract.precondition, *contract.optional_preconditions]},
            }
        )
