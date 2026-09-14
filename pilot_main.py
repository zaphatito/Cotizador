"""Entrada exclusiva del instalador piloto, con configuración inicial separada."""
import os
import subprocess
import sys
from pathlib import Path


def main():
    if "--pilot-self-check" in sys.argv:
        from tools.pilot_self_check import run_self_check
        return run_self_check(sys.argv[sys.argv.index("--pilot-self-check") + 1])
    if "--configure-pilot" in sys.argv:
        from src.initial_setup_wizard import run_initial_setup_wizard
        return run_initial_setup_wizard()

    from src.paths import DATA_DIR
    marker = Path(DATA_DIR) / "pilot-setup.complete"
    if not marker.exists():
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve()))
        command.append("--configure-pilot")
        result = subprocess.run(command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            return result.returncode
        marker.write_text("Configuración piloto completada.\n", encoding="utf-8")
    from src.app import run_app
    run_app()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        if "--pilot-self-check" not in sys.argv:
            raise
        import traceback
        target = Path(sys.argv[sys.argv.index("--pilot-self-check") + 1])
        target.mkdir(parents=True, exist_ok=True)
        (target / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise SystemExit(1)
