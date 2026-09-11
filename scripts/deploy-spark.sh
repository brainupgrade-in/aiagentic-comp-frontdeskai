#!/bin/bash
# Deploy FrontDesk AI into a participant namespace on the Spark cluster.
#
# Run it from your JupyterLab terminal — APP_NAMESPACE and APP_HOST are already
# in your environment, so this needs no arguments:
#
#   bash scripts/deploy-spark.sh
#
# Overrides:
#   IMAGE=brainupgrade/frontdeskai:<tag>   pin a specific build
#   AUTH_PASSWORD=...                      first-login password (default brainupgrade)
#
# This is the Spark path. scripts/deploy.sh is the kind/local path and is
# unchanged; the two use different manifests because the participant namespace
# forbids NodePorts and caps memory at 1Gi.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MANIFESTS="${REPO_DIR}/scripts/manifests/spark"

NAMESPACE="${APP_NAMESPACE:-$(kubectl config view --minify -o jsonpath='{..namespace}' 2>/dev/null || true)}"
if [ -z "${NAMESPACE}" ]; then
  echo "ERROR: could not determine the namespace."
  echo "       Set APP_NAMESPACE, e.g. APP_NAMESPACE=agenticaiu31 bash $0"
  exit 1
fi

if [ -z "${APP_HOST:-}" ]; then
  echo "ERROR: APP_HOST is not set — no hostname to serve the app on."
  echo "       It is normally injected into your sandbox. Set it by hand if not:"
  echo "       APP_HOST=<your -app hostname> bash $0"
  exit 1
fi

IMAGE="${IMAGE:-brainupgrade/frontdeskai:latest}"
LLM_SECRET="${LLM_SECRET:-${NAMESPACE}-llm}"
AUTH_PASSWORD="${AUTH_PASSWORD:-brainupgrade}"
# One Tempo serves the whole cohort, so the service name carries the namespace.
OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-frontdeskai-${NAMESPACE}}"

echo "==> Namespace: ${NAMESPACE}"
echo "==> Host:      https://${APP_HOST}"
echo "==> Image:     ${IMAGE}"
echo "==> Traces:    service ${OTEL_SERVICE_NAME} -> Tempo in ns monitoring"

if ! kubectl -n "${NAMESPACE}" get secret "${LLM_SECRET}" >/dev/null 2>&1; then
  echo "WARNING: Secret '${LLM_SECRET}' not found in ${NAMESPACE}."
  echo "         The app will start but has no LLM gateway credential."
fi

# ── Secret: SECRET_KEY must survive a redeploy ───────────────────────────────
# It is the Fernet key for encrypted per-skill config in the database. A new
# key on every deploy makes previously stored skill credentials unreadable.
EXISTING_KEY=$(kubectl -n "${NAMESPACE}" get secret frontdeskai-secret \
                 -o jsonpath='{.data.SECRET_KEY}' 2>/dev/null | base64 -d 2>/dev/null || true)
if [ -n "${EXISTING_KEY}" ]; then
  echo "==> Reusing the existing SECRET_KEY"
  SECRET_KEY="${EXISTING_KEY}"
else
  echo "==> Generating a SECRET_KEY"
  SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
fi

kubectl -n "${NAMESPACE}" create secret generic frontdeskai-secret \
  --from-literal=SECRET_KEY="${SECRET_KEY}" \
  --from-literal=AUTH_PASSWORD="${AUTH_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

# ── Secret: the participant's own Langfuse project ───────────────────────────
# Read from this repo's own .env -- the same file the app reads with load_dotenv()
# when you run it locally, so one file configures both paths. Copy .env.example to
# .env and fill in the three LANGFUSE_ values. It is gitignored, so it cannot be
# committed or pushed.
#
# Each participant traces into their OWN Langfuse project, so nobody reads anyone
# else's prompts.
#
# The file is READ, never sourced: it is hand-written and a stray line in it should
# not execute during a deploy.
LANGFUSE_ENV_FILE="${LANGFUSE_ENV_FILE:-${REPO_DIR}/.env}"

read_env() {                       # read_env <FILE> <KEY> -> value on stdout, or nothing
  [ -f "$1" ] || return 0
  sed -n "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*//p" "$1" \
    | tail -n1 | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

if [ -f "${LANGFUSE_ENV_FILE}" ]; then
  LF_PUBLIC=$(read_env "${LANGFUSE_ENV_FILE}" LANGFUSE_PUBLIC_KEY)
  LF_SECRET=$(read_env "${LANGFUSE_ENV_FILE}" LANGFUSE_SECRET_KEY)
  # The app reads LANGFUSE_HOST. Accept LANGFUSE_BASE_URL too, because that is what
  # the Langfuse SDK docs and several of our own .env files call it -- getting this
  # name wrong disables tracing in silence, with no error anywhere.
  LF_HOST=$(read_env "${LANGFUSE_ENV_FILE}" LANGFUSE_HOST)
  [ -n "${LF_HOST}" ] || LF_HOST=$(read_env "${LANGFUSE_ENV_FILE}" LANGFUSE_BASE_URL)
  LF_ENVTAG=$(read_env "${LANGFUSE_ENV_FILE}" LANGFUSE_TRACING_ENVIRONMENT)
  [ -n "${LF_ENVTAG}" ] || LF_ENVTAG="${NAMESPACE}"
else
  LF_PUBLIC=""; LF_SECRET=""; LF_HOST=""; LF_ENVTAG="${NAMESPACE}"
fi

if [ -n "${LF_PUBLIC}" ] && [ -n "${LF_SECRET}" ] && [ -n "${LF_HOST}" ]; then
  echo "==> Langfuse: your own project at ${LF_HOST} (environment ${LF_ENVTAG})"
  kubectl -n "${NAMESPACE}" create secret generic frontdeskai-langfuse \
    --from-literal=LANGFUSE_PUBLIC_KEY="${LF_PUBLIC}" \
    --from-literal=LANGFUSE_SECRET_KEY="${LF_SECRET}" \
    --from-literal=LANGFUSE_HOST="${LF_HOST}" \
    --from-literal=LANGFUSE_TRACING_ENVIRONMENT="${LF_ENVTAG}" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo "==> Langfuse: NOT configured -- the app will deploy and run without it."
  echo "    cp ${REPO_DIR}/.env.example ${REPO_DIR}/.env  and set:"
  echo "      LANGFUSE_PUBLIC_KEY=pk-lf-..."
  echo "      LANGFUSE_SECRET_KEY=sk-lf-..."
  echo "      LANGFUSE_HOST=https://<your-region>.cloud.langfuse.com"
  echo "    then re-run this script. Nothing else needs to change."
  # Leave any existing Secret alone: re-running without the file must not delete
  # a working configuration.
fi

# ── Manifests ────────────────────────────────────────────────────────────────
echo "==> Applying manifests"
sed -e "s|SERVICE_NAME_PLACEHOLDER|${OTEL_SERVICE_NAME}|" \
    "${MANIFESTS}/configmap.yaml" | kubectl -n "${NAMESPACE}" apply -f -
kubectl -n "${NAMESPACE}" apply -f "${MANIFESTS}/service.yaml"

sed -e "s|image: DOCKERHUB_USERNAME/frontdeskai:latest|image: ${IMAGE}|" \
    -e "s|name: LLM_SECRET_NAME|name: ${LLM_SECRET}|" \
    "${MANIFESTS}/deployment.yaml" | kubectl -n "${NAMESPACE}" apply -f -

sed -e "s|host: APP_HOST|host: ${APP_HOST}|" \
    "${MANIFESTS}/ingress.yaml" | kubectl -n "${NAMESPACE}" apply -f -

# Picks up a changed ConfigMap, which a pod does not reload on its own.
kubectl -n "${NAMESPACE}" rollout restart deployment/frontdeskai
echo "==> Waiting for the app to be ready (first pull is slow)..."
kubectl -n "${NAMESPACE}" rollout status deployment/frontdeskai --timeout=300s

echo ""
echo "==> FrontDesk AI deployed."
echo "    URL:    https://${APP_HOST}"
echo "    Login:  rajesh.kumar@unigps.in / ${AUTH_PASSWORD}"
echo "    Logs:   kubectl -n ${NAMESPACE} logs -f deploy/frontdeskai"
echo "    Traces: Grafana -> Explore -> Tempo -> service.name = ${OTEL_SERVICE_NAME}"
