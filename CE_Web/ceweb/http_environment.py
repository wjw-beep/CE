from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .environment import InMemoryWebEnvironment
from .models import PolicyState, Task, Trial


class HTTPWebEnvironment(InMemoryWebEnvironment):
    def __init__(self, name: str, definition: dict[str, Any]):
        super().__init__(name, definition)
        self.http = definition["http"]
        self.base_url = str(self.http["base_url"]).rstrip("/")
        self.timeout = float(self.http["timeout_seconds"])
        self.endpoints = self.http["endpoints"]

    def _request(self, endpoint: str, method: str, payload: dict[str, Any] | None = None, query: dict[str, str] | None = None) -> dict[str, Any]:
        path = str(self.endpoints[endpoint])
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        credential_env = self.http.get("bearer_token_env")
        if credential_env:
            token = os.environ.get(str(credential_env))
            if not token:
                raise ValueError(f"Environment variable required: {credential_env}")
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"External environment request failed: {endpoint}") from exc
        if not isinstance(result, dict):
            raise ValueError("External environment must return a JSON object")
        return result

    def execute(self, task: Task, policy: PolicyState, intervention_name: str) -> Trial:
        if task.site != self.name:
            raise ValueError("Task and site mismatch")
        if task.operation not in self.definition["operation_bindings"]:
            raise ValueError("Unsupported operation")
        if intervention_name not in self.definition["authorized_interventions"]:
            raise PermissionError("Unauthorized intervention")
        intervention = self.definition["interventions"][intervention_name]
        if not intervention.get("recoverable", False):
            raise ValueError("Nonrecoverable intervention")
        reset = self._request("reset", "POST", {
            "task_id": task.id,
            "record_id": task.record_id,
            "initial_value": task.initial_value,
            "target_value": task.target_value,
        })
        if not reset.get("reset", False):
            raise RuntimeError("External environment refused reset")
        seeded = self._request("intervene", "POST", {
            "record_id": task.record_id,
            "intervention": intervention_name,
            "type": intervention["type"],
        })
        if not seeded.get("applied", False):
            raise RuntimeError("External environment refused intervention")
        operation = self.definition["operation_bindings"][task.operation]
        binding = operation["action"] if isinstance(operation, dict) else operation
        initial_state = self._request("inspect", "GET", query={"record_id": task.record_id})
        initial = {"record": {"value": initial_state["value"], "exists": bool(initial_state["exists"])}, "permission": {"write": True}}
        writes = 0
        reads = 0
        visible_ack = False
        verified_read = False
        observations = []
        for attempt in range(policy.max_retries + 1):
            write = self._request("write", "POST", {
                "record_id": task.record_id,
                "value": task.target_value,
                "operation": binding,
            })
            writes += 1
            acknowledged = bool(write["acknowledged"])
            visible_ack = visible_ack or acknowledged
            observations.append({"action": "write", "attempt": attempt, "acknowledged": acknowledged})
            if not policy.verify_after_write:
                break
            for _ in range(policy.confirmation_reads):
                observable = self._request("read", "GET", query={"record_id": task.record_id})
                reads += 1
                observed_value = observable["value"]
                observations.append({"action": "read", "value": observed_value})
                if observed_value == task.target_value:
                    verified_read = True
                    break
            if verified_read:
                break
        final_state = self._request("inspect", "GET", query={"record_id": task.record_id})
        final = {"record": {"value": final_state["value"], "exists": bool(final_state["exists"])}}
        claim = int(verified_read if policy.verify_after_write else visible_ack and policy.claim_on_ack)
        trace = {
            "acknowledged": visible_ack,
            "completion_claim": bool(claim),
            "verified_read": verified_read,
            "write_attempts": writes,
            "read_attempts": reads,
            "observations": observations,
        }
        return Trial(task, policy, initial, final, trace, int(final_state["value"] == task.target_value), claim, writes + reads, intervention_name)
