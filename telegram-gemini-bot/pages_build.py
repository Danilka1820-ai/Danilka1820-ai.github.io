"""Request a GitHub Pages build without blocking Telegram update acknowledgement."""
from __future__ import annotations

import os
import subprocess
import sys


def request_build(repository: str, run=subprocess.run) -> bool:
    result = run(
        ["gh", "api", "--method", "POST", f"repos/{repository}/pages/builds"],
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        print("Запрос сборки GitHub Pages принят.")
        return True
    print(
        f"::warning::Не удалось запросить сборку GitHub Pages "
        f"(код завершения {result.returncode}); повторим в следующем цикле."
    )
    return False


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1]:
        raise SystemExit("Использование: pages_build.py OWNER/REPOSITORY")
    if request_build(sys.argv[1]):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("requested=true\n")
    else:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("requested=false\n")
