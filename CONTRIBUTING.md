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
