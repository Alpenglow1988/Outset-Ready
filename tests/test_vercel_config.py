import json
import os
import subprocess
from pathlib import Path


def test_vercel_only_builds_main():
    config = json.loads((Path(__file__).parents[1] / "vercel.json").read_text())
    command = config["ignoreCommand"]

    branch_environment = {**os.environ, "VERCEL_GIT_COMMIT_REF": "build/example"}
    main_environment = {**os.environ, "VERCEL_GIT_COMMIT_REF": "main"}

    branch = subprocess.run(command, shell=True, env=branch_environment, check=False)
    main = subprocess.run(command, shell=True, env=main_environment, check=False)

    assert branch.returncode == 0  # Vercel ignores this build.
    assert main.returncode == 1  # Vercel continues this build.
