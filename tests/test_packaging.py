"""Deployment files stay consistent with each other and with the app."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def requirements() -> list[str]:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def test_pyproject_dependencies_match_requirements():
    # Vercel installs from pyproject.toml, local setups and Render from requirements.txt.
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dependencies"] == requirements()
    assert project["tool"]["uv"]["package"] is False


def test_vercel_runs_the_app_next_to_the_database():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["regions"] == ["sin1"]  # Neon's closest region to India is Singapore too
    assert "app.py" in config["functions"]
    assert (ROOT / "app.py").read_text(encoding="utf-8").count("app = create_app()") == 1


def test_python_version_is_pinned_for_hosts():
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
