<p align="center">
  <img src="https://raw.githubusercontent.com/plutoframework/pluto/main/assets/readme/banner.svg" alt="Pluto — a small framework, wide open possibilities" width="100%" />
</p>

# Pluto

A small framework. Wide open possibilities.

Build explicit Go APIs quickly, add the frontend you prefer, and keep the
structure clear for people and agents. Opinionated scaffolding, focused runtime
libraries and secure defaults give your application a consistent starting point.

[Website](https://plutoframework.github.io/pluto-site/) · [Documentation](https://plutoframework.github.io/pluto-site/docs/index/) · [CLI demo](https://github.com/plutoframework/pluto/blob/main/docs/DEMO.md) · [First issues](https://github.com/plutoframework/pluto/blob/main/CONTRIBUTING.md#starter-tasks)

## From an empty folder to an API

### 1. Install the CLI

Go **1.25.5+** and Git are required:

```sh
go install github.com/plutoframework/cli/cmd/pluto@v0.14.0
```

### 2. Create and run

```sh
pluto --no-input init orbit --module example.com/orbit --env-prefix ORBIT_
cd orbit
go run ./cmd/api
```

### 3. Call a working endpoint

```sh
curl -fsS http://127.0.0.1:8080/healthz
```

The response is `{"data":"ok"}`. The generated API is running. Business routes
remain intentionally unimplemented until you add operations, authorization and tests.

[![Real Pluto CLI walkthrough: scaffold an API, start it and call its health endpoint](https://raw.githubusercontent.com/plutoframework/pluto/main/assets/readme/demo.gif)](docs/DEMO.md)

[Text transcript](docs/DEMO.md) · [Static image](assets/readme/demo.png) · [Step-by-step guide](https://plutoframework.github.io/pluto-site/docs/getting-started/) · [Worked examples](https://plutoframework.github.io/pluto-site/docs/examples/)

## The ecosystem

| Project                                                      | Responsibility                                             |
| ------------------------------------------------------------ | ---------------------------------------------------------- |
| [CLI](https://github.com/plutoframework/cli)                 | Scaffolds, terminal wizard, formatting, checks and OpenAPI |
| [Core](https://github.com/plutoframework/pluto-core)         | Pagination, idempotency, cache, flags and business state   |
| [Auth](https://github.com/plutoframework/pluto-auth)         | Sessions and optional local credential lifecycle           |
| [RBAC](https://github.com/plutoframework/pluto-rbac)         | Named roles, capabilities and scoped assignments           |
| [Jobs](https://github.com/plutoframework/pluto-jobs)         | Durable workers and transactional PostgreSQL outbox        |
| [Agent](https://github.com/plutoframework/pluto-agent)       | Typed AI runs and explicitly authorized tools              |
| [Frontend](https://github.com/plutoframework/pluto-frontend) | Typed UI state and responsive actions                      |
| [Example](https://github.com/plutoframework/pluto-example)   | A full-stack purchasing application                        |
| [Skills](https://github.com/plutoframework/pluto-skills)     | Short, installable guidance for coding agents              |
| [Site](https://github.com/plutoframework/pluto-site)         | Product site, Starlight docs and Markdown exports          |

This repository is the ecosystem entrypoint. The Go module
`github.com/plutoframework/pluto` is reserved; it does not export a framework
runtime. Start with the CLI and the focused packages above.

## Our conventions

- Explicit routes, thin handlers and testable application operations.
- Standard Go first; reviewed dependencies stay at deliberate integration boundaries.
- PostgreSQL, pgx and sqlc; explicit migrations and transaction ownership.
- Default-off staged capabilities, server-owned authority and scoped data access.
- Readable 80-column formatting, bounded source files and canonical checks.
- Immediate UI feedback, explicit optimism and honest handling of uncertain writes.

## Build with an agent

```sh
npx skills add plutoframework/pluto-skills
```

Choose only the guidance needed. Agents can fetch individual topics from the
[Markdown documentation index](https://plutoframework.github.io/pluto-site/docs/llms.txt).

## Contribute

Read [CONTRIBUTING.md](CONTRIBUTING.md), find a [good first issue](https://github.com/plutoframework/pluto/blob/main/CONTRIBUTING.md#starter-tasks),
or follow the linked package repository for implementation work. Pluto is an
evolving framework preview; operational requirements and limits are documented
per package. No independent security audit is claimed.
