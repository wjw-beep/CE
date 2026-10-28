from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .certification import CertificationError, SourceCertifier
from .config import save_yaml, validate_config
from .environment import build_environments
from .evolution import CandidateFitter, IndependentAdmission, verified_rate
from .graph import EvidenceGraph
from .models import Attempt, Contract, PolicyState, Task
from .predicates import violation
from .tasks import TaskFactory


class EvolutionEngine:
    def __init__(self, config: dict[str, Any]):
        validate_config(config)
        self.config = config
        self.environments = build_environments(config)
        self.tasks = TaskFactory.from_config(config)
        self.graph = EvidenceGraph(config)
        self.certifier = SourceCertifier(config, self.tasks)
        self.fitter = CandidateFitter(config)
        self.admission = IndependentAdmission(config)
        self.certified: list[Contract] = []
        self.rejected: list[dict[str, str]] = []
        self.budget_left = float(config["experiment"]["budget"])
        self.agent_id = str(config["agents"]["recipient"]["id"])
        self.policy = PolicyState.from_mapping(config["agents"]["recipient"]["policy"])
        self._used_tasks: set[str] = set()

    def _assign(self, site: str, operation: str, partition: str, amount: int) -> list[Task]:
        result = self.tasks.make(site, operation, partition, amount)
        if self._used_tasks.intersection(task.id for task in result):
            raise RuntimeError("Data leakage between evaluation partitions")
        self._used_tasks.update(task.id for task in result)
        return result

    def prepare_sources(self) -> None:
        donor_policies = {
            item["id"]: PolicyState.from_mapping(item["policy"])
            for item in self.config["agents"]["donors"]
        }
        for template in self.config["contracts"]:
            try:
                donor = donor_policies[template["donor_id"]]
                site = self.environments[template["source_site"]]
                certified = self.certifier.certify(template, site, donor)
                self.certified.append(certified)
                self.graph.add_certificate(certified)
            except (CertificationError, KeyError, ValueError) as exc:
                self.rejected.append({"id": template["id"], "reason": str(exc)})
        if not self.certified:
            raise RuntimeError("No counterexample passed source certification")

    def _eligible(self) -> list[tuple[Contract, str]]:
        preferred = self.config["agents"]["recipient"]["adaptation_sites"]
        result = []
        for contract in self.certified:
            for name in preferred:
                environment = self.environments[name]
                grounded = environment.grounding(contract) is not None
                self.graph.record_grounding(self.agent_id, contract.id, name, environment.revision, grounded)
                if grounded:
                    result.append((contract, name))
                    break
        return result

    def _clean_intervention(self, site: str) -> str:
        options = self.environments[site].definition["interventions"]
        allowed = self.environments[site].definition["authorized_interventions"]
        for name in allowed:
            if options[name]["type"] == self.config["tasks"]["clean_intervention_type"]:
                return name
        raise ValueError(f"No authorized clean intervention: {site}")

    def _spend(self, amount: float) -> None:
        self.budget_left -= amount
        if self.budget_left < -1e-8:
            raise RuntimeError("Evaluation budget exhausted")

    def evolve(self) -> dict[str, Any]:
        if not self.certified:
            self.prepare_sources()
        settings = self.config["tasks"]
        history = []
        initial_policy = self.policy
        for cycle in range(int(self.config["experiment"]["max_cycles"])):
            eligible = self._eligible()
            chosen = self.graph.choose(self.agent_id, [candidate for candidate, _ in eligible])
            if chosen is None:
                break
            site_name = next(site for candidate, site in eligible if candidate.id == chosen.id)
            environment = self.environments[site_name]
            grounding = environment.grounding(chosen)
            if grounding is None:
                continue
            reproduction_cost = float(self.config["experiment"]["reproduction_cost"])
            fit_worst = float(self.config["mutation"]["max_candidates"]) * (settings["challenge_fit"] + settings["regression_fit"]) + settings["regression_fit"]
            gate_worst = 2 * (settings["challenge_gate"] + settings["regression_gate"])
            required = reproduction_cost + fit_worst + gate_worst
            if self.budget_left < required:
                break
            before = self.policy
            reproduction_task = self._assign(site_name, chosen.operation, "local_reproduction", 1)[0]
            witness = environment.execute(reproduction_task, before, grounding.intervention_name)
            self._spend(reproduction_cost)
            reproduced = bool(violation(chosen, witness))
            if not reproduced:
                attempt = Attempt(self.agent_id, chosen.id, site_name, "not_reproduced", False, False, 0.0, reproduction_cost, before.to_dict(), before.to_dict())
                self.graph.add_attempt(attempt, environment.revision)
                history.append(attempt.to_dict())
                continue
            challenges_fit = self._assign(site_name, chosen.operation, "challenge_fit", settings["challenge_fit"])
            challenges_gate = self._assign(site_name, chosen.operation, "challenge_gate", settings["challenge_gate"])
            regression_fit = self._assign(site_name, chosen.operation, "regression_fit", settings["regression_fit"])
            regression_gate = self._assign(site_name, chosen.operation, "regression_gate", settings["regression_gate"])
            clean = self._clean_intervention(site_name)
            best, candidates, spent_fit = self.fitter.fit(
                environment, before, grounding.intervention_name, challenges_fit, regression_fit, clean,
                {"task": witness.task.id, "trace": witness.trace, "verified_outcome": witness.reward}
            )
            self._spend(spent_fit)
            gate = None
            spent_gate = 0
            if best is not None and best.objective > self.config["mutation"]["minimum_objective"]:
                gate = self.admission.assess(
                    environment, before, best.policy, challenges_gate, regression_gate,
                    grounding.intervention_name, clean, int(self.config["experiment"]["seed"]) + cycle * 2
                )
                spent_gate = 2 * (len(challenges_gate) + len(regression_gate))
                self._spend(spent_gate)
                if gate.admitted:
                    self.policy = best.policy
            cost = reproduction_cost + spent_fit + spent_gate
            attempt = Attempt(
                recipient=self.agent_id,
                contract=chosen.id,
                website=site_name,
                stage="accepted" if gate and gate.admitted else "rejected",
                reproduced=True,
                accepted=bool(gate and gate.admitted),
                gain=gate.repair_delta if gate else 0.0,
                cost=cost,
                policy_before=before.to_dict(),
                policy_after=self.policy.to_dict(),
                gate=gate.to_dict() if gate else {},
            )
            self.graph.add_attempt(attempt, environment.revision)
            history.append({**attempt.to_dict(), "fit_candidates": [item.to_dict() for item in candidates]})
        return {
            "agent_id": self.agent_id,
            "policy_before": initial_policy.to_dict(),
            "policy_after": self.policy.to_dict(),
            "certified_contracts": len(self.certified),
            "rejected_contracts": self.rejected,
            "budget_initial": self.config["experiment"]["budget"],
            "budget_remaining": self.budget_left,
            "history": history,
        }

    def evaluate_heldout(self, initial_policy: PolicyState) -> list[dict[str, Any]]:
        output = []
        settings = self.config["tasks"]
        for site_name in self.config["agents"]["recipient"].get("heldout_sites", []):
            environment = self.environments[site_name]
            clean = self._clean_intervention(site_name)
            for contract in self.certified:
                grounding = environment.grounding(contract)
                if grounding is None:
                    continue
                tasks = self._assign(site_name, contract.operation, "heldout", settings["heldout_per_contract"])
                baseline_trials = [environment.execute(task, initial_policy, grounding.intervention_name) for task in tasks]
                evolved_trials = [environment.execute(task, self.policy, grounding.intervention_name) for task in tasks]
                result = {
                    "site": site_name,
                    "contract": contract.id,
                    "sample_count": len(tasks),
                    "frozen_reward": sum(t.reward for t in baseline_trials) / len(tasks),
                    "evolved_reward": sum(t.reward for t in evolved_trials) / len(tasks),
                    "frozen_false_completion_count": sum(t.false_completion for t in baseline_trials),
                    "evolved_false_completion_count": sum(t.false_completion for t in evolved_trials),
                    "forward_transfer": sum(b.reward - a.reward for a, b in zip(baseline_trials, evolved_trials)) / len(tasks),
                }
                output.append(result)
            clean_tasks = self._assign(site_name, self.config["tasks"]["default_operation"], "heldout_clean", settings["heldout_clean"])
            output.append(
                {
                    "site": site_name,
                    "contract": "clean",
                    "sample_count": len(clean_tasks),
                    "frozen_reward": verified_rate(environment, clean_tasks, initial_policy, clean),
                    "evolved_reward": verified_rate(environment, clean_tasks, self.policy, clean),
                }
            )
        return output

    def run(self, output_dir: str | Path | None = None) -> dict[str, Any]:
        baseline = self.policy
        result = self.evolve()
        result["heldout"] = self.evaluate_heldout(baseline)
        if output_dir is not None:
            destination = Path(output_dir)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            (destination / "evidence_graph.json").write_text(json.dumps(self.graph.export(), indent=2, ensure_ascii=False), encoding="utf-8")
            save_yaml(destination / "policy.yaml", {"agent": self.agent_id, "policy": self.policy.to_dict()})
            save_yaml(destination / "certified_contracts.yaml", {"contracts": [c.to_dict() for c in self.certified]})
        return result
