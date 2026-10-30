# Ecosystem roadmap

[Live roadmap and issue index](https://github.com/plutoframework/pluto/issues/1)

GitHub issues are the source of truth for status, acceptance criteria and
dependencies. Work belongs in the owning repository; this index connects the
library, generator, frontend, example and documentation deliverables.

## Order of work

1. **Shipped:** Auth v0.4.0 shared account and ceremony foundation.
2. **Next:** native social sign-in and second-factor workflows; missing CI
   in the CLI, RBAC and reference application.
3. **Then:** account security management, operational recovery and deliberate
   generator upgrades.
4. **Later:** passwordless/machine access and accepted discovery proposals.

These are sequencing groups, not promised dates or future release numbers.
A workflow includes its library, explicit migrations, HTTP boundary, CLI,
configured frontend, runnable example and documentation. Consumer issues ship
incrementally with each supported method.

## Repository milestones

### pluto

- [Ecosystem MVP completion](https://github.com/plutoframework/pluto/milestone/1)
### pluto-auth

- [Shared account and ceremony foundation](https://github.com/plutoframework/pluto-auth/milestone/1) — shipped.
- [Social and federated sign-in](https://github.com/plutoframework/pluto-auth/milestone/2)
- [Native second-factor workflow](https://github.com/plutoframework/pluto-auth/milestone/3)
- [Account security management](https://github.com/plutoframework/pluto-auth/milestone/4)
- [Passwordless and machine access](https://github.com/plutoframework/pluto-auth/milestone/5)
### cli

- [Generator and upgrade reliability](https://github.com/plutoframework/cli/milestone/1)
### pluto-example

- [Reference application completeness](https://github.com/plutoframework/pluto-example/milestone/1)
### pluto-rbac

- [Authorization integration and validation](https://github.com/plutoframework/pluto-rbac/milestone/1)
### pluto-jobs

- [Outbox operations](https://github.com/plutoframework/pluto-jobs/milestone/1)
### pluto-core

- [Operational primitives](https://github.com/plutoframework/pluto-core/milestone/1)
### pluto-agent

- [Provider and run reliability](https://github.com/plutoframework/pluto-agent/milestone/1)
### pluto-frontend

- [Responsive UI and account workflows](https://github.com/plutoframework/pluto-frontend/milestone/1)
### pluto-skills

- [Executable ecosystem guidance](https://github.com/plutoframework/pluto-skills/milestone/1)
### pluto-site

- [Runnable and current documentation](https://github.com/plutoframework/pluto-site/milestone/1)

## Scope and completion

- Read issue acceptance criteria and dependencies before starting work.
- Discovery issues end in an ADR and explicit go/no-go decision. They are not
  unconditional MVP commitments.
- Keep intentional contracts: jobs deliver at least once, unsigned cursors do
  not grant authority, and applications enforce resource policies.
- Close issues with linked changes, validation, migration/release notes and
  consumer evidence. Update milestones and the live roadmap as work lands.
- Keep security reports private until triaged. The public backlog describes
  product gaps, not undisclosed vulnerability details.

OAuth/OIDC authorization-server hosting, SAML/SCIM, generic agent orchestration,
RAG and offline queues remain separate product decisions.
