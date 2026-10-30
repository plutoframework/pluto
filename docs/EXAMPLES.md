# Example authoring audit

Reviewed the ecosystem READMEs, package references, site topics and CLI agent
instructions against the [authoring standard](https://plutoframework.github.io/pluto-site/docs/maintainers/documentation.md).

| Area | Primary usage | Testing boundary |
| --- | --- | --- |
| Agent | Real OpenAI summary command with typed output | Scripted purchase review and controlled HTTP tests |
| CLI | Actual scaffolding and operation code; explicit OpenAI agent selection | Protected-route fixture harness is identified as testing |
| Auth | PostgreSQL credential service constructor and full application integration | In-process session issuance remains a labeled fixture |
| Jobs | Transactional enqueue and actual PostgreSQL audit archival | Synthetic handler demonstrates settlement, not external delivery |
| Frontend | Actual generated API calls, scoped cache confirmation and UI feedback | Deferred-promise rejection/uncertainty scenario remains testing |
| Core | Actual pagination/state/cache primitives with local inputs | Local rows do not pretend to exercise a database |
| RBAC | Actual policy evaluation with trusted assignments and resource guards | Sample subjects are inputs, not an authentication mechanism |
| Example app | Real credentials, database and optional model provider | Demo inference is documented under testing |
| Site | Application integrations precede deterministic test walkthroughs | Reference snapshots retain source versions |
| Skills | Concise authoring skill and maintainer routing | No duplicate manual or inferred live-call permission |
| Ecosystem overview | Real CLI-to-health-endpoint recording | Health response is not a completed business endpoint |

Deterministic tests and development fixtures are intentional. Do not remove them
or make CI depend on paid services. New walkthroughs should show how to connect
the actual capability before using test machinery to explain its behavior.

`check-docs.py` checks source-backed snippets and links. `check-examples.py`
executes the short pure/HTTP examples against published packages and tests the
Agent's real adapter with a controlled transport. These checks do not prove live
inference quality, production delivery or deployment readiness.
