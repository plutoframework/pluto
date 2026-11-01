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

from operation_conformance.style import check


CONSUMER = "conformance.invalid/operation"
CORE_DATA = "github.com/plutoframework/pluto-core/examples/shipments/postgres"
CORE_DOMAIN = "github.com/plutoframework/pluto-core/examples/shipments"
JOBS_DATA = "github.com/plutoframework/pluto-jobs/stores/postgres"
PACKAGES = ("./...", CORE_DATA, CORE_DOMAIN, JOBS_DATA)
REQUIRED = (
    {
        CONSUMER + "::" + name
        for name in (
            "TestComposedNormalizedDispatch",
            "TestComposedCurrentGateCAS",
            "TestComposedExport500Cap",
            "TestComposedTypedDeliveryBound",
            "TestComposedIdentityResourceBoundary",
            *(
                "TestComposedIdentityResourceBoundary/" + mode
                for mode in (
                    "zero",
                    "foreign",
                    "expired",
                    "revoked",
                    "cross-scope",
                    "resource",
                    "expired-execution",
                    "revoked-before-write",
                )
            ),
        )
    }
    | {
        CORE_DATA + "::" + name
        for name in (
            "TestPostgresAuthorizedScopedCache",
            "TestPostgresVisiblePaginationAndBoundedExport",
            "TestPostgresDeclaredAtomicDispatch",
            *(
                "TestPostgresDeclaredAtomicDispatch/" + mode
                for mode in (
                    "none",
                    "audit",
                    "delivery",
                    "completion",
                    "commit",
                )
            ),
            "TestPostgresAuthoritativeGuardFacts",
            "TestPostgresConcurrentIdempotentDispatch",
            "TestPostgresTypedDeliveryAfterCommit",
        )
    }
    | {
        CORE_DOMAIN + "::TestNormalizedPrivateInputs",
        JOBS_DATA + "::TestDelayRetryAndCrashExhaustion",
    }
)


def validate_results(output):
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    if not all(
        isinstance(event, dict) and isinstance(event.get("Action"), str)
        for event in events
    ):
        raise ValueError("invalid Go test event schema")
    if any(
        "Test" in event
        and (
            not isinstance(event["Test"], str)
            or not event["Test"]
            or not isinstance(event.get("Package"), str)
            or not event["Package"]
        )
        for event in events
    ):
        raise ValueError("invalid named Go test event schema")
    if any(
        event.get("Action") == "skip" and event.get("Test") for event in events
    ):
        raise ValueError("required composition evidence skipped")
    passed = {
        event.get("Package", "") + "::" + event["Test"]
        for event in events
        if event.get("Action") == "pass" and isinstance(event.get("Test"), str)
    }
    if not REQUIRED <= passed:
        raise ValueError(
            f"missing composition evidence: {sorted(REQUIRED - passed)}"
        )
    if any(event.get("Action") == "fail" for event in events):
        raise ValueError("composition test reported failure")
    return sorted(REQUIRED)


def command(arguments, *, cwd=None, env=None, timeout=180):
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        # Arguments contain only synthetic credentials; never accept an external
        # database URL or inherit one as the target database.
        raise RuntimeError(
            f"{arguments[0]} failed: {result.stdout}{result.stderr}"
        )
    return result.stdout


OWNER_LABEL = "pluto.operation-conformance.owner"


def remove_owned(name, owner):
    try:
        inspected = command(["docker", "inspect", name], timeout=30)
    except RuntimeError as error:
        # Only the engine's explicit absent-object response is harmless. Daemon
        # outages, permission failures and timeouts remain cleanup failures.
        if f"No such object: {name}" in str(
            error
        ) or f"No such container: {name}" in str(error):
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


def run(core, rbac, auth, jobs, image="postgres:17-alpine"):
    report = {
        "version": 1,
        "passed": False,
        "candidateOnly": True,
        "coverage": (
            "Live Auth/RBAC, Core Data/operations and typed Jobs candidate "
            "composition plus upstream named SQL proofs; "
            "no generated-release adoption claim."
        ),
        "errors": [],
    }
    container = None
    owner = uuid.uuid4().hex
    try:
        style_errors = check(Path(__file__).resolve().parents[2])
        if style_errors:
            raise ValueError("\n".join(style_errors))
        report["sources"] = {}
        sources = (
            ("core", core),
            ("rbac", rbac),
            ("auth", auth),
            ("jobs", jobs),
        )
        for name, directory in sources:
            if not (directory / "go.mod").is_file():
                raise ValueError(f"missing {name} module")
        for name, directory in sources:
            report["sources"][name] = {
                "path": str(directory),
                "revision": command(
                    ["git", "-C", str(directory), "rev-parse", "HEAD"]
                ).strip(),
                "trackedDirty": bool(
                    command(
                        ["git", "-C", str(directory), "diff", "HEAD", "--"]
                    ).strip()
                ),
                "untrackedSource": [
                    name
                    for name in command(
                        [
                            "git",
                            "-C",
                            str(directory),
                            "ls-files",
                            "--others",
                            "--exclude-standard",
                        ]
                    ).splitlines()
                    if Path(name).suffix in {".go", ".mod", ".sum"}
                ],
            }
        container = "pluto-operation-" + owner
        command(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                container,
                "--label",
                f"{OWNER_LABEL}={owner}",
                "--publish",
                "127.0.0.1::5432",
                "--env",
                "POSTGRES_PASSWORD=operation-conformance",
                "--env",
                "POSTGRES_DB=conformance",
                image,
            ]
        )
        report["database"] = {
            "image": image,
            "imageID": command(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Image}}",
                    container,
                ]
            ).strip(),
            "disposable": True,
        }
        deadline = time.monotonic() + 45
        while True:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("disposable PostgreSQL did not become ready")
            time.sleep(0.25)
        port = (
            command(["docker", "port", container, "5432/tcp"])
            .strip()
            .rsplit(":", 1)[1]
        )
        with tempfile.TemporaryDirectory(
            prefix="pluto-operation-conformance-"
        ) as temporary:
            consumer = Path(temporary)
            digest = hashlib.sha256()
            report["fixtureFiles"] = []
            for source in sorted(Path(__file__).parent.glob("*.go.txt")):
                content = source.read_bytes()
                digest.update(source.name.encode() + b"\0" + content)
                report["fixtureFiles"].append(
                    {
                        "name": source.name,
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
                shutil.copyfile(
                    source, consumer / source.name.removesuffix(".txt")
                )
            report["fixtureSHA256"] = digest.hexdigest()
            report["goVersion"] = command(["go", "version"]).strip()
            environment = dict(os.environ, GOWORK="off", GOFLAGS="")
            environment["PLUTO_OPERATION_DATABASE_URL"] = (
                "postgres://postgres:operation-conformance@"
                f"127.0.0.1:{port}/conformance?sslmode=disable"
            )
            environment["PLUTO_CORE_TEST_DATABASE_URL"] = environment[
                "PLUTO_OPERATION_DATABASE_URL"
            ]
            environment["PLUTO_CORE_REQUIRE_DATABASE"] = "1"
            environment["PLUTO_JOBS_TEST_DATABASE_URL"] = environment[
                "PLUTO_OPERATION_DATABASE_URL"
            ]
            environment["PLUTO_JOBS_REQUIRE_DATABASE"] = "1"
            command(
                ["go", "mod", "init", CONSUMER], cwd=consumer, env=environment
            )
            for module, directory in (
                ("github.com/plutoframework/pluto-core", core),
                ("github.com/plutoframework/pluto-rbac", rbac),
                (
                    "github.com/plutoframework/pluto-rbac/identity",
                    rbac / "identity",
                ),
                ("github.com/plutoframework/pluto-auth", auth),
                ("github.com/plutoframework/pluto-jobs", jobs),
                (JOBS_DATA, jobs / "stores/postgres"),
                (
                    "github.com/plutoframework/pluto-core/stores/postgres",
                    core / "stores/postgres",
                ),
                (
                    "github.com/plutoframework/pluto-core/"
                    "examples/shipments/postgres",
                    core / "examples/shipments/postgres",
                ),
            ):
                command(
                    [
                        "go",
                        "mod",
                        "edit",
                        f"-require={module}@v0.0.0",
                        f"-replace={module}={directory}",
                    ],
                    cwd=consumer,
                    env=environment,
                )
            command(["go", "mod", "tidy"], cwd=consumer, env=environment)
            tool = (
                Path(__file__).resolve().parents[1] / "conformance-golangci.sh"
            )
            report["formatCommand"] = [
                "go",
                "run",
                "./scripts/commentspacing",
                "-formatter",
                str(tool),
                str(consumer),
            ]
            command(
                report["formatCommand"],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
            )
            report["lintCommand"] = [
                str(tool),
                "run",
                "--enable-only",
                "govet,ineffassign,staticcheck,unused,wsl_v5",
                "./...",
            ]
            command(report["lintCommand"], cwd=consumer, env=environment)
            report["testCommand"] = [
                "go",
                "test",
                "-mod=mod",
                "-tags",
                "plutocandidate",
                "-race",
                "-count=1",
                "-json",
                *PACKAGES,
            ]
            output = command(
                report["testCommand"],
                cwd=consumer,
                env=environment,
                timeout=300,
            )
            report["executed"] = validate_results(output)
            report["namedSkips"] = []
            report["passed"] = True
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        report["errors"].append(str(error))
    finally:
        if container:
            try:
                remove_owned(container, owner)
                report.setdefault("database", {})["removed"] = True
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as error:
                report["errors"].append(f"database cleanup failed: {error}")
                report["passed"] = False
    return report
