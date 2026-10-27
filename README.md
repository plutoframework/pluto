# Pluto

An opinionated Go framework for building APIs quickly, with explicit project
structure, readable code, secure defaults and a small dependency surface.

The framework is in its design/prototype stage. This module reserves
`github.com/plutoframework/pluto`; it does not export a framework runtime yet.
Current HTTP and application primitives live in the example and CLI templates
while their ergonomics are reviewed.

Related projects:

- [CLI](https://github.com/plutoframework/cli): Cobra-based project and endpoint
  scaffolding, formatting, validation and optional development tooling.
- [Example API](https://github.com/plutoframework/pluto-example): a purchasing API
  demonstrating handlers, operations, tenant isolation, flags and audit events.
- [RBAC](https://github.com/plutoframework/pluto-rbac): standalone roles,
  permissions, inheritance and explicitly scoped assignment resolution.

The intended framework uses Chi/net/http, typed configuration, explicit
dependency injection, consistent JSON responses and an OpenAPI contract. The
initial database direction is PostgreSQL with pgx and sqlc. Runtime dependencies
remain small and reviewed; generated application code remains editable.

Requires Go 1.25.5+ to build the placeholder module:

```sh
go test ./...
```
