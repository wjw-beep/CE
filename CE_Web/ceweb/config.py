from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        result = yaml.safe_load(handle)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a YAML mapping: {path}")
    return result


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _load_tree(path: Path, seen: set[Path]) -> dict[str, Any]:
    path = path.resolve()
    if path in seen:
        raise ValueError("Circular YAML extends chain")
    current = load_yaml(path)
    extends = current.pop("extends", None)
    for key in ("site_config", "contract_config"):
        if key in current:
            current[key] = str((path.parent / str(current[key])).resolve())
    if extends is None:
        return current
    base = _load_tree(path.parent / str(extends), seen | {path})
    return _merge(base, current)


def load_config(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    config = _load_tree(resolved, set())
    for key, target in (("site_config", "sites"), ("contract_config", "contracts")):
        if key not in config:
            raise ValueError(f"Missing configuration key: {key}")
        config[target] = load_yaml(config[key])[target]
    overrides = config.pop("sites_overrides", {})
    for site, changes in overrides.items():
        if site not in config["sites"]:
            raise ValueError(f"Cannot override unknown site: {site}")
        config["sites"][site] = _merge(config["sites"][site], changes)
    required = ("experiment", "certification", "selection", "mutation", "acceptance", "tasks", "agents")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Missing configuration sections: {missing}")
    config["_config_dir"] = str(resolved.parent)
    return config


def save_yaml(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(value, handle, allow_unicode=True, sort_keys=False)


def validate_config(config: dict[str, Any]) -> None:
    if not config["sites"] or not config["contracts"]:
        raise ValueError("At least one site and contract are required")
    cert = config["certification"]
    if cert["replay_trials"] < 1 or not 0 < cert["min_replay_rate"] <= 1:
        raise ValueError("Invalid source certification parameters")
    if cert["minimization_trials"] < 1:
        raise ValueError("Invalid minimization trial count")
    acc = config["acceptance"]
    if not 0 < acc["alpha"] < 1 or acc["bootstrap_samples"] < 100:
        raise ValueError("Invalid confidence settings")
    if config["experiment"]["budget"] <= 0:
        raise ValueError("Budget must be positive")
    if config["tasks"]["challenge_fit"] < 1 or config["tasks"]["challenge_gate"] < 1:
        raise ValueError("Challenge partitions must be nonempty")
    if config["tasks"]["regression_fit"] < 1 or config["tasks"]["regression_gate"] < 1:
        raise ValueError("Regression partitions must be nonempty")
    for contract in config["contracts"]:
        if contract["source_site"] not in config["sites"]:
            raise ValueError("Unknown contract source site")
    for name, site in config["sites"].items():
        for key in ("revision", "entity_roles", "operation_bindings", "interventions", "authorized_interventions"):
            if key not in site:
                raise ValueError(f"Site {name} missing {key}")
        if not site.get("observable_fields"):
            raise ValueError(f"Site {name} missing observable fields")
        for operation in site["operation_bindings"].values():
            if isinstance(operation, dict) and (not operation.get("entity_role") or not operation.get("action")):
                raise ValueError(f"Site {name} has an invalid operation binding")
        for intervention in site["interventions"].values():
            chance = float(intervention.get("drop_probability", 1.0))
            if not 0 <= chance <= 1:
                raise ValueError(f"Invalid intervention drop probability at {name}")
        if len(site["authorized_interventions"]) != len(set(site["authorized_interventions"])):
            raise ValueError(f"Duplicate authorization for {name}")
        driver = site.get("driver", "memory")
        if driver not in ("memory", "http"):
            raise ValueError(f"Unsupported environment driver: {driver}")
        if driver == "http":
            settings = site.get("http", {})
            if not settings.get("base_url") or float(settings.get("timeout_seconds", 0)) <= 0:
                raise ValueError("HTTP environment requires URL and timeout")
            required_endpoints = {"reset", "intervene", "inspect", "write", "read"}
            if not required_endpoints.issubset(settings.get("endpoints", {})):
                raise ValueError("Missing HTTP environment endpoints")
    if config["mutation"]["max_changed_fields"] < 1:
        raise ValueError("At least one mutation field must be permitted")
    strategy = config["mutation"].get("strategy", "enumerate")
    if strategy not in ("enumerate", "llm"):
        raise ValueError("Invalid mutation strategy")
    if strategy == "llm":
        model = config["mutation"].get("model", {})
        if not all(model.get(key) for key in ("base_url", "model", "api_key_env", "timeout_seconds")):
            raise ValueError("LLM mutation requires a model provider configuration")
