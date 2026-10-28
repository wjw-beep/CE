from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import Request, urlopen

from .models import PolicyState


class OpenAICompatibleMutator:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.provider = config["mutation"]["model"]

    def propose(self, current: PolicyState, local_failure: dict[str, Any]) -> list[PolicyState]:
        spec = self.provider
        token = os.environ.get(spec["api_key_env"])
        if not token:
            raise ValueError(f"Missing API key environment variable: {spec['api_key_env']}")
        legal = self.config["mutation"]["allowed_values"]
        request_content = {
            "current_policy": current.to_dict(),
            "recipient_verified_failure": local_failure,
            "allowed_values": legal,
            "max_changed_fields": self.config["mutation"]["max_changed_fields"],
            "max_candidates": self.config["mutation"]["max_candidates"],
        }
        payload = {
            "model": spec["model"],
            "temperature": spec.get("temperature", 0),
            "messages": [
                {
                    "role": "system",
                    "content": "Propose a JSON object with key candidates containing a list of policy change dictionaries. Use only permitted keys and values. Do not request new capabilities or permissions. Do not depend on any donor repair.",
                },
                {"role": "user", "content": json.dumps(request_content)},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        request = Request(
            spec["base_url"].rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=float(spec["timeout_seconds"])) as response:
            result = json.loads(response.read().decode("utf-8"))
        contents = result["choices"][0]["message"]["content"]
        data = json.loads(contents)
        options = data["candidates"]
        if not isinstance(options, list):
            raise ValueError("Model must return a candidate list")
        candidates: list[PolicyState] = []
        seen = set()
        for changes in options:
            if not isinstance(changes, dict) or not changes:
                continue
            if len(changes) > int(self.config["mutation"]["max_changed_fields"]):
                continue
            if any(key not in legal or value not in legal[key] for key, value in changes.items()):
                continue
            candidate = current.evolve(changes)
            if candidate == current:
                continue
            signature = json.dumps(candidate.to_dict(), sort_keys=True)
            if signature not in seen:
                candidates.append(candidate)
                seen.add(signature)
            if len(candidates) >= int(self.config["mutation"]["max_candidates"]):
                break
        return candidates
