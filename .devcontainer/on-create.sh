#!/usr/bin/env bash
# Runs during onCreateCommand, which IS captured by Codespaces prebuilds
# (prebuilds run up to and including updateContentCommand; postCreateCommand is
# explicitly excluded). Everything here should be a static, cacheable artifact.
#
# Live state — notably the kind cluster itself — must NOT be created here. A
# control plane restored from a prebuild snapshot carries etcd state and serving
# certs bound to the original container IP, which is a reliability trap. Cluster
# creation stays in setup.sh (postCreateCommand). See .devcontainer/setup.sh.
set -euo pipefail

# Keep these two in lockstep: KIND_NODE_IMAGE must be the default node image for
# the pinned kind release, otherwise the pre-pull warms the wrong layers and
# `kind create cluster` downloads a second image.
#   kind v0.32.0 defaults to
#   kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5
KIND_VERSION="v0.32.0"
KIND_NODE_IMAGE="kindest/node:v1.36.1"

echo "==> Installing kind ${KIND_VERSION}..."
curl -Lo /tmp/kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64"
sudo install -o root -g root -m 0755 /tmp/kind /usr/local/bin/kind
rm /tmp/kind

# Pre-pull the node image so `kind create cluster` skips its largest download.
# Deliberately non-fatal: this is a cache warm-up, and failing the whole prebuild
# over it would be worse than launching slightly slower. Whether these layers
# survive into the prebuild snapshot depends on docker-in-docker storage being
# captured — measure launch time before/after rather than assuming.
echo "==> Pre-pulling ${KIND_NODE_IMAGE}..."
if docker pull "${KIND_NODE_IMAGE}"; then
  echo "==> Node image cached."
else
  echo "WARNING: could not pre-pull ${KIND_NODE_IMAGE}; kind will pull it at cluster-create time." >&2
fi
