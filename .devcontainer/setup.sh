#!/usr/bin/env bash
set -euo pipefail

# Runs as postCreateCommand — NOT captured by Codespaces prebuilds, so this
# executes on every codespace launch. Keep it to work that genuinely cannot be
# baked into a snapshot; cacheable setup belongs in .devcontainer/on-create.sh
# (kind binary, node image pre-pull).

# App dependencies are NOT installed here on purpose. The app runs only in the
# kind cluster, where Containerfile installs app/requirements.txt into the image.
# Installing them again in the devcontainer would just slow down Codespace
# creation (chromadb + onnxruntime are heavy). Uncomment if you ever want to run
# `python app/app.py` directly in the Codespace, or want Pylance to resolve the
# third-party imports:
# echo "==> Installing Python dependencies..."
# pip install --quiet -r app/requirements.txt

if [ ! -f .env ]; then
  echo "==> Seeding .env from .env.example..."
  cp .env.example .env
fi

# Codespaces secrets arrive as environment variables. Copy them into .env so the
# deploy scripts (which read .env, not the environment) pick them up. A secret
# wins over the template's placeholder — notably LANGFUSE_HOST, where keeping the
# default US host while the keys belong to the EU region yields zero traces.
# This runs once at creation, before anyone hand-edits .env; quickstart.sh is the
# repeatable path and deliberately only fills values that are still empty.
python3 - <<'PY'
import os, re, pathlib
env = pathlib.Path(".env")
text = env.read_text()
for var in ("OLLAMA_API_KEY", "GROQ_API_KEY",
            "LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_HOST"):
    value = os.environ.get(var, "")
    if not value:
        continue
    line = f"{var}={value}"
    text, n = re.subn(rf"(?m)^{var}=.*$", lambda _m: line, text)
    if n == 0:
        text = text.rstrip("\n") + f"\n{line}\n"
    print(f"==> {var} taken from the Codespace environment")
env.write_text(text)
PY

# Cluster creation lives in scripts/create-kind-cluster.sh so the Codespace and
# localhost paths stay identical — do not duplicate the kind config here.
bash scripts/create-kind-cluster.sh frontdeskai

# With a key available we can go all the way to a running app with seeded demo
# data; without one there is nothing useful to deploy yet.
have_key=false
grep -qE '^(OLLAMA|GROQ)_API_KEY=.+' .env && have_key=true

if [ "${have_key}" = "true" ]; then
  echo ""
  echo "==> LLM key found — deploying the app so it is ready when you open it..."
  bash scripts/quickstart.sh || {
    echo ""
    echo "!!! Automatic deploy failed. Rerun manually: bash scripts/quickstart.sh"
  }
else
  echo ""
  echo "=========================================================="
  echo " Almost there — two steps left"
  echo "=========================================================="
  echo " 1. Put an LLM API key in .env (either one is enough):"
  echo "      OLLAMA_API_KEY  (primary)   https://ollama.com"
  echo "      GROQ_API_KEY    (fallback)  https://console.groq.com"
  echo " 2. bash scripts/quickstart.sh"
  echo ""
  echo " That deploys the app with demo data pre-seeded, plus the MCP Leave"
  echo " Service, and prints the URL and demo logins when it is ready."
  echo ""
  echo " Tip: save the key as a Codespaces secret named OLLAMA_API_KEY and the"
  echo " next codespace you create deploys itself with no manual steps."
  echo "=========================================================="
  echo " Note: the app runs in the kind cluster only. Its Python deps are baked"
  echo " into the image by Containerfile and deliberately NOT installed here, so"
  echo " 'python app/app.py' will not work in this devcontainer."
  echo "=========================================================="
fi
