# Contributing

## Choose a first issue

Browse the starter tasks below.
Each task should have a narrow scope, an observable outcome and clear checks.
Search existing issues before opening another. For a proposal, explain the user
problem and smallest useful change before writing a broad implementation.

## Work in the right layer

- Read this repository's `AGENTS.md` when present and the linked reference docs.
- Keep Go standard-library boundaries and optional adapters explicit.
- Use the CLI for application scaffolds; maintainers fix generator source, rebuild
  and test disposable consumers instead of patching generated output.
- Keep handlers thin, authority server-owned and authored files protected.
- Use configured 80-column expanded formatting. Put a blank line above standalone
  comments except at file line one; preserve required compiler/tool directives.
- Keep staged capabilities off by default and test both flag states.

## Validate the change

Run from this repository's root:

```sh
go test ./...
python3 scripts/check-docs.py
```

Use the repository's additional race, bounded fuzz/mutation and integration checks
for the behavior changed. Real-service checks require isolated services; a skip is
not a pass. For docs-only changes, check links and execute changed examples. Never
suppress checks to hide a failure; report missing prerequisites and untested edges.

## Frozen architecture baseline

The framework-owned negative conformance runner invokes a supplied candidate CLI
directly against the pinned, unchanged `pluto-example` baseline. It never invokes
example-owned checks or updates its source, manifest or pins. The trusted ledger
is `scripts/architecture-baseline.json`.

```sh
PYTHONPATH=scripts python3 -m unittest architecture_baseline.test_runner -v
python3 scripts/check-architecture-baseline.py --cli /absolute/path/to/pluto \
  --baseline ../pluto-example --report /tmp/architecture-evidence.json
```

A successful runner exit proves only the ledger's expected failures were found;
`targetCompliancePassed` remains false. Evidence records baseline and executable
hashes, diagnostics coverage gaps and errors. Missing/crashed checkers, malformed
reports, unexpected exits and changed baseline files fail. Untracked files fail
except `.DS_Store` and ignored non-source artifacts; ignored source, configuration
and scripts still fail outside generated dependency/build directories
(`node_modules`, `dist`, `.next`, `__pycache__`, `test-results`,
`playwright-report`, `coverage`). These ignored outputs are not assessed.
`--require-complete` also fails when the checker reports any unassessed boundary.
This staged proof does not close G1–G10 or prove
trusted CI execution. The existing automation-test workflow runs the runner
selftests without a candidate CLI dependency. The candidate command is unreleased;
supply a locally built CLI rather than assuming the published release supports it.

The candidate operation conformance runner composes local Core and RBAC checkouts
in a temporary module and provisions its own disposable PostgreSQL container:

```sh
PYTHONPATH=scripts python3 -m unittest operation_conformance.test_runner -v
python3 scripts/check-operation-conformance.py \
  --core ../pluto-core --rbac ../pluto-rbac \
  --report /tmp/operation-conformance.json
```

It requires the named permission, concurrent-write and rollback tests to execute
under the race detector; a skip or missing test fails. Evidence records candidate
revisions, dirty state, fixture hash and database image identity. The runner
cleans up its owned container on success and failure. This proves the composed
reference path, not published dependency compatibility or automatic adoption by
generated applications. CI runs the runner's dependency-free selftests.

## Library maintainer tooling

The Core, Auth, RBAC, Jobs, Agent and Frontend repositories expose the same
Make targets. Run them inside the library checkout:

```sh
make fmt    # apply the configured formatting
make vet    # check formatting, lint and build without fixes
make check  # standard checks and tests
make test   # tests only
```

These delegate to each library's existing scripts, including checks for optional
Go modules. They do not require an application manifest. `pluto fmt` and
`pluto vet` remain application commands. See each library's `CONTRIBUTING.md`
for prerequisites and additional fuzz, mutation and service integration checks.

The shared Go comment-spacing helper lives in `scripts/commentspacing`; keep its
copies in library and CLI repositories synchronized. Run
`go test ./scripts/commentspacing` after changing it.

## Pull request review

Changes to `main` use pull requests. Routine fixes can merge automatically after
CI, security checks and a complete Astra review pass. Sensitive code and tooling
changes require an independent maintainer approval. See the
[product guide](https://plutoframework.github.io/pluto-site/docs/maintainers/pull-requests.md)
for the policy, retries and credential boundaries.

Push review fixes to the existing PR branch so the next review evaluates the
updated change and revisits previous findings.

## Send a focused contribution

Explain the problem, resulting behavior, validation and any compatibility or
migration needs. Include a regression for a reproduced bug where practical. UI
changes need exercised loading, empty, error, success and accessibility states;
async actions must acknowledge immediately and handle uncertain outcomes honestly.

Do not commit secrets, production data, local credentials or unrelated changes.
Follow any repository security policy and use GitHub's private vulnerability
reporting when enabled. Do not post undisclosed exploit details in a public issue;
if no private route is offered, ask maintainers how to report privately without
including sensitive details. Publishing a patch is not an independent audit.

## Starter tasks

- [Pass AbortSignal to generated form submit callbacks](https://github.com/plutoframework/cli/issues/1)
- [Add a role-catalog JSON example for administration UIs](https://github.com/plutoframework/pluto-rbac/issues/1)
- [Write a task-first troubleshooting page for local setup](https://github.com/plutoframework/pluto-site/issues/1)

## Keep examples executable

The ecosystem README workflow clones public sources, checks source-backed snippet
markers and runs complete examples against published package pins. For local
checks, pass sibling repositories with `scripts/check-docs.py --repo-dir ../NAME`.
Use `mise exec node@24.22.1 -- python3 scripts/check-examples.py` to execute the
copyable Go and JavaScript programs. Database, generated-route and agent examples
have their own documented walkthroughs; do not pretend a source-link check runs them.

## Completed starter examples

- [Scoped pagination](https://github.com/plutoframework/pluto-core/tree/main/examples/pagination) completed the pagination starter task.
- [Optimistic list actions](https://github.com/plutoframework/pluto-frontend/tree/main/examples/actions) completed the frontend starter task.
