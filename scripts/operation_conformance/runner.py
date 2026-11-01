"""Run a temporary candidate consumer against an owned disposable database."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid


REQUIRED = {
    "TestComposedPermissionBoundary", "TestComposedConcurrentCAS",
    "TestComposedOutboxRollback",
}


def validate_results(output):
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    if not all(isinstance(event, dict) and isinstance(event.get("Action"), str) for event in events):
        raise ValueError("invalid Go test event schema")
    if any(event.get("Action") == "skip" for event in events):
        raise ValueError("required composition evidence skipped")
    passed = {event.get("Test") for event in events if event.get("Action") == "pass"}
    if not REQUIRED <= passed:
        raise ValueError(f"missing composition evidence: {sorted(REQUIRED - passed)}")
    if any(event.get("Action") == "fail" for event in events):
        raise ValueError("composition test reported failure")
    return sorted(REQUIRED)


def command(arguments, *, cwd=None, env=None, timeout=180):
    result = subprocess.run(
        arguments, cwd=cwd, env=env, capture_output=True, text=True,
        timeout=timeout, check=False,
    )
    if result.returncode:
        # Arguments contain only synthetic credentials; never accept an external
        # database URL or inherit one as the target database.
        raise RuntimeError(f"{arguments[0]} failed: {result.stdout}{result.stderr}")
    return result.stdout


OWNER_LABEL = "pluto.operation-conformance.owner"


def remove_owned(name, owner):
    try:
        inspected = command(["docker", "inspect", name], timeout=30)
    except RuntimeError as error:
        # Only the engine's explicit absent-object response is harmless. Daemon
        # outages, permission failures and timeouts remain cleanup failures.
        if f"No such object: {name}" in str(error) or f"No such container: {name}" in str(error):
            return
        raise
    objects = json.loads(inspected)
    if not isinstance(objects, list) or len(objects) != 1:
        raise ValueError("invalid owned-container inspection")
    container = objects[0]
    if not isinstance(container, dict):
        raise ValueError("invalid owned-container object")
    config = container.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict) or labels.get(OWNER_LABEL) != owner:
        raise ValueError("container ownership mismatch; refusing removal")
    identity = container.get("Id")
    if not isinstance(identity, str) or not identity:
        raise ValueError("owned container identity missing")
    # Delete the inspected immutable ID, never a name another resource could
    # acquire between inspection and removal.
    try:
        command(["docker", "rm", "--force", identity], timeout=30)
    except RuntimeError as error:
        if f"No such container: {identity}" not in str(error):
            raise


def run(core, rbac, image="postgres:17-alpine"):
    report = {
        "version": 1, "passed": False, "candidateOnly": True,
        "coverage": "Private application composition with local candidates; no generated-release receipt adoption claim.",
        "errors": [],
    }
    container = None
    owner = uuid.uuid4().hex
    try:
        report["sources"] = {}
        for name, directory in (("core", core), ("rbac", rbac)):
            if not (directory / "go.mod").is_file():
                raise ValueError(f"missing {name} module")
            report["sources"][name] = {
                "path": str(directory),
                "revision": command(["git", "-C", str(directory), "rev-parse", "HEAD"]).strip(),
                "trackedDirty": bool(command(["git", "-C", str(directory), "diff", "HEAD", "--"]).strip()),
                "untrackedSource": [name for name in command(["git", "-C", str(directory), "ls-files", "--others", "--exclude-standard"]).splitlines()
                                    if Path(name).suffix in {".go", ".mod", ".sum"}],
            }
        container = "pluto-operation-" + owner
        command([
            "docker", "run", "--detach", "--rm", "--name", container,
            "--label", f"{OWNER_LABEL}={owner}",
            "--publish", "127.0.0.1::5432",
            "--env", "POSTGRES_PASSWORD=operation-conformance", "--env",
            "POSTGRES_DB=conformance", image,
        ])
        report["database"] = {"image": image, "imageID": command([
            "docker", "inspect", "--format", "{{.Image}}", container,
        ]).strip(), "disposable": True}
        deadline = time.monotonic() + 45
        while True:
            result = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", "postgres"],
                capture_output=True, timeout=10, check=False,
            )
            if result.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("disposable PostgreSQL did not become ready")
            time.sleep(0.25)
        port = command(["docker", "port", container, "5432/tcp"]).strip().rsplit(":", 1)[1]
        with tempfile.TemporaryDirectory(prefix="pluto-operation-conformance-") as temporary:
            consumer = Path(temporary)
            source = Path(__file__).with_name("consumer_test.go.txt")
            report["fixtureSHA256"] = hashlib.sha256(source.read_bytes()).hexdigest()
            shutil.copyfile(source, consumer / "consumer_test.go")
            environment = dict(os.environ, GOWORK="off", GOFLAGS="")
            environment["PLUTO_OPERATION_DATABASE_URL"] = (
                f"postgres://postgres:operation-conformance@127.0.0.1:{port}/conformance?sslmode=disable"
            )
            command(["go", "mod", "init", "conformance.invalid/operation"], cwd=consumer, env=environment)
            for module, directory in (
                ("github.com/plutoframework/pluto-core", core),
                ("github.com/plutoframework/pluto-rbac", rbac),
                ("github.com/plutoframework/pluto-core/examples/shipments/postgres", core / "examples/shipments/postgres"),
            ):
                command(["go", "mod", "edit", f"-require={module}@v0.0.0", f"-replace={module}={directory}"], cwd=consumer, env=environment)
            command(["go", "mod", "tidy"], cwd=consumer, env=environment)
            output = command(["go", "test", "-race", "-count=1", "-json", "./..."], cwd=consumer, env=environment)
            report["executed"] = validate_results(output)
            report["passed"] = True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["errors"].append(str(error))
    finally:
        if container:
            try:
                remove_owned(container, owner)
                report.setdefault("database", {})["removed"] = True
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                report["errors"].append(f"database cleanup failed: {error}")
                report["passed"] = False
    return report
