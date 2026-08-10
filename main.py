# main.py
from __future__ import annotations

import sys
import os


def _option_value(argv: list[str], option: str) -> str | None:
    try:
        index = argv.index(option)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return ""
    return str(argv[index + 1])


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--initial-setup" in args:
        from src.initial_setup_wizard import run_initial_setup_wizard

        seed_path = _option_value(args, "--seed")
        return run_initial_setup_wizard(seed_path=seed_path or None)

    if getattr(sys, "frozen", False):
        app_root = os.path.dirname(os.path.abspath(sys.executable))
        config_dir = os.path.join(app_root, "config")
        required = os.path.join(config_dir, "initial_configuration.required")
        pending = os.path.join(config_dir, "initial_configuration.pending.json")
        if os.path.exists(required) or os.path.exists(pending):
            from src.initial_setup_wizard import run_initial_setup_wizard

            return run_initial_setup_wizard(
                seed_path=os.path.join(config_dir, "config.json")
            )

    from src.app import run_app

    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
