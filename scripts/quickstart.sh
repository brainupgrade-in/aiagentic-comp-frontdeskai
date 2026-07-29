#!/bin/bash
# One command from a fresh clone to a working demo.
#
#   bash scripts/quickstart.sh
#
# Creates the kind cluster if it is missing, deploys the app (with demo data
# pre-seeded), deploys the MCP Leave Service, waits until /health answers, and
# verifies the seed landed. Safe to re-run — every step is idempotent.
#
# Skip the MCP stack with:  SKIP_MCP=true bash scripts/quickstart.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${REPO_DIR}/.env"
CLUSTER_NAME="frontdeskai"
APP_URL="http://localhost:8000"

step() { echo ""; echo "── $* ─────────────────────────────────────────────"; }

# ── 1. .env with at least one LLM key ────────────────────────────────────────
step "Checking .env"
if [ ! -f "${ENV_FILE}" ]; then
  cp "${REPO_DIR}/.env.example" "${ENV_FILE}"
  echo "Created .env from .env.example."
fi

# Read the two keys without sourcing the whole file
key_of() { grep -E "^$1=" "${ENV_FILE}" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'\''' | tr -d '\r'; }
OLLAMA_KEY="$(key_of OLLAMA_API_KEY)"
GROQ_KEY="$(key_of GROQ_API_KEY)"

# Codespaces/CI secrets win over an empty .env entry
if [ -z "${OLLAMA_KEY}" ] && [ -n "${OLLAMA_API_KEY:-}" ]; then
  sed -i "s|^OLLAMA_API_KEY=.*|OLLAMA_API_KEY=${OLLAMA_API_KEY}|" "${ENV_FILE}"
  OLLAMA_KEY="${OLLAMA_API_KEY}"
  echo "Took OLLAMA_API_KEY from the environment."
fi
if [ -z "${GROQ_KEY}" ] && [ -n "${GROQ_API_KEY:-}" ]; then
  sed -i "s|^GROQ_API_KEY=.*|GROQ_API_KEY=${GROQ_API_KEY}|" "${ENV_FILE}"
  GROQ_KEY="${GROQ_API_KEY}"
  echo "Took GROQ_API_KEY from the environment."
fi

if [ -z "${OLLAMA_KEY}" ] && [ -z "${GROQ_KEY}" ]; then
  cat <<'MSG'

No LLM API key found in .env — the app cannot answer anything without one.

  1. Get a key:
       OLLAMA_API_KEY  (primary)   https://ollama.com
       GROQ_API_KEY    (fallback)  https://console.groq.com
  2. Put it in .env
  3. Rerun: bash scripts/quickstart.sh

MSG
  exit 1
fi
echo "LLM keys present: ollama=$([ -n "${OLLAMA_KEY}" ] && echo yes || echo no), groq=$([ -n "${GROQ_KEY}" ] && echo yes || echo no)"

# ── 2. Cluster ───────────────────────────────────────────────────────────────
step "Checking kind cluster '${CLUSTER_NAME}'"
if command -v kind >/dev/null 2>&1 && kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo "Cluster already exists."
  kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true
else
  echo "Creating it..."
  bash "${REPO_DIR}/scripts/create-kind-cluster.sh" "${CLUSTER_NAME}"
fi

# ── 3. App ───────────────────────────────────────────────────────────────────
step "Deploying FrontDesk AI (this builds the image — first run takes a few minutes)"
bash "${REPO_DIR}/scripts/deploy.sh"

# ── 4. MCP Leave Service ─────────────────────────────────────────────────────
if [ "${SKIP_MCP:-false}" = "true" ]; then
  step "Skipping MCP Leave Service (SKIP_MCP=true)"
else
  step "Deploying MCP Leave Service"
  if bash "${REPO_DIR}/scripts/deploy-mcp.sh"; then
    echo "MCP Leave Service ready."
  else
    echo "MCP deploy failed — the app still works; leave queries fall back to SQLite."
    echo "Retry later with: bash scripts/deploy-mcp.sh"
  fi
fi

# ── 5. Wait for health ───────────────────────────────────────────────────────
step "Waiting for the app to answer on ${APP_URL}"
ready=false
for _ in $(seq 1 60); do
  if curl -fsS -m 3 "${APP_URL}/health" >/dev/null 2>&1; then ready=true; break; fi
  sleep 5
done
if [ "${ready}" != "true" ]; then
  echo "The app did not become ready in 5 minutes. Check:"
  echo "  kubectl get pods -l app=frontdeskai"
  echo "  kubectl logs deployment/frontdeskai"
  exit 1
fi
echo "Health check OK."

# ── 6. Verify the demo data is loaded ────────────────────────────────────────
step "Verifying seeded demo data"
kubectl exec deployment/frontdeskai -- python -c "
import os, sqlite3
db = os.path.join(os.environ.get('SQLITE_DIR', '/shared/.sqlite'), 'frontdesk_tools.db')
c = sqlite3.connect(db)
counts = {t: c.execute('select count(*) from ' + t).fetchone()[0]
          for t in ('employees','leave_balances','tickets','expense_claims','meeting_rooms','payslips')}
print('  ' + ', '.join(f'{k}={v}' for k, v in counts.items()))
if counts['employees'] == 0:
    raise SystemExit('  Seed data missing — check SEED_DEMO_DATA in deployment.yaml')
" || {
  echo "Could not verify the seed. Inspect: kubectl logs deployment/frontdeskai"
  exit 1
}

cat <<MSG

===========================================================
 FrontDesk AI is ready
===========================================================
 Open:      ${APP_URL}
 Password:  brainupgrade   (first login stores it for that user)

 Log in as:
   rajesh.kumar@unigps.in   employee — best all-round demo user
   amit.patel@unigps.in     Finance Lead — can approve claims
   admin@unigps.in          admin — required for the skill scenarios
   alice@unigps.in          MCP-only identity (leave data in PostgreSQL)

 Then follow use-case-scenarios.md — start at Part 1.

 Optional: bash scripts/install-observability.sh   (Grafana on :3000)
===========================================================
MSG
