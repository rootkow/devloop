# devloop

An open-source framework that packages the Dev Loop engine so any team can run autonomous, agent-driven code improvement workflows on their own Kubernetes cluster. Ships four container images, the `omneval-devloop` Python SDK, and a Helm chart. Temporal is a documented prerequisite that consumers bring independently.

## Language

**Dev Loop**:
The multi-phase autonomous workflow for maintaining and improving an enrolled codebase. Phases run in order: Plan → Phase Gate → Execute → Review → Phase Gate → Merge → Summarization. Triggered by the `agent-ready` label being applied to a GitHub issue. One issue is processed per round; the loop repeats until no unblocked issues remain.
_Avoid_: agent pipeline, CI loop, autonomous CI

**Phase Gate**:
A Discord-mediated pause in the Dev Loop where the agent posts a structured summary and waits for explicit human approval before advancing to the next phase. Required at Plan→Execute and Review→Merge. Mid-execution blocking questions also constitute a Phase Gate but carry a 4-hour timeout, after which the agent documents its best-guess assumption and continues.
_Avoid_: approval step, human-in-the-loop checkpoint, Discord prompt

**Planner**:
The first phase of the Dev Loop. An OpenHands agent that reads all open `agent-ready`-labeled issues for a project, builds a dependency-ordered execution plan, and posts it to Discord as a Phase Gate for approval before any code is written.
_Avoid_: planning agent, issue sorter

**Summarization Agent**:
A Temporal workflow that runs after each Merge phase. Reads the git diff and closed issues since the last run, generates a plain-English explanation of what changed and why, and posts to a configured Discord channel.
_Avoid_: changelog agent, diff summarizer

**Project Registry**:
A YAML config file (owned by the consumer, typically `agents/projects.yaml` in their GitOps repo) enumerating all repos enrolled for Dev Loop management. Each entry declares: GitHub repo URL, agent image reference, default branch, `agent-ready` label name, Discord channel mapping, omneval ingest secret name, and GitHub token secret name. Adding a project is a change to the consumer's repo — no dynamic registration.
_Avoid_: agent config, project database

**Agent Base Image**:
The container image (`ghcr.io/omneval/devloop-agent-base`) used as the `FROM` base for all per-project agent images. Contains the shared toolchain: OpenHands SDK, `omneval-devloop` (for the shared Agent Job output ConfigMap protocol and its pinned Temporal + kubernetes clients), git, gh CLI, kubectl, flux CLI, argocd CLI. Per-project images extend it with only the language runtime and prompts they need.
_Avoid_: base container, shared agent image

**Agent Job output ConfigMap**:
The Kubernetes ConfigMap an Agent Execution Job writes its result to and reads a human's mid-run reply from — the message-bus seam between the Job and the Temporal Orchestration Worker. The agent writes the JSON-encoded result under the `result` key (`AgentJobResult.to_payload`); the worker polls and rebuilds it (`AgentJobResult.from_payload`). A blocking question parks the Job and the worker patches the answer back under the `human_answer` key. The contract (field set and key names) is owned once in `devloop.shared` so both `devloop-temporal-worker` and `devloop-agent-base` reference one definition.
_Avoid_: result ConfigMap, status ConfigMap, output map

**Agent Execution Job**:
A Kubernetes `batch/v1 Job` spawned by the Temporal Orchestration Worker for each Execute or Review phase. Each Job runs a single-use Temporal Activity Worker, processes one agent task via OpenHands SDK with `LocalWorkspace`, then exits. The pod is the isolation boundary — no Docker-in-Docker. The Job image is per-project, pulled from the Project Registry entry.
_Avoid_: agent pod, worker job, sandbox job

**Temporal Orchestration Worker**:
The long-running Kubernetes Deployment that hosts Temporal Activity Workers for lightweight activities: planning, Discord messaging, GitHub API calls, and Agent Execution Job spawning. The `devloop-temporal-worker` reference image runs this using only `omneval-devloop`. Consumers who need additional workflows (e.g. a homelab Alert Response Workflow) build their own image that installs `omneval-devloop` and registers their custom workflows alongside.
_Avoid_: Temporal worker pod, orchestration service

**Discord Bot**:
The Kubernetes Deployment (`ghcr.io/omneval/devloop-discord-bot`) that bridges Discord and the Temporal server. Creates threads, posts Phase Gate summaries, and forwards user replies back as Temporal signals. Consumers configure it with their own Discord bot token and channel IDs.
_Avoid_: Discord integration, bot service, notification service

**omneval-devloop**:
The Python package (`pip install omneval-devloop`, PyPI name `omneval-devloop`, import as `import devloop`) that contains the reusable Dev Loop workflow logic: `DevLoopWorkflow`, `SummarizationWorkflow`, `k8s_jobs`, `projects`, `github_ops`, `shared` dataclasses, and activity implementations. Consumers import it to register the Dev Loop workflows alongside their own custom Temporal workflows without forking the devloop repo.
_Avoid_: devloop-sdk, devloop library, agent SDK

**devloop Consumer**:
Any deployment that installs `omneval-devloop` and runs it against one or more enrolled codebases. A consumer owns its Project Registry, per-project agent images, and deployment configuration. Consumers who need custom Temporal workflows (beyond the Dev Loop) build their own Temporal Orchestration Worker image that installs `omneval-devloop` alongside their custom workflow code.
_Avoid_: devloop user, devloop instance

**devloop images**:
The four container images published to `ghcr.io/omneval/` by this repo: `devloop-agent-base` (shared toolchain base), `devloop-temporal-worker` (reference Temporal Orchestration Worker), `devloop-discord-bot` (Discord ↔ Temporal bridge), `devloop-poller` (GitHub issue poller). Image tags follow `sha-<7-char-hash>-<unix-epoch>` for main builds and semver for releases.
_Avoid_: devloop containers, agent images (too generic)

---

## Conventions

**Python tooling**: Always use [uv](https://github.com/astral-sh/uv) for Python dependency management. Initialise packages with `uv init`. Define dependencies in `pyproject.toml`; do not use `requirements.txt`. Commit `uv.lock`. In Dockerfiles, copy uv from the official image and install with `uv pip install --system --no-cache .`:

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /uvx /bin/
COPY pyproject.toml uv.lock .
RUN UV_HTTP_TIMEOUT=300 uv pip install --system --no-cache .
```

Publish packages to PyPI with `uv build` + `uv publish` (OIDC trusted publisher — no stored API token).

**Image tag format**: `sha-<7-char-hash>-<unix-epoch>` for builds from main; semver (`v1.2.3`) for releases. The epoch component allows FluxCD ImagePolicy to select the newest build by alphabetical ordering without requiring semver on every commit.

---

## Architecture decisions

- **ADR-0003** (from `home-server`): Temporal is the durable orchestration layer. OpenHands SDK and Agent Execution Jobs are called as activities from within Temporal workflows.
- **ADR-0004** (from `home-server`): Agent Execution Jobs use Kubernetes Jobs + OpenHands `LocalWorkspace` — no Docker-in-Docker. The pod is the isolation boundary.
- **ADR-0005** (from `home-server`): OpenHands SDK replaced Pi/Sandcastle for stuck detection, built-in OTLP tracing, and native pause/resume.
- **ADR-0006** (from `home-server`): Dev Loop core is extracted as the `omneval-devloop` Python package rather than a plugin/extension mechanism, giving consumers a stable, testable API surface with version mismatches caught at install time.
