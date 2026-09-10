# Evals

Small measured checks for behaviour that a unit test cannot pin down, run against
the live model. They exist because a prompt regression is invisible to `pytest`.

## `hr_worker_eval.py`

Does the HR worker call its tools, and does it still refuse for other people?

Runs the **real** `hr_worker` against the **real** model, so it needs the app's
environment. Run it in the deployed pod:

```bash
NS=agenticaiu31
kubectl -n $NS cp scripts/eval/hr_worker_eval.py \
  $(kubectl -n $NS get pod -l app=frontdeskai -o jsonpath='{.items[0].metadata.name}'):/tmp/e.py
kubectl -n $NS exec deploy/frontdeskai -- python3 /tmp/e.py
```

To compare a prompt change before rebuilding the image, stage a patched copy and
point `APP_DIR` at it (`/app` is root-owned, so it cannot be edited in place):

```bash
kubectl -n $NS exec -i deploy/frontdeskai -- bash -c \
  'rm -rf /tmp/app2 && cp -r /app /tmp/app2 && cat > /tmp/a.b64 && base64 -d /tmp/a.b64 > /tmp/app2/agents.py' \
  < <(base64 -w0 app/agents.py)
kubectl -n $NS exec deploy/frontdeskai -- env APP_DIR=/tmp/app2 python3 /tmp/e.py
```

Eight cases: four requests × clean and poisoned history. A full run is ~16 model
calls, about three minutes.

**Scores on qwen36-35b-a3b-lab, 2026-09-10:** before the HR identity-wording fix
**10/16**, after **16/16**. The four failures were all under poisoned history.
