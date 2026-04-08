#!/bin/bash
# Configure k3s to use a local (insecure) container registry mirror.
#
# This script:
#   1. Deploys a Docker Registry v2 pod inside k3s (in the kube-system namespace)
#   2. Exposes it on a NodePort (default 30500)
#   3. Writes /etc/rancher/k3s/registries.yaml so k3s containerd trusts it
#   4. Restarts k3s so the registry config is picked up
#
# After this script, push images with:
#   docker tag myimage:latest localhost:30500/myimage:latest
#   docker push localhost:30500/myimage:latest
#
# In k8s manifests use:
#   image: localhost:30500/myimage:latest
#
# Usage:
#   sudo bash scripts/setup-k3s-registry.sh [--port 30500]

set -euo pipefail

REGISTRY_PORT="${REGISTRY_PORT:-30500}"
REGISTRY_NAMESPACE="kube-system"
REGISTRY_STORAGE_SIZE="5Gi"

for arg in "$@"; do
  case "$arg" in
    --port) shift; REGISTRY_PORT="$1" ;;
    *) ;;
  esac
done

# ── Root check ────────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  echo "ERROR: This script must be run as root (sudo)."
  exit 1
fi

KUBECTL="k3s kubectl"

# ── Deploy registry into kube-system ──────────────────────────────────────────
echo "==> Deploying local registry (port ${REGISTRY_PORT})..."
$KUBECTL apply -f - <<EOF
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: local-registry-pvc
  namespace: ${REGISTRY_NAMESPACE}
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: ${REGISTRY_STORAGE_SIZE}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: local-registry
  namespace: ${REGISTRY_NAMESPACE}
  labels:
    app: local-registry
spec:
  replicas: 1
  selector:
    matchLabels:
      app: local-registry
  template:
    metadata:
      labels:
        app: local-registry
    spec:
      containers:
        - name: registry
          image: registry:2
          ports:
            - containerPort: 5000
          env:
            - name: REGISTRY_STORAGE_DELETE_ENABLED
              value: "true"
          volumeMounts:
            - name: registry-data
              mountPath: /var/lib/registry
          resources:
            requests:
              memory: "128Mi"
              cpu: "100m"
            limits:
              memory: "256Mi"
              cpu: "250m"
      volumes:
        - name: registry-data
          persistentVolumeClaim:
            claimName: local-registry-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: local-registry
  namespace: ${REGISTRY_NAMESPACE}
  labels:
    app: local-registry
spec:
  selector:
    app: local-registry
  type: NodePort
  ports:
    - port: 5000
      targetPort: 5000
      nodePort: ${REGISTRY_PORT}
EOF

# ── Write registries.yaml so k3s containerd trusts the registry ───────────────
echo "==> Configuring k3s registry mirror..."
mkdir -p /etc/rancher/k3s
cat > /etc/rancher/k3s/registries.yaml <<EOF
mirrors:
  "localhost:${REGISTRY_PORT}":
    endpoint:
      - "http://localhost:${REGISTRY_PORT}"
configs:
  "localhost:${REGISTRY_PORT}":
    tls:
      insecure_skip_verify: true
EOF

# ── Restart k3s to apply the registry config ──────────────────────────────────
echo "==> Restarting k3s to apply registry config..."
systemctl restart k3s

echo "==> Waiting for k3s to come back up..."
for i in $(seq 1 20); do
  if $KUBECTL get nodes &>/dev/null; then
    break
  fi
  echo "    Attempt ${i}/20..."
  sleep 5
done

echo "==> Waiting for registry pod to be ready..."
$KUBECTL wait --for=condition=ready pod -l app=local-registry \
  -n "${REGISTRY_NAMESPACE}" --timeout=120s

echo ""
echo "======================================================"
echo "  Local registry ready at localhost:${REGISTRY_PORT}"
echo "======================================================"
echo ""
echo "  Push an image:"
echo "    docker tag myimage:latest localhost:${REGISTRY_PORT}/myimage:latest"
echo "    docker push localhost:${REGISTRY_PORT}/myimage:latest"
echo ""
echo "  Reference in manifests:"
echo "    image: localhost:${REGISTRY_PORT}/myimage:latest"
echo ""
