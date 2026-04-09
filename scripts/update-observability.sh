#!/bin/bash
# Update observability stack components without full reinstall.
# Run this after changing helm values, datasource config, or the Grafana dashboard.
#
# Usage:
#   bash scripts/update-observability.sh            # update everything
#   bash scripts/update-observability.sh grafana     # Grafana + dashboard only
#   bash scripts/update-observability.sh dashboard   # dashboard ConfigMap only
#   bash scripts/update-observability.sh prometheus  # Prometheus only
#   bash scripts/update-observability.sh loki        # Loki only
#   bash scripts/update-observability.sh promtail    # Promtail only
#   bash scripts/update-observability.sh tempo       # Tempo only

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OBS="${REPO_DIR}/scripts/observability"
RESOURCES="${REPO_DIR}/resources"
NAMESPACE="monitoring"
TARGET="${1:-all}"

# ── Helpers ──────────────────────────────────────────────────────────────────
update_tempo() {
  echo "==> Updating Tempo..."
  helm upgrade --install tempo grafana/tempo \
    --namespace "${NAMESPACE}" \
    --values "${OBS}/tempo.yaml" \
    --wait --timeout 3m
  echo "    Tempo updated"
}

update_loki() {
  echo "==> Updating Loki..."
  helm upgrade --install loki grafana/loki \
    --namespace "${NAMESPACE}" \
    --values "${OBS}/loki.yaml" \
    --wait --timeout 3m
  echo "    Loki updated"
}

update_promtail() {
  echo "==> Updating Promtail..."
  helm upgrade --install promtail grafana/promtail \
    --namespace "${NAMESPACE}" \
    --values "${OBS}/promtail.yaml" \
    --wait --timeout 3m
  echo "    Promtail updated"
}

update_prometheus() {
  echo "==> Updating Prometheus + Grafana (kube-prometheus-stack)..."
  # Delete datasource configmap first to avoid stale isDefault crash
  kubectl delete configmap kube-prometheus-stack-grafana -n "${NAMESPACE}" --ignore-not-found
  helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
    --namespace "${NAMESPACE}" \
    --values "${OBS}/kube-prometheus-stack.yaml" \
    --wait --timeout 5m
  echo "    Prometheus + Grafana updated"
}

update_dashboard() {
  echo "==> Updating FrontDesk AI app health dashboard..."
  kubectl create configmap frontdeskai-app-dashboard \
    --namespace "${NAMESPACE}" \
    --from-file=frontdeskai-dashboard.json="${RESOURCES}/frontdeskai-dashboard.json" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl label configmap frontdeskai-app-dashboard \
    --namespace "${NAMESPACE}" \
    grafana_dashboard=1 --overwrite
  echo "    Dashboard ConfigMap applied — Grafana sidecar will reload it within ~30s"
  echo "    URL: /d/frontdeskai-app"
}

update_grafana() {
  update_prometheus
  update_dashboard
}

# ── Dispatch ─────────────────────────────────────────────────────────────────
case "${TARGET}" in
  all)
    update_tempo
    update_loki
    update_promtail
    update_prometheus
    update_dashboard
    ;;
  grafana)    update_grafana ;;
  dashboard)  update_dashboard ;;
  prometheus) update_prometheus ;;
  loki)       update_loki ;;
  promtail)   update_promtail ;;
  tempo)      update_tempo ;;
  *)
    echo "ERROR: Unknown target '${TARGET}'"
    echo "Usage: $0 [all|grafana|dashboard|prometheus|loki|promtail|tempo]"
    exit 1
    ;;
esac

echo ""
echo "==> Done. Grafana: http://localhost:3000  (agenticai / agentgrow.io)"
