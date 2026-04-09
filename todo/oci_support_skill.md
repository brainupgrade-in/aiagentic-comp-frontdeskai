# Plan: `oci_support_skill` — OCI Support Ticket Automation

**Worker:** `tech`  
**Priority:** High  
**Impact:** Employees can create and track OCI support incidents conversationally, without logging into OCI Console.

---

## Background

FrontDesk AI's `tech` worker currently handles IT tickets via `create_ticket`, `get_ticket_status`, and `list_my_tickets` (internal SQLite-backed). This skill extends it to Oracle Cloud Infrastructure's official Support API (CIMS), so employees can raise and track cloud infrastructure incidents through the same chat interface.

OCI MCP Server used: [`oracle/mcp` → `src/oci-support-mcp-server`](https://github.com/oracle/mcp/tree/main/src/oci-support-mcp-server)

---

## What Gets Built

A single dynamic skill file: `/shared/.frontdeskai/skills/oci_support.py`

### Tools exposed

| Tool | Description |
|------|-------------|
| `oci_list_incidents` | List all support incidents for the tenancy |
| `oci_get_incident` | Get details of a specific incident by ID |
| `oci_create_incident` | Create a new OCI support incident |
| `oci_list_incident_resource_types` | List valid resource categories for incident creation |

---

## Skill File Structure

```python
# /shared/.frontdeskai/skills/oci_support.py

SKILL_META = {
    "name": "oci_support",
    "description": "Create and track Oracle Cloud Infrastructure (OCI) support incidents via CIMS API",
    "categories": ["tech"],
    "config_keys": ["user_ocid", "fingerprint", "tenancy_id", "region", "private_key", "csi_number"],
}

# imports: oci, skills.skill_config
# @tool functions: oci_list_incidents, oci_get_incident, oci_create_incident,
#                  oci_list_incident_resource_types
```

---

## Auth: Programmatic OCI Config (No File Mount Required)

The OCI Python SDK accepts credentials as a plain dict with `key_content` instead of `key_file`.
All values are stored in the `system_config` DB via `set_skill_config`, with the private key
Fernet-encrypted at rest. **No Kubernetes Secret, no volume mount, no `~/.oci/config` file needed.**

```python
def _get_oci_config() -> dict:
    from skills import skill_config
    cfg = {
        "user":        skill_config("oci_support", "user_ocid"),
        "fingerprint": skill_config("oci_support", "fingerprint"),
        "tenancy":     skill_config("oci_support", "tenancy_id"),
        "region":      skill_config("oci_support", "region"),
        "key_content": skill_config("oci_support", "private_key"),  # decrypted at runtime
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise ValueError(f"OCI config incomplete. Missing: {missing}. Ask admin to run set_skill_config.")
    oci.config.validate_config(cfg)
    return cfg
```

### Config keys

| Key | Description | Secret? |
|-----|-------------|---------|
| `user_ocid` | OCI user OCID (`ocid1.user.oc1..xxx`) | No |
| `fingerprint` | API key fingerprint (`xx:xx:xx:...`) | No |
| `tenancy_id` | Tenancy OCID (`ocid1.tenancy.oc1..xxx`) | No |
| `region` | OCI region identifier (e.g. `ap-mumbai-1`) | No |
| `private_key` | PEM private key content (full `-----BEGIN...-----END-----`) | **Yes** |
| `csi_number` | Oracle CSI number for support entitlement | No |

---

## Implementation Steps

### Step 1 — Install OCI Python SDK in the container image

Edit `requirements.txt`:
```
oci>=2.120.0
```

Rebuild and redeploy (one command):
```bash
bash scripts/deploy.sh
```

This is the **only infra change** required. No K8s Secrets, no volume mounts.

### Step 2 — Write the skill file

Create `/shared/.frontdeskai/skills/oci_support.py` with `_get_oci_config()` helper and these `@tool` functions:

**`oci_list_incidents(limit: int = 10) -> str`**
- Call `_get_oci_config()` → `oci.cims.IncidentClient(config)`
- Call `.list_incidents(csi=csi_number, tenancy_id=tenancy_id)`
- Return formatted table: ID | Title | Severity | Status | Created

**`oci_get_incident(incident_id: str) -> str`**
- Call `IncidentClient.get_incident(incident_id=incident_id, csi=csi_number, tenancy_id=tenancy_id)`
- Return full details: description, status, severity, updates, closed_at

**`oci_create_incident(title: str, description: str, severity: str = "4-MINOR") -> str`**
- Call `IncidentClient.create_incident(...)` with `CreateIncidentDetails`
- Severity options: `"1-CRITICAL"`, `"2-HIGH"`, `"3-MEDIUM"`, `"4-MINOR"`
- Return incident ID and Oracle Support portal URL

**`oci_list_incident_resource_types() -> str`**
- Call `IncidentClient.list_incident_resource_types()`
- Return list of valid `problemType` / `serviceName` values for use with `oci_create_incident`

### Step 3 — Install skill via admin chat

Login as `admin@unigps.in` and say:
```
Install a new skill called oci_support with this code: [paste skill file content]
```

### Step 4 — Configure OCI credentials via admin chat

```
set_skill_config("oci_support", "user_ocid",   "ocid1.user.oc1..xxxxxxxxxx")
set_skill_config("oci_support", "fingerprint", "xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx")
set_skill_config("oci_support", "tenancy_id",  "ocid1.tenancy.oc1..xxxxxxxxxx")
set_skill_config("oci_support", "region",      "ap-mumbai-1")
set_skill_config("oci_support", "csi_number",  "12345678")
set_skill_config("oci_support", "private_key", "-----BEGIN RSA PRIVATE KEY-----
MIIEo...
-----END RSA PRIVATE KEY-----", is_secret=True)
```

The private key is Fernet-encrypted immediately on receipt and stored in `system_config` with `_enc` suffix.
It is decrypted in-memory only when a tool call executes.

To retrieve the OCI values from the OCI Console:
- **user_ocid:** Profile → User settings → OCID
- **fingerprint:** Profile → API keys → copy fingerprint
- **tenancy_id:** Administration → Tenancy details → OCID
- **region:** Shown in Console top-right region selector (use identifier, e.g. `ap-mumbai-1`)
- **private_key:** The PEM file downloaded when you created the API key pair

### Step 5 — Test employee conversations

```
Employee: "Raise an OCI support ticket — production load balancer returning 503s since 3am"
→ tech worker: oci_create_incident("LB 503 errors", "Production LB returning 503...", "2-HIGH")
→ Response: "Support ticket INC-XXXXXX created. Severity: HIGH."

Employee: "What's the status of my OCI ticket INC-XXXXXX?"
→ tech worker: oci_get_incident("INC-XXXXXX")
→ Response: "INC-XXXXXX — In Progress. Last update: ..."

Employee: "Show me all open OCI support cases"
→ tech worker: oci_list_incidents(limit=10)
→ Response: formatted table of incidents
```

---

## Validation Checklist

- [ ] `oci>=2.120.0` installed in pod: `kubectl exec deployment/frontdeskai -- python -c "import oci; print(oci.__version__)"`
- [ ] All 5 config keys set: admin chat → `get_skill_config("oci_support")`
- [ ] `_get_oci_config()` passes `oci.config.validate_config()` without error
- [ ] `oci_list_incidents` returns incidents (or empty list — not an error)
- [ ] `oci_create_incident` returns a valid incident ID
- [ ] Skill loads: `kubectl logs deployment/frontdeskai | grep oci_support`
- [ ] Tech worker routes "create OCI ticket" intent to `oci_create_incident`
- [ ] `private_key` shown as `*** (encrypted)` in `get_skill_config` output

---

## Error Handling in Skill Code

```python
except ValueError as e:
    return str(e)  # config incomplete message from _get_oci_config()
except oci.exceptions.ServiceError as e:
    return f"OCI API error {e.status}: {e.message}"
except oci.exceptions.InvalidConfig as e:
    return f"OCI config invalid: {e}. Ask admin to verify credentials with set_skill_config."
```

---

## Files Changed

| File | Change |
|------|--------|
| `requirements.txt` | Add `oci>=2.120.0` |
| `/shared/.frontdeskai/skills/oci_support.py` | New skill file (installed at runtime via admin chat) |

No changes to `deployment.yaml` — credentials live entirely in the `system_config` DB.
