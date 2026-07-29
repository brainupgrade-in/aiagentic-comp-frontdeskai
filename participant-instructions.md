# FrontDesk AI — Participant Deployment Guide

## Prerequisites

- **An LLM API key** — either is enough, both is better:
  - **Ollama Cloud** (primary) — https://ollama.com
  - **Groq** (automatic fallback) — free tier at https://console.groq.com
- Either a GitHub account for a Codespace (**Option A**, recommended), or Docker + Python 3.13 locally
  (**Option B** — 3.13 matches the `Containerfile` base image)

---

## Option A: GitHub Codespace

**1. (Optional but recommended) Save your key as a Codespaces secret** — GitHub → **Settings →
Codespaces → Secrets → New secret**, named `OLLAMA_API_KEY` (and/or `GROQ_API_KEY`), scoped to this repo.
Do this first and the rest is automatic.

**2. Launch** — repo on GitHub → **Code → Codespaces → Create codespace on main**. The devcontainer
installs Python 3.13, kubectl, helm, kind and Docker-in-Docker, creates the kind cluster `frontdeskai`,
seeds `.env`, and copies any Codespaces secrets into it. If a key is present it then runs
`scripts/quickstart.sh` for you: image build, deploy, MCP Leave Service, health check, and a demo-data
check. Watch for the `FrontDesk AI is ready` banner.

**3. If you skipped the secret** — put a key in `.env` and run the same thing by hand:

```bash
bash scripts/quickstart.sh
```

**4. Access** — open **http://localhost:8000** (NodePort via `extraPortMappings`, no port-forward). Log
in as `rajesh.kumar@unigps.in` with password `brainupgrade`; the first login stores that password against
your user. Demo data — 10 employees, leave balances, tickets, expense claims, meeting rooms, payslips —
is already loaded, so every scenario has something real to work with.

**5. Take the tour** — [use-case-scenarios.md](use-case-scenarios.md), starting at Part 1.

The app's Python dependencies are deliberately **not** installed in the Codespace — they are baked into
the image by `Containerfile` and run inside kind. Deploy with the scripts, don't run `python app/app.py`.

---

## Option B: Local Machine

Same one command, once the cluster prerequisites are in place:

```bash
git clone https://github.com/brainupgrade-in/aiagentic-comp-frontdeskai.git
cd aiagentic-comp-frontdeskai
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.27.0/kind-linux-amd64 \
  && chmod +x ./kind && sudo mv ./kind /usr/local/bin/kind
cp .env.example .env          # set OLLAMA_API_KEY and/or GROQ_API_KEY
bash scripts/quickstart.sh    # creates the cluster if needed, then deploys everything
```

`quickstart.sh` calls `scripts/create-kind-cluster.sh`, which needs passwordless sudo to create and chown
`/shared`:

```bash
echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/$USER
```

If you plan to install observability, raise the host inotify limits first or Promtail will
`CrashLoopBackOff` with `too many open files` (add to `/etc/sysctl.conf` to persist):

```bash
sudo sysctl fs.inotify.max_user_instances=512
sudo sysctl fs.inotify.max_user_watches=524288
```

### Without Kubernetes

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt
cp .env.example .env          # SEED_DEMO_DATA=true is already set there
python app/app.py             # http://localhost:8000
```

---

## What quickstart.sh does

Re-running it is safe — every step is idempotent.

1. Creates `.env` from `.env.example` if missing, and pulls `OLLAMA_API_KEY` / `GROQ_API_KEY` /
   `LANGFUSE_*` out of the environment into it
2. Fails early with instructions if no LLM key is set
3. Creates the kind cluster `frontdeskai` if it does not exist
4. `scripts/deploy.sh` — builds the image, loads it into kind, applies the secret and manifests
5. `scripts/deploy-mcp.sh` — PostgreSQL + MCP Leave Server, with a smoke test (non-fatal if it fails)
6. Waits for `/health`, then verifies the seeded row counts and prints the demo logins

Skip the MCP stack with `SKIP_MCP=true bash scripts/quickstart.sh`.

---

## Optional Add-ons

**Observability stack** — `bash scripts/install-observability.sh` installs Prometheus, Grafana, Loki,
Promtail and Tempo with 3-way correlation. Grafana: **http://localhost:3000** (agenticai /
agentgrow.io). Prometheus and Tempo have no NodePort:

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9091:9090
```

**Langfuse tracing** — put all three `LANGFUSE_*` values in `.env` and redeploy (`bash scripts/deploy.sh`
or `bash scripts/update-secret.sh`). Editing `.env` alone changes nothing: the keys reach the pod through
the `frontdeskai-secret` K8s secret, which those scripts rebuild. Mind the region — `us.cloud.langfuse.com`
and `cloud.langfuse.com` are separate installations with separate keys and separate UIs. Verify:

```bash
kubectl logs deployment/frontdeskai | grep -i langfuse
# "Langfuse enabled" at startup, then "Langfuse handler ready" with "auth_check": true
```

---

## Day-to-Day

```bash
kubectl get pods -l app=frontdeskai      # verify
kubectl logs deployment/frontdeskai
bash scripts/deploy.sh                   # redeploy after code changes
bash scripts/update-secret.sh            # update API keys from .env, no image rebuild
```

## Troubleshooting

**`ImagePullBackOff` / `ErrImageNeverPull`** — rerun `bash scripts/deploy.sh` to rebuild and reload the
image into kind.

**No demo data (empty leave balances, "employee not found")** — the seed runs at pod start when
`SEED_DEMO_DATA=true`, which `deployment.yaml` sets. If your PVC predates that flag, restart to fill it
in — the seed is idempotent and will not overwrite your own changes:

```bash
kubectl rollout restart deployment/frontdeskai
kubectl exec deployment/frontdeskai -- python -c \
  "import sqlite3,os; c=sqlite3.connect(os.environ.get('SQLITE_DIR','/shared/.sqlite')+'/frontdesk_tools.db'); \
   print(c.execute('select count(*) from employees').fetchone())"
```

**Not responding, or 401 Unauthorized** — check the logs and confirm the keys are in the secret:

```bash
kubectl logs deployment/frontdeskai | grep -i "error\|unauthorized"
kubectl get secret frontdeskai-secret -o jsonpath='{.data.OLLAMA_API_KEY}' | base64 -d
bash scripts/update-secret.sh    # if missing
```

**Every answer is slow, or the audit trail shows retries** — you are probably being rate-limited on one
provider and failing over to the other. Set both keys, or switch models from admin chat:
`switch to gemma4:cloud on ollama`.

**kind cluster not found** — `kind get clusters`; if missing, rerun `bash scripts/quickstart.sh` (it
recreates the cluster with the NodePort mappings localhost access depends on).

**Codespace enters recovery mode (Docker-in-Docker fails)** — the devcontainer base image must be
`mcr.microsoft.com/devcontainers/python:3.13-bookworm`, not `bullseye`. Delete and recreate the Codespace
to pick up the fix.
