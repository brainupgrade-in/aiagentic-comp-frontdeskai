#!/bin/bash
# Install full observability stack on kind cluster:
#   Tempo      — distributed tracing (receives OTLP on :4317)
#   Loki       — log aggregation
#   Promtail   — log collector (extracts trace_id from JSON logs)
#   Prometheus + Grafana (kube-prometheus-stack)
#
# All three are correlated in Grafana:
#   Metrics → click trace_id exemplar → Tempo trace
#   Loki log → click trace_id field   → Tempo trace
#   Tempo trace → click service       → Loki logs for that trace
#   Tempo trace → click service       → Prometheus metrics for that service
#
# Usage: bash scripts/install-observability.sh
# Requires: helm, kubectl pointing at a running kind cluster

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OBS="${REPO_DIR}/scripts/observability"
RESOURCES="${REPO_DIR}/resources"
NAMESPACE="monitoring"

# Verify kind context
CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")
if ! echo "${CURRENT_CONTEXT}" | grep -q "kind"; then
  echo "WARNING: Current context '${CURRENT_CONTEXT}' does not look like a kind cluster."
  read -r -p "Continue anyway? [y/N] " confirm
  [[ "${confirm}" =~ ^[Yy]$ ]] || exit 1
fi

echo "==> Adding Helm repos..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

echo "==> Creating namespace '${NAMESPACE}'..."
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# ── 1. Tempo ────────────────────────────────────────────────────────────────
echo ""
echo "==> [1/4] Installing Tempo (traces backend)..."
helm upgrade --install tempo grafana/tempo \
  --namespace "${NAMESPACE}" \
  --values "${OBS}/tempo.yaml" \
  --wait --timeout 3m

# ── 2. Loki ─────────────────────────────────────────────────────────────────
echo ""
echo "==> [2/4] Installing Loki (logs backend)..."
helm upgrade --install loki grafana/loki \
  --namespace "${NAMESPACE}" \
  --values "${OBS}/loki.yaml" \
  --wait --timeout 3m

# ── 3. Promtail ─────────────────────────────────────────────────────────────
echo ""
echo "==> [3/4] Installing Promtail (log collector)..."
helm upgrade --install promtail grafana/promtail \
  --namespace "${NAMESPACE}" \
  --values "${OBS}/promtail.yaml" \
  --wait --timeout 3m

# ── 4. kube-prometheus-stack (Prometheus + Grafana) ─────────────────────────
echo ""
echo "==> [4/4] Installing Prometheus + Grafana..."
# Delete the Grafana datasource configmap on reinstall so stale "isDefault"
# entries in the Grafana SQLite DB don't cause a CrashLoopBackOff.
kubectl delete configmap kube-prometheus-stack-grafana -n "${NAMESPACE}" --ignore-not-found
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace "${NAMESPACE}" \
  --values "${OBS}/kube-prometheus-stack.yaml" \
  --wait --timeout 5m

# ── 5. FrontDesk AI App Dashboard ───────────────────────────────────────────
echo ""
echo "==> [5/6] Installing FrontDesk AI app health dashboard..."
kubectl create configmap frontdeskai-app-dashboard \
  --namespace "${NAMESPACE}" \
  --from-file=frontdeskai-dashboard.json="${RESOURCES}/frontdeskai-dashboard.json" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl label configmap frontdeskai-app-dashboard \
  --namespace "${NAMESPACE}" \
  grafana_dashboard=1 --overwrite

# ── 6. Grafana Correlations ──────────────────────────────────────────────────
echo ""
echo "==> [6/6] Installing Grafana correlations (global drill-through links)..."
kubectl create configmap frontdeskai-correlations \
  --namespace "${NAMESPACE}" \
  --from-file=correlations.yaml="${OBS}/grafana-correlations.yaml" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl label configmap frontdeskai-correlations \
  --namespace "${NAMESPACE}" \
  grafana_correlation=1 --overwrite

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "=========================================================="
echo " Observability stack installed in namespace '${NAMESPACE}'"
echo "=========================================================="
echo ""
echo " Grafana  (metrics + logs + traces):"
echo "   kubectl port-forward -n ${NAMESPACE} svc/kube-prometheus-stack-grafana 3000:80 &"
echo "   http://localhost:3000  (agenticai / agentgrow.io)"
echo ""
echo " Prometheus (raw metrics):"
echo "   kubectl port-forward -n ${NAMESPACE} svc/kube-prometheus-stack-prometheus 9090:9090 &"
echo "   http://localhost:9090"
echo ""
echo " Datasource correlation in Grafana:"
echo "   Prometheus → trace_id exemplar  → Tempo"
echo "   Loki log   → trace_id field     → Tempo"
echo "   Tempo span → tracesToLogs       → Loki"
echo "   Tempo span → tracesToMetrics    → Prometheus"
echo ""
echo " FrontDesk AI must be deployed and running for traces/metrics to appear."
echo "   bash scripts/deploy.sh"
echo "=========================================================="
