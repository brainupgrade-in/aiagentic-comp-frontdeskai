# FrontDesk AI — Participant Deployment Guide

## Prerequisites

- **Ollama Cloud API key** (primary LLM) — https://ollama.com
- **Groq API key** (fallback, recommended) — free tier at https://console.groq.com
- Either a GitHub account for a Codespace (**Option A**, recommended), or Docker + Python 3.13 locally
  (**Option B** — 3.13 matches the `Containerfile` base image)

---

## Option A: GitHub Codespace

**1. Launch** — repo on GitHub → **Code → Codespaces → Create codespace on main**. The devcontainer
installs Python 3.13, kubectl, helm, kind and Docker-in-Docker, creates the kind cluster `frontdeskai`,
seeds `.env`, and creates `/shared/.sqlite`. Wait for the "Devcontainer notes" banner.

The app's Python dependencies are deliberately **not** installed in the Codespace — they are baked into
the image by `Containerfile` and run inside kind. Deploy with `scripts/deploy.sh`, don't run
`python app/app.py`.

**2. Configure keys**

```bash
cp .env.example .env
# set OLLAMA_API_KEY (required) and GROQ_API_KEY (recommended)
```

**3. Deploy** — `bash scripts/deploy.sh` builds the image, loads it into kind, creates the secret, and
deploys.

**4. Access** — open **http://localhost:8000** (NodePort via `extraPortMappings`, no port-forward). Log
in with any email + `brainupgrade`; the first login stores that password against your user.

---

## Option B: Local Machine

```bash
git clone https://github.com/brainupgrade-in/aiagentic-comp-frontdeskai.git
cd aiagentic-comp-frontdeskai
python3 -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt
cp .env.example .env          # set OLLAMA_API_KEY + GROQ_API_KEY
python app/app.py             # http://localhost:8000
```

To run it on kind locally instead:

```bash
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.27.0/kind-linux-amd64 \
  && chmod +x ./kind && sudo mv ./kind /usr/local/bin/kind
bash scripts/create-kind-cluster.sh   # one-time: cluster + NodePort mappings + /shared/.sqlite
bash scripts/deploy.sh
```

`create-kind-cluster.sh` needs passwordless sudo to create and chown `/shared`:

```bash
echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/$USER
```

If you plan to install observability, raise the host inotify limits first or Promtail will
`CrashLoopBackOff` with `too many open files` (add to `/etc/sysctl.conf` to persist):

```bash
sudo sysctl fs.inotify.max_user_instances=512
sudo sysctl fs.inotify.max_user_watches=524288
```

---

## Optional Add-ons

**Observability stack** — `bash scripts/install-observability.sh` installs Prometheus, Grafana, Loki,
Promtail and Tempo with 3-way correlation. Grafana: **http://localhost:3000** (agenticai /
agentgrow.io). Prometheus and Tempo have no NodePort:

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9091:9090
```

**MCP Leave Service** — `bash scripts/deploy-mcp.sh` deploys PostgreSQL + the MCP Leave Server into the
`postgres` namespace and runs a cross-namespace smoke test. Needed for the *"how many leaves do I have
left?"* demo, which goes through a remote MCP server instead of local SQLite tools.

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

**Not responding, or 401 Unauthorized** — check the logs and confirm both keys are in the secret:

```bash
kubectl logs deployment/frontdeskai | grep -i "error\|unauthorized"
kubectl get secret frontdeskai-secret -o jsonpath='{.data.OLLAMA_API_KEY}' | base64 -d
bash scripts/update-secret.sh    # if missing
```

**kind cluster not found** — `kind get clusters`; if missing, rerun `bash scripts/create-kind-cluster.sh`
(it sets the NodePort mappings localhost access depends on) then `bash scripts/deploy.sh`.

**Codespace enters recovery mode (Docker-in-Docker fails)** — the devcontainer base image must be
`mcr.microsoft.com/devcontainers/python:3.13-bookworm`, not `bullseye`. Delete and recreate the Codespace
to pick up the fix.
