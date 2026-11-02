"""Dependency security checks for all tracked modules and npm lockfiles."""

from pathlib import Path
import subprocess


def run():
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], text=True
    ).split("\0")
    modules = sorted(Path(p).parent for p in tracked if p.endswith("go.mod"))
    locks = sorted(
        Path(p).parent for p in tracked if p.endswith("package-lock.json")
    )
    if modules:
        subprocess.run(
            ["go", "install", "golang.org/x/vuln/cmd/govulncheck@v1.9.0"],
            check=True,
        )
        tool = (
            Path(
                subprocess.check_output(
                    ["go", "env", "GOPATH"], text=True
                ).strip()
            )
            / "bin/govulncheck"
        )
        for module in modules:
            subprocess.run([str(tool), "./..."], cwd=module, check=True)
    for directory in locks:
        subprocess.run(
            ["npm", "audit", "--package-lock-only", "--audit-level=high"],
            cwd=directory,
            check=True,
        )
    if not modules and not locks:
        print(
            "No third-party Go or npm dependency manifests in this repository."
        )


if __name__ == "__main__":
    run()
