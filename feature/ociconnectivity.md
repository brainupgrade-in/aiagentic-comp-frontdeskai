# OCI Connectivity — FrontDesk AI

## Overview

FrontDesk AI integrates with Oracle Cloud Infrastructure (OCI) through dynamic skills that let employees manage cloud operations, track support incidents, and query cloud spend — entirely through conversational chat. No OCI Console access required.

All OCI credentials are configured at runtime via admin chat and stored encrypted in the `system_config` database. No Kubernetes Secrets, volume mounts, or pod restarts are needed to add or rotate credentials.

## Status

| Skill | State | Source |
|-------|-------|--------|
| `oci_compute` | **Built** — ships in the repo | [`skills/oci_compute.py`](../skills/oci_compute.py), reference in [`skills/oci_compute.md`](../skills/oci_compute.md) |
| `oci_support` | **Designed, not built** | Plan: [`todo/oci_support_skill.md`](../todo/oci_support_skill.md) |
| `oci_finance` | **Designed, not built** | Plan: [`todo/oci_finance_skill.md`](../todo/oci_finance_skill.md) |

This document describes the shared design across all three. The `oci_support` and `oci_finance`
sections below specify intended behaviour — the tools they list do not exist yet.

The one shared prerequisite is already in place: `oci>=2.120.0` is declared in `app/requirements.txt`
and baked into the container image. Verify inside the pod with:

```bash
kubectl exec deployment/frontdeskai -- python -c "import oci; print(oci.__version__)"
```

---

## Architecture

```
Employee chat
     │
     ▼
FrontDesk AI (LangGraph)
     │
     ├── tech worker  ──────► oci_support_skill  ──► OCI CIMS API   (support incidents)
     │                └──────► oci_compute_skill  ──► OCI Compute API (VM management)
     │
     └── finance worker ────► oci_finance_skill  ──► OCI Usage API  (cloud spend)
                                                 └──► OCI Pricing API (cost estimates)
```

Credentials flow:

```
Admin chat → set_skill_config(..., is_secret=True)
                  │
                  ▼
         system_config DB (Fernet-encrypted, key suffix _enc)
                  │
          decrypted in-memory at tool call time
                  │
                  ▼
         oci.config dict with key_content (PEM)
                  │
                  ▼
         OCI SDK client (ComputeClient / IncidentClient / UsageapiClient)
```

---

## Authentication Design

The OCI Python SDK supports programmatic config via a plain dict with `key_content` (PEM string) instead of `key_file` (path). This allows all credentials to be stored in the existing `system_config` SQLite table and managed entirely through admin chat.

### `_get_oci_config()` helper (used in every OCI skill)

```python
def _get_oci_config(skill_name: str) -> dict:
    from skills import skill_config
    cfg = {
        "user":        skill_config(skill_name, "user_ocid"),
        "fingerprint": skill_config(skill_name, "fingerprint"),
        "tenancy":     skill_config(skill_name, "tenancy_id"),
        "region":      skill_config(skill_name, "region"),
        "key_content": skill_config(skill_name, "private_key"),  # decrypted at runtime
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise ValueError(f"OCI config incomplete. Missing: {missing}. Ask admin to set_skill_config.")
    oci.config.validate_config(cfg)
    return cfg
```

### Credential storage

| Config key | DB key (example for oci_support) | Stored as |
|------------|----------------------------------|-----------|
| `user_ocid` | `skill.oci_support.user_ocid` | Plain text |
| `fingerprint` | `skill.oci_support.fingerprint` | Plain text |
| `tenancy_id` | `skill.oci_support.tenancy_id` | Plain text |
| `region` | `skill.oci_support.region` | Plain text |
| `private_key` | `skill.oci_support.private_key_enc` | **Fernet-encrypted** |
| `csi_number` | `skill.oci_support.csi_number` | Plain text |

The `private_key` is encrypted immediately when `set_skill_config(..., is_secret=True)` is called and decrypted only in-memory at tool invocation time. It is never logged or returned to chat.

---

## Skills

### 1. `oci_support_skill` — Support Ticket Automation _(designed, not built)_

**Worker:** `tech`  
**OCI Service:** CIMS (Customer Incident Management System)

#### Tools

| Tool | Description |
|------|-------------|
| `oci_list_incidents` | List all support incidents for the tenancy |
| `oci_get_incident` | Get full details of a specific incident by ID |
| `oci_create_incident` | Create a new support incident with title, description, and severity |
| `oci_list_incident_resource_types` | List valid product/service categories for incident creation |

#### Required config keys

```
user_ocid, fingerprint, tenancy_id, region, private_key (secret), csi_number
```

#### Admin setup

```
set_skill_config("oci_support", "user_ocid",   "ocid1.user.oc1..xxxxxxxxxx")
set_skill_config("oci_support", "fingerprint", "xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx")
set_skill_config("oci_support", "tenancy_id",  "ocid1.tenancy.oc1..xxxxxxxxxx")
set_skill_config("oci_support", "region",      "ap-mumbai-1")
set_skill_config("oci_support", "csi_number",  "12345678")
set_skill_config("oci_support", "private_key", "<PEM content>", is_secret=True)
```

#### Example conversations

```
Employee: "Raise an OCI ticket — prod load balancer is returning 503s since 3am"
→ oci_create_incident("LB 503 errors", "Production LB returning 503...", "2-HIGH")
→ "Support ticket INC-XXXXXX created. Severity: HIGH."

Employee: "Status of OCI ticket INC-XXXXXX?"
→ oci_get_incident("INC-XXXXXX")
→ "INC-XXXXXX — In Progress. Last update: Oracle engineer reviewing logs."

Employee: "Show all open OCI support cases"
→ oci_list_incidents(limit=10)
→ formatted table of incidents
```

---

### 2. `oci_finance_skill` — Cloud Cost Visibility _(designed, not built)_

**Worker:** `finance`, `analytics`  
**OCI Services:** Usage API, Object Storage (usage reports), Public Pricing API

#### Tools

| Tool | OCI Source | Description |
|------|-----------|-------------|
| `oci_get_cloud_usage` | Usage API | Actual spend for a date range grouped by service/compartment/region |
| `oci_list_usage_reports` | Object Storage | List available usage report CSV files |
| `oci_get_price` | Public Pricing API | Price for a specific OCI service by name or SKU |
| `oci_estimate_cost` | Public Pricing API | Monthly/annual cost estimate for N units of a service |

> **Note:** `oci_get_price` and `oci_estimate_cost` use the public OCI pricing REST API
> (`apexapps.oracle.com`) which requires no authentication. Only the usage tools need OCI credentials.

#### Required config keys

```
user_ocid, fingerprint, tenancy_id, region, private_key (secret), namespace, bucket_name
```

#### Admin setup

```
set_skill_config("oci_finance", "user_ocid",   "ocid1.user.oc1..xxxxxxxxxx")
set_skill_config("oci_finance", "fingerprint", "xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx")
set_skill_config("oci_finance", "tenancy_id",  "ocid1.tenancy.oc1..xxxxxxxxxx")
set_skill_config("oci_finance", "region",      "ap-mumbai-1")
set_skill_config("oci_finance", "namespace",   "your-object-storage-namespace")
set_skill_config("oci_finance", "bucket_name", "usage-report")
set_skill_config("oci_finance", "private_key", "<PEM content>", is_secret=True)
```

#### Required IAM policy

```
Allow group FinanceViewers to read usage-reports in tenancy
```

#### Example conversations

```
Employee: "What's our OCI cloud spend this month by service?"
→ oci_get_cloud_usage("2026-04-01", "2026-04-09", "service")
→ "Compute: $1,240 | Database: $890 | Object Storage: $45 | Total: $2,175"

Employee: "How much does running 10 E4.Flex VMs (8 OCPU, 128GB) cost per month?"
→ oci_get_price("VM.Standard.E4.Flex") + oci_estimate_cost(...)
→ "80 OCPUs × $0.025/OCPU/hr × 730hr = $1,825/month for 10 VMs"

Employee: "Compare Q1 vs Q4 compute spend"
→ Two oci_get_cloud_usage calls → formatted comparison
```

---

### 3. `oci_compute_skill` — Developer VM Self-Service _(built)_

**Worker:** `tech`, `skill_admin`  
**OCI Service:** Core Compute  
**Source:** [`skills/oci_compute.py`](../skills/oci_compute.py) — see [`skills/oci_compute.md`](../skills/oci_compute.md) for the full reference

#### Tools

| Tool | Admin only? | Description |
|------|------------|-------------|
| `oci_list_instances` | No | List compute instances in a compartment |
| `oci_get_instance` | No | Status and details of a specific instance |
| `oci_instance_action` | No | SOFTRESET / STOP / START an instance |
| `oci_launch_instance` | **Yes** | Provision a new compute instance |
| `oci_terminate_instance` | **Yes** | Terminate (delete) an instance |

Destructive operations check `current_user_email` against `ADMIN_EMAILS` env var and return an error for non-admins.

#### Required config keys

```
user_ocid, fingerprint, tenancy_id, region, private_key (secret),
compartment_id, default_subnet_id, default_image_id
```

#### Admin setup

```
set_skill_config("oci_compute", "user_ocid",          "ocid1.user.oc1..xxxxxxxxxx")
set_skill_config("oci_compute", "fingerprint",        "xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx")
set_skill_config("oci_compute", "tenancy_id",         "ocid1.tenancy.oc1..xxxxxxxxxx")
set_skill_config("oci_compute", "region",             "ap-mumbai-1")
set_skill_config("oci_compute", "compartment_id",     "ocid1.compartment.oc1..xxxxxxxxxx")
set_skill_config("oci_compute", "default_subnet_id",  "ocid1.subnet.oc1.ap-mumbai-1.xxxxxxxxxx")
set_skill_config("oci_compute", "default_image_id",   "ocid1.image.oc1.ap-mumbai-1.xxxxxxxxxx")
set_skill_config("oci_compute", "private_key",        "<PEM content>", is_secret=True)
```

To look up the Ubuntu 22.04 image OCID for your region:
```bash
oci compute image list --compartment-id <tenancy-ocid> \
  --operating-system "Canonical Ubuntu" \
  --operating-system-version "22.04" \
  --query 'data[0].id'
```

#### Required IAM policy

```
Allow group DevSupport to manage instances in compartment dev
Allow group DevSupport to read instances in compartment dev
```

Scope to a single compartment — not tenancy-wide.

#### Example conversations

```
Developer: "List my running VMs"
→ oci_list_instances(lifecycle_state="RUNNING")
→ table: Display Name | Shape | State | Public IP | Created

Developer: "My VM inst-abc123 is frozen, please restart it"
→ oci_instance_action("inst-abc123", "SOFTRESET")
→ "Instance 'dev-priya-01' soft reset initiated. Available in ~2 minutes."

Developer: "Stop the staging server overnight to save costs"
→ oci_instance_action("ocid1.instance.oc1...xyz", "STOP")
→ "Instance 'staging-server' STOP initiated."

Admin: "Provision a 4-OCPU 32GB instance named 'new-hire-dev' for Priya"
→ oci_launch_instance("new-hire-dev", "VM.Standard.E4.Flex", 4, 32)
→ "Instance 'new-hire-dev' launched. OCID: ocid1.instance..."
```

---

## Installing Skills

OCI skills are installed at runtime — no image rebuild or redeployment required.

### Method 1 — Copy onto the PVC (how `oci_compute` ships)

```bash
kubectl cp skills/oci_compute.py \
  $(kubectl get pod -l app=frontdeskai -o jsonpath='{.items[0].metadata.name}'):/shared/.frontdeskai/skills/oci_compute.py
kubectl rollout restart deployment/frontdeskai
```

### Method 2 — Admin chat

Login as an admin (see `ADMIN_EMAILS`) and say:
```
Install a new skill called oci_support with this code: [paste skill file content]
```

Skills are auto-loaded on pod startup from `/shared/.frontdeskai/skills/`. A manual reload can be triggered by asking an admin to `"list skills"` (which calls `load_all_skills()` first).

---

## Checking Skill Status

Via admin chat:

```
list_skills
```

Output shows installed skills, their categories, tools, and which config keys are set vs. missing.

```
get_skill_config("oci_support")
```

Output shows all config values (secrets masked as `*** (encrypted)`):
```
Configuration for skill 'oci_support':
  Declared config_keys:
    user_ocid: ocid1.user.oc1..xxxxxxxxxx
    fingerprint: xx:xx:xx:...
    tenancy_id: ocid1.tenancy.oc1..xxxxxxxxxx
    region: ap-mumbai-1
    private_key: *** (encrypted)
    csi_number: 12345678
```

---

## Credential Rotation

To rotate the OCI private key (zero downtime — no pod restart):

```
set_skill_config("oci_support", "private_key", "<new PEM content>", is_secret=True)
```

The new key is used on the next tool call. The old encrypted value in `system_config` is overwritten.
No pod restart and no change to `deployment.yaml`, Kubernetes Secrets, or volume mounts is required —
credentials live entirely in the `system_config` DB.

---

## Error Reference

| Error message | Cause | Fix |
|--------------|-------|-----|
| `OCI config incomplete. Missing: [...]` | One or more config keys not set | Run `set_skill_config` for missing keys |
| `OCI config invalid: ...` | Malformed OCID, fingerprint, or PEM | Re-enter the value; check for copy-paste whitespace |
| `OCI API error 404: ...` | Resource (instance/incident) not found | Verify the OCID or incident ID |
| `OCI API error 403: ...` | IAM policy missing or insufficient | Add required IAM policy for the OCI user |
| `OCI API error 429: ...` | API rate limit hit | Retry after a few seconds |
| `This operation requires admin privileges` | Non-admin user called launch/terminate | Login as admin or request via admin |
