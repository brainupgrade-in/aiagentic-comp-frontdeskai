# FrontDesk AI — User Manual

## Logging In

Open the app in your browser. Log in with your work email and the shared password (default: `brainupgrade`).

- **First login:** your password is verified against the shared password, then a personal hash is stored. From then on only your stored password works.
- **Admin accounts** (configured in `ADMIN_EMAILS`) see two extra menu items in the header: **Analytics** and **Knowledge Base**.

---

## Employee Self-Service

### Submitting a Request

Type your request in plain English in the chat box and press **Enter** (or **Shift+Enter** for a new line). The system automatically routes it to the right department — you do not need to select a category.

| What you say | Handled by |
|---|---|
| *"Apply for 3 days casual leave from Monday"* | HR |
| *"What is the leave balance for earned leave?"* | HR (RAG policy lookup) |
| *"My laptop screen is flickering"* | Tech (raises a ticket) |
| *"Production database is slow"* | Tech |
| *"When will my travel expense be reimbursed?"* | Finance |
| *"I need a standing desk"* | Facilities |
| *"Book the Ganges room for 2pm tomorrow for 6 people"* | Facilities |
| *"Show my last 3 months payslips"* | Finance |
| *"I want to change my password"* | Account |

After each response the input box stays focused — type your next message immediately.

### Feedback

Every assistant response has 👍 / 👎 buttons. Clicking one records your feedback (visible in the admin analytics dashboard).

### Escalation

If the agent cannot resolve your request, it is automatically escalated to a manager for policy exceptions, or you receive contact details for the relevant team.

---

## Admin Guide

Admin accounts can do everything employees can, plus manage the system entirely through chat — no config files, restarts, or rebuilds required.

### Knowledge Base Management

Go to **Knowledge Base** in the header. Upload `.md`, `.txt`, or `.pdf` policy documents. They are indexed into the RAG vector store immediately — HR, Tech, Finance, and Facilities workers will cite them in responses. You can also delete documents from the same page.

### Analytics Dashboard

Go to **Analytics** in the header to view:
- Conversation volume over time
- Category breakdown (HR / Tech / Finance / Facilities / etc.)
- Escalation and fallback rates
- Confidence score distribution
- 👍 / 👎 feedback summary

You can also query analytics via chat:
```
How many tickets were raised today?
Show escalation rate for this week
How many conversations happened last month?
```

### LLM Configuration (via Chat)

```
What model are we using?
Change model to llama-3.1-8b-instant
Switch to OpenRouter with google/gemini-2.0-flash-001 and API key sk-or-...
Update groq api key to gsk_...
```

Changes take effect immediately and persist across restarts — no rebuild needed.

**Supported providers:**
- **Groq** (default): `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `mixtral-8x7b-32768`, etc.
- **OpenRouter**: 200+ models via `provider/model` format (e.g. `google/gemini-2.0-flash-001`, `anthropic/claude-3.5-sonnet`)

### SMTP Email Configuration (via Chat)

```
Configure SMTP with host=smtp.gmail.com port=587 username=me@gmail.com password=mypassword from=noreply@company.com
Show email settings
Send an email to alice@company.com about her leave approval
```

SMTP passwords are encrypted at rest. If `SECRET_KEY` is rotated, re-run `configure_smtp`.

### Skill Installation — Self-Teaching (via Chat)

Teach the system new capabilities at runtime:

```
Install a skill to check stock prices
List installed skills
Set the API key for the stock skill to abc123
Show config for the stock skill
```

The system will:
1. Search the web for suitable APIs
2. Write and validate Python code
3. Install it to disk (persists across restarts)
4. Make it immediately available to the relevant domain workers

#### Skill lifecycle example

```
Admin: "Install a weather lookup skill"
  → system researches APIs, writes code, validates, installs, loads

Admin: "Set the API key for weather skill to abc123"
  → stored encrypted in DB

Employee: "What's the weather in Bangalore?"
  → Facilities worker uses the weather tool automatically
```

---

## Updating API Keys Without Rebuilding

**Option 1 — Via admin chat (zero downtime):**
```
Update groq api key to gsk_...
```

**Option 2 — Via Kubernetes secret patch (requires pod restart, no rebuild):**
```bash
kubectl patch secret frontdeskai-secret \
  --type=merge -p '{"stringData":{"GROQ_API_KEY":"<new-key>"}}'
kubectl rollout restart deployment/frontdeskai
```

---

## Changing Your Password

In the chat, say:
```
I want to change my password
```
The Account agent will guide you through it securely.
