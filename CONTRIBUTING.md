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
- [Add a runnable optimistic list-removal example](https://github.com/plutoframework/pluto-frontend/issues/1)
- [Add an executable bounded-pagination example](https://github.com/plutoframework/pluto-core/issues/1)
- [Add a role-catalog JSON example for administration UIs](https://github.com/plutoframework/pluto-rbac/issues/1)
- [Write a task-first troubleshooting page for local setup](https://github.com/plutoframework/pluto-site/issues/1)
