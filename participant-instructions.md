# FrontDesk AI — Participant Deployment Guide

## Prerequisites

- You are logged into your sandbox pod
- `kubectl` is configured with your namespace
- You have a Groq API key (get one at https://console.groq.com)

## Deploy (One Command)

```bash
git clone https://github.com/brainupgrade-in/aiagentic-comp-frontdeskai.git
cd aiagentic-comp-frontdeskai
bash k8s/deploy.sh YOUR_GROQ_API_KEY
```

Replace `YOUR_GROQ_API_KEY` with your actual key.

The script will automatically:
1. Detect your namespace from kubectl context
2. Deploy a private container registry in your namespace
3. Build the container image
4. Push the image to your private registry
5. Create the Kubernetes secret with your API key
6. Deploy the application
7. Wait for the app to be ready

## Access the App

Once deployed, open in your browser:

```
https://YOURNAMESPACE-app.brainupgrade.in
```

**Login:** Any email address + password `brainupgrade`

## Verify

```bash
kubectl get pods
kubectl logs deployment/frontdeskai
```

## Redeploy After Code Changes

After modifying the code, rebuild and redeploy:

```bash
bash k8s/build-and-push.sh
kubectl rollout restart deployment/frontdeskai
```

## Troubleshooting

**Pod stuck in ImagePullBackOff:**
```bash
kubectl describe pod -l app=frontdeskai
```
Check that the registry pod is running: `kubectl get pods -l app=registry`

**App not responding:**
```bash
kubectl logs deployment/frontdeskai
```
Check that the GROQ_API_KEY is set correctly: `kubectl get secret frontdeskai-secret -o yaml`
