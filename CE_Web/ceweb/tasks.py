from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Task


@dataclass
class TaskFactory:
    config: dict[str, Any]
    next_seed: int

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> TaskFactory:
        return cls(config=config, next_seed=int(config["tasks"]["seed_start"]))

    def make(self, site: str, operation: str, partition: str, count: int) -> list[Task]:
        settings = self.config["tasks"]
        output = []
        for _ in range(count):
            seed = self.next_seed
            self.next_seed += 1
            output.append(
                Task(
                    id=f"{partition}:{site}:{seed}",
                    site=site,
                    seed=seed,
                    record_id=f"{settings['record_prefix']}-{seed}",
                    initial_value=str(settings["initial_value"]),
                    target_value=f"{settings['target_prefix']}-{seed}",
                    operation=operation,
                    partition=partition,
                )
            )
        return output
