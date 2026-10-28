from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config, validate_config
from .engine import EvolutionEngine


def main() -> None:
    parser = argparse.ArgumentParser(prog="ceweb")
    parser.add_argument("command", choices=["run", "validate"])
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--output", default="runs/demo")
    options = parser.parse_args()
    configuration = load_config(options.config)
    validate_config(configuration)
    if options.command == "validate":
        print(json.dumps({"valid": True, "contracts": len(configuration["contracts"]), "sites": len(configuration["sites"])}, ensure_ascii=False))
        return
    results = EvolutionEngine(configuration).run(Path(options.output))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
