# FrontDesk AI — User Manual

## Logging In

Log in with your work email and the shared password (default: `brainupgrade`). On first login that
password is verified against the shared one and then stored as a personal hash — from then on only your
own password works. Admin accounts (listed in `ADMIN_EMAILS`) get two extra header items: **Analytics**
and **Knowledge Base**.

---

## Employee Self-Service

Type your request in plain English and press **Enter** (**Shift+Enter** for a new line). Routing is
automatic — you never pick a category.

| What you say | Handled by |
|---|---|
| *"Apply for 3 days casual leave from Monday"* | HR |
| *"What is the leave balance for earned leave?"* | HR (RAG policy lookup) |
| *"My laptop screen is flickering"* | Tech (raises a ticket) |
| *"When will my travel expense be reimbursed?"* | Finance |
| *"Show my last 3 months payslips"* | Finance |
| *"Book the Ganges room for 2pm tomorrow for 6 people"* | Facilities |
| *"I want to change my password"* | Account |

Every response has 👍 / 👎 buttons; feedback shows up in the admin analytics dashboard. Requests the
agent can't resolve are escalated to a manager for policy exceptions, or you get the right team's contact
details.

---

## Admin Guide

Admins can do everything employees can, plus manage the system entirely through chat — no config files,
restarts, or rebuilds.

### Knowledge Base

**Knowledge Base** in the header: upload or delete `.md`, `.txt`, or `.pdf` policy documents. They are
indexed into the RAG store immediately and cited by the HR, Tech, Finance, and Facilities workers.

### Analytics

**Analytics** in the header shows conversation volume, category breakdown, escalation and fallback rates,
confidence distribution, and 👍/👎 summary. The same data is available in chat:

```
How many tickets were raised today?
Show escalation rate for this week
```

### LLM Configuration

```
What model are we using?
Switch to qwen3-next:80b on ollama
Change model to llama-3.1-8b-instant on groq
Switch to OpenRouter with google/gemini-2.0-flash-001 and API key sk-or-...
Set fallback to groq llama-3.1-8b-instant
Update ollama api key to ...
```

Providers: **Ollama Cloud** (primary default `gemma4:cloud`), **Groq** (fallback default
`llama-3.3-70b-versatile`), **OpenRouter** (`provider/model` format). Changes take effect immediately,
persist across restarts, and the fallback is used automatically if the primary call fails.

### SMTP Email

```
Configure SMTP with host=smtp.gmail.com port=587 username=me@gmail.com password=mypassword from=noreply@company.com
Show email settings
Send an email to alice@company.com about her leave approval
```

Passwords are encrypted at rest. If `SECRET_KEY` is rotated, re-run `configure_smtp`.

### Skills — Self-Teaching

```
Install a skill to check stock prices
List installed skills
Set the API key for the stock skill to abc123
Show config for the stock skill
```

The system searches the web for a suitable API, writes and validates Python, installs it to disk
(surviving restarts), and makes it available to the relevant domain workers. So:

```
Admin:    "Install a weather lookup skill"       → researched, written, validated, installed, loaded
Admin:    "Set the API key for weather skill to abc123"   → stored encrypted
Employee: "What's the weather in Bangalore?"     → Facilities worker uses it automatically
```

**Bundled skill — OCI compute.** Once installed with its config keys set, employees can manage cloud VMs
from chat (`list all running OCI instances`, `restart the instance named frontdeskai-dev-01`). Launching
and terminating are admin-only. See `skills/oci_compute.md`.

---

## Updating API Keys Without Rebuilding

```
Update ollama api key to ...            # admin chat — zero downtime
```

```bash
bash scripts/update-secret.sh           # from .env — restarts the pod, no rebuild

kubectl patch secret frontdeskai-secret \
  --type=merge -p '{"stringData":{"GROQ_API_KEY":"<new-key>"}}' \
  && kubectl rollout restart deployment/frontdeskai
```

## Changing Your Password

Say `I want to change my password` in chat — the Account agent walks you through it securely.
