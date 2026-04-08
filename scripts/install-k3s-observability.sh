#!/bin/bash
# Install the full observability stack on k3s for FrontDesk AI.
#
# Components (all in the 'monitoring' namespace):
#   - Prometheus       — scrapes /metrics from frontdeskai pods
#   - Grafana          — dashboards (metrics, logs, traces)
#   - Loki             — log aggregation backend
#   - Promtail         — DaemonSet shipping container logs to Loki
#   - Tempo            — distributed trace backend (OTLP gRPC on 4317)
#
# Grafana is pre-provisioned with:
#   - Prometheus, Loki, and Tempo datasources (with trace-to-log and log-to-trace links)
#   - A FrontDesk AI overview dashboard
#
# Prerequisites:
#   - k3s cluster running (scripts/install-k3s-server.sh)
#   - helm v3 installed
#
# Usage:
#   bash scripts/install-k3s-observability.sh
#
# Environment overrides:
#   GRAFANA_PASSWORD   Admin password    (default: frontdesk)
#   GRAFANA_HOST       Ingress hostname  (default: grafana.local)
#   MONITORING_NS      Namespace         (default: monitoring)
#   STORAGE_SIZE       PVC size          (default: 5Gi)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MONITORING_NS="${MONITORING_NS:-monitoring}"
GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-frontdesk}"
GRAFANA_HOST="${GRAFANA_HOST:-grafana.local}"
STORAGE_SIZE="${STORAGE_SIZE:-5Gi}"

# ── Preflight checks ─────────────────────────────────────────────────────────
command -v kubectl >/dev/null || { echo "ERROR: kubectl not found"; exit 1; }
command -v helm >/dev/null 2>&1 || {
  echo "==> Helm not found. Installing..."
  curl -sfL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
}

kubectl get nodes &>/dev/null || { echo "ERROR: Cannot reach k3s cluster"; exit 1; }

# ── Create namespace ──────────────────────────────────────────────────────────
kubectl create namespace "${MONITORING_NS}" --dry-run=client -o yaml | kubectl apply -f -
echo "==> Namespace: ${MONITORING_NS}"

# ── Add Helm repos ────────────────────────────────────────────────────────────
echo "==> Adding Helm repositories..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>/dev/null || true
helm repo add grafana https://grafana.github.io/helm-charts 2>/dev/null || true
helm repo update

# ══════════════════════════════════════════════════════════════════════════════
# 1. TEMPO — trace backend (must be up before the app starts sending spans)
# ══════════════════════════════════════════════════════════════════════════════
echo "==> Installing Tempo..."
helm upgrade --install tempo grafana/tempo \
  --namespace "${MONITORING_NS}" \
  --set tempo.storage.trace.backend=local \
  --set tempo.storage.trace.local.path=/var/tempo/traces \
  --set tempo.receivers.otlp.protocols.grpc.endpoint="0.0.0.0:4317" \
  --set tempo.receivers.otlp.protocols.http.endpoint="0.0.0.0:4318" \
  --set persistence.enabled=true \
  --set persistence.size="${STORAGE_SIZE}" \
  --wait --timeout 180s

# ══════════════════════════════════════════════════════════════════════════════
# 2. LOKI — log aggregation backend
# ══════════════════════════════════════════════════════════════════════════════
echo "==> Installing Loki..."
helm upgrade --install loki grafana/loki \
  --namespace "${MONITORING_NS}" \
  --set deploymentMode=SingleBinary \
  --set loki.auth_enabled=false \
  --set loki.commonConfig.replication_factor=1 \
  --set loki.storage.type=filesystem \
  --set singleBinary.replicas=1 \
  --set singleBinary.persistence.enabled=true \
  --set singleBinary.persistence.size="${STORAGE_SIZE}" \
  --set monitoring.selfMonitoring.enabled=false \
  --set monitoring.lokiCanary.enabled=false \
  --set test.enabled=false \
  --set gateway.enabled=false \
  --set chunksCache.enabled=false \
  --set resultsCache.enabled=false \
  --wait --timeout 180s

# ══════════════════════════════════════════════════════════════════════════════
# 3. PROMTAIL — log collector DaemonSet → Loki
# ══════════════════════════════════════════════════════════════════════════════
echo "==> Installing Promtail..."
helm upgrade --install promtail grafana/promtail \
  --namespace "${MONITORING_NS}" \
  --set "config.clients[0].url=http://loki.${MONITORING_NS}.svc.cluster.local:3100/loki/api/v1/push" \
  --set config.snippets.pipelineStages[0].docker='{}' \
  --wait --timeout 180s

# ══════════════════════════════════════════════════════════════════════════════
# 4. PROMETHEUS (kube-prometheus-stack — includes Prometheus Operator + CRDs)
# ══════════════════════════════════════════════════════════════════════════════
echo "==> Installing Prometheus (kube-prometheus-stack, Grafana disabled)..."
helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
  --namespace "${MONITORING_NS}" \
  --set grafana.enabled=false \
  --set alertmanager.enabled=false \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.retention=7d \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.accessModes[0]=ReadWriteOnce \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage="${STORAGE_SIZE}" \
  --wait --timeout 300s

# ══════════════════════════════════════════════════════════════════════════════
# 5. GRAFANA — dashboards with pre-provisioned datasources
# ══════════════════════════════════════════════════════════════════════════════
echo "==> Installing Grafana..."

# Write the FrontDesk AI dashboard JSON to a temp file
DASHBOARD_JSON=$(mktemp)
trap 'rm -f "${DASHBOARD_JSON}"' EXIT
cat > "${DASHBOARD_JSON}" <<'DASHBOARD_EOF'
{
  "annotations": { "list": [] },
  "editable": true,
  "title": "FrontDesk AI",
  "uid": "frontdeskai",
  "tags": ["frontdeskai"],
  "panels": [
    {
      "id": 1,
      "title": "Request Rate (req/s)",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [{ "expr": "rate(frontdeskai_request_duration_seconds_count[5m])", "legendFormat": "req/s" }]
    },
    {
      "id": 2,
      "title": "Request Latency (p50 / p95 / p99)",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [
        { "expr": "histogram_quantile(0.50, rate(frontdeskai_request_duration_seconds_bucket[5m]))", "legendFormat": "p50" },
        { "expr": "histogram_quantile(0.95, rate(frontdeskai_request_duration_seconds_bucket[5m]))", "legendFormat": "p95" },
        { "expr": "histogram_quantile(0.99, rate(frontdeskai_request_duration_seconds_bucket[5m]))", "legendFormat": "p99" }
      ]
    },
    {
      "id": 3,
      "title": "Requests by Category",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [{ "expr": "rate(frontdeskai_category_total[5m])", "legendFormat": "{{ category }}" }]
    },
    {
      "id": 4,
      "title": "LLM Call Latency by Agent",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [{ "expr": "rate(frontdeskai_llm_call_duration_seconds_sum[5m]) / rate(frontdeskai_llm_call_duration_seconds_count[5m])", "legendFormat": "{{ agent }}" }]
    },
    {
      "id": 5,
      "title": "LLM Tokens Consumed",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 16 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [{ "expr": "rate(frontdeskai_llm_tokens_total[5m])", "legendFormat": "{{ agent }}" }]
    },
    {
      "id": 6,
      "title": "Escalations / Fallbacks / Errors",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 16 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [
        { "expr": "rate(frontdeskai_escalations_total[5m])", "legendFormat": "escalations" },
        { "expr": "rate(frontdeskai_fallbacks_total[5m])", "legendFormat": "fallbacks" },
        { "expr": "rate(frontdeskai_agent_errors_total[5m])", "legendFormat": "errors" }
      ]
    },
    {
      "id": 7,
      "title": "Application Logs",
      "type": "logs",
      "gridPos": { "h": 10, "w": 24, "x": 0, "y": 24 },
      "datasource": { "type": "loki", "uid": "loki" },
      "targets": [{ "expr": "{app=\"frontdeskai\"}", "refId": "A" }],
      "options": {
        "showTime": true,
        "sortOrder": "Descending",
        "enableLogDetails": true
      }
    },
    {
      "id": 8,
      "title": "Traces",
      "type": "traces",
      "gridPos": { "h": 10, "w": 24, "x": 0, "y": 34 },
      "datasource": { "type": "tempo", "uid": "tempo" },
      "targets": [{ "queryType": "nativeSearch", "serviceName": "frontdeskai" }]
    }
  ],
  "time": { "from": "now-1h", "to": "now" },
  "refresh": "30s",
  "schemaVersion": 39
}
DASHBOARD_EOF

# Create a ConfigMap from the dashboard JSON for sidecar pickup
kubectl create configmap grafana-dashboard-frontdeskai \
  --from-file=frontdeskai.json="${DASHBOARD_JSON}" \
  -n "${MONITORING_NS}" \
  --dry-run=client -o yaml | \
  kubectl label --local -f - grafana_dashboard=1 -o yaml | \
  kubectl apply -f -

helm upgrade --install grafana grafana/grafana \
  --namespace "${MONITORING_NS}" \
  --set adminPassword="${GRAFANA_PASSWORD}" \
  --set persistence.enabled=true \
  --set persistence.size=2Gi \
  --set sidecar.dashboards.enabled=true \
  --set sidecar.dashboards.label=grafana_dashboard \
  --set sidecar.dashboards.searchNamespace=ALL \
  --set-json 'datasources.datasources\.yaml.apiVersion=1' \
  --set-json "datasources.datasources\.yaml.datasources=[
    {
      \"name\": \"Prometheus\",
      \"type\": \"prometheus\",
      \"uid\": \"prometheus\",
      \"url\": \"http://prometheus-kube-prometheus-prometheus.${MONITORING_NS}.svc.cluster.local:9090\",
      \"access\": \"proxy\",
      \"isDefault\": true
    },
    {
      \"name\": \"Loki\",
      \"type\": \"loki\",
      \"uid\": \"loki\",
      \"url\": \"http://loki.${MONITORING_NS}.svc.cluster.local:3100\",
      \"access\": \"proxy\",
      \"jsonData\": {
        \"derivedFields\": [{
          \"datasourceUid\": \"tempo\",
          \"matcherRegex\": \"\\\"trace_id\\\":\\s*\\\"([a-f0-9]+)\\\"\",
          \"name\": \"TraceID\",
          \"url\": \"$${__value.raw}\"
        }]
      }
    },
    {
      \"name\": \"Tempo\",
      \"type\": \"tempo\",
      \"uid\": \"tempo\",
      \"url\": \"http://tempo.${MONITORING_NS}.svc.cluster.local:3100\",
      \"access\": \"proxy\",
      \"jsonData\": {
        \"tracesToLogsV2\": {
          \"datasourceUid\": \"loki\",
          \"filterByTraceID\": true,
          \"filterBySpanID\": false
        },
        \"tracesToMetrics\": {
          \"datasourceUid\": \"prometheus\"
        },
        \"serviceMap\": {
          \"datasourceUid\": \"prometheus\"
        },
        \"nodeGraph\": {
          \"enabled\": true
        }
      }
    }
  ]" \
  --wait --timeout 180s

# ── Grafana Ingress (k3s Traefik) ────────────────────────────────────────────
echo "==> Creating Grafana ingress (host: ${GRAFANA_HOST})..."
kubectl apply -n "${MONITORING_NS}" -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: grafana
  labels:
    app: grafana
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
spec:
  rules:
    - host: ${GRAFANA_HOST}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: grafana
                port:
                  number: 80
EOF

# ══════════════════════════════════════════════════════════════════════════════
# 6. Apply the FrontDesk AI ServiceMonitor (for Prometheus scraping)
# ══════════════════════════════════════════════════════════════════════════════
echo "==> Applying FrontDesk AI ServiceMonitor..."
kubectl apply -f "${SCRIPT_DIR}/../k8s/servicemonitor.yaml"

# ── Summary ───────────────────────────────────────────────────────────────────
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "<node-ip>")

echo ""
echo "======================================================"
echo "  Observability stack deployed!"
echo "======================================================"
echo ""
echo "  Grafana"
echo "    URL      : http://${GRAFANA_HOST}"
echo "    User     : admin"
echo "    Password : ${GRAFANA_PASSWORD}"
echo ""
echo "  Datasources (pre-provisioned):"
echo "    Prometheus : http://prometheus-kube-prometheus-prometheus.${MONITORING_NS}:9090"
echo "    Loki       : http://loki.${MONITORING_NS}:3100"
echo "    Tempo      : http://tempo.${MONITORING_NS}:3100"
echo ""
echo "  Dashboard:"
echo "    'FrontDesk AI' — metrics, logs, and traces in one view"
echo ""
echo "  Add to /etc/hosts if accessing locally:"
echo "    ${NODE_IP} ${GRAFANA_HOST}"
echo ""
echo "  Verify:"
echo "    kubectl get pods -n ${MONITORING_NS}"
echo ""
