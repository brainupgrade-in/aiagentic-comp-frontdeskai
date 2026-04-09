# Plan: `oci_compute_skill` — Developer VM Self-Service

**Worker:** `tech`  
**Priority:** Medium  
**Impact:** Developers can list, restart, stop/start, and provision OCI compute instances via chat, eliminating the need to open OCI Console or file a cloud-admin ticket.

---

## Background

FrontDesk AI's `tech` worker handles IT support tickets. This skill adds OCI Compute operations so developers can perform routine VM self-service through the same chat interface. Non-destructive operations (list, status, restart) are available to all authenticated users; destructive/expensive operations (launch, terminate) are admin-gated.

Uses the OCI Python SDK (`oci>=2.120.0`) directly — no MCP server needed. The MCP server approach adds a network hop with no functional benefit since both use the same underlying OCI SDK.

---

## What Gets Built

A single dynamic skill file: `/shared/.frontdeskai/skills/oci_compute.py`

### Tools exposed

| Tool | Destructive? | Description |
|------|-------------|-------------|
| `oci_list_instances` | No | List compute instances in a compartment |
| `oci_get_instance` | No | Get status and details of a specific instance |
| `oci_instance_action` | Soft | Perform SOFTRESET / STOP / START on an instance |
| `oci_launch_instance` | Yes — admin only | Provision a new compute instance |
| `oci_terminate_instance` | Yes — admin only | Terminate (delete) an instance |

---

## Skill File Structure

```python
# /shared/.frontdeskai/skills/oci_compute.py

SKILL_META = {
    "name": "oci_compute",
    "description": "Manage OCI compute instances: list, status, restart, stop/start, provision, terminate",
    "categories": ["tech"],
    "config_keys": ["user_ocid", "fingerprint", "tenancy_id", "region", "private_key",
                    "compartment_id", "default_subnet_id", "default_image_id"],
}

# imports: oci, skills.skill_config, auth.current_user_email, os
# @tool functions: oci_list_instances, oci_get_instance, oci_instance_action,
#                  oci_launch_instance, oci_terminate_instance
```

---

## Auth: Programmatic OCI Config (No File Mount Required)

```python
def _get_oci_config() -> dict:
    from skills import skill_config
    cfg = {
        "user":        skill_config("oci_compute", "user_ocid"),
        "fingerprint": skill_config("oci_compute", "fingerprint"),
        "tenancy":     skill_config("oci_compute", "tenancy_id"),
        "region":      skill_config("oci_compute", "region"),
        "key_content": skill_config("oci_compute", "private_key"),
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
| `user_ocid` | OCI user OCID | No |
| `fingerprint` | API key fingerprint | No |
| `tenancy_id` | Tenancy OCID | No |
| `region` | OCI region identifier (e.g. `ap-mumbai-1`) | No |
| `private_key` | PEM private key content | **Yes** |
| `compartment_id` | Default compartment OCID to operate in | No |
| `default_subnet_id` | Default subnet OCID for new instance launches | No |
| `default_image_id` | Default image OCID (e.g. Ubuntu 22.04) for new instances | No |

---

## Implementation Steps

### Step 1 — OCI SDK (shared prerequisite)

`oci>=2.120.0` in `requirements.txt`. If either other OCI skill is implemented first, this is already done. No volume mounts needed.

### Step 2 — Admin-gate design

Destructive operations (`oci_launch_instance`, `oci_terminate_instance`) must check whether the requesting user is an admin. Use the `ADMIN_EMAILS` environment variable (already used in `app.py`):

```python
import os
from auth import current_user_email

def _is_admin() -> bool:
    try:
        email = current_user_email.get()
    except LookupError:
        return False
    admin_emails = [e.strip() for e in os.getenv("ADMIN_EMAILS", "admin@unigps.in").split(",")]
    return email in admin_emails
```

Non-admin calls to destructive tools return:
```
"This operation requires admin privileges. Please contact your cloud admin."
```

### Step 3 — OCI Compute API patterns

```python
import oci

config = _get_oci_config()   # built from skill_config() — no file needed
compute = oci.core.ComputeClient(config)

# List instances
instances = compute.list_instances(compartment_id=compartment_id).data

# Get single instance
instance = compute.get_instance(instance_id=instance_ocid).data

# Perform action (START/STOP/SOFTRESET/RESET/HARDRESET)
compute.instance_action(instance_id=instance_ocid, action="SOFTRESET")

# Launch instance
details = oci.core.models.LaunchInstanceDetails(
    compartment_id=compartment_id,
    availability_domain=ad,
    shape="VM.Standard.E4.Flex",
    shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=2, memory_in_gbs=16),
    source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=image_id),
    create_vnic_details=oci.core.models.CreateVnicDetails(subnet_id=subnet_id),
    display_name=display_name,
)
instance = compute.launch_instance(details).data

# Terminate
compute.terminate_instance(instance_id=instance_ocid, preserve_boot_volume=False)
```

### Step 4 — Write the skill file

**`oci_list_instances(compartment_id: str = "", lifecycle_state: str = "RUNNING") -> str`**
- Falls back to `skill_config("oci_compute","compartment_id")` if `compartment_id` not provided
- `lifecycle_state` accepts: `"RUNNING"`, `"STOPPED"`, `"ALL"`
- Returns table: Display Name | Shape | State | Public IP | Created
- Truncate at 20 rows with count

**`oci_get_instance(instance_id: str) -> str`**
- Returns: display name, shape, OCPU/memory, lifecycle state, public IP, private IP, launch time, fault domain

**`oci_instance_action(instance_id: str, action: str) -> str`**
- `action` accepts: `"SOFTRESET"` (graceful restart), `"STOP"`, `"START"`, `"RESET"` (hard restart)
- Validate action is in allowed set before calling API
- Return: "Instance `<name>` SOFTRESET initiated. It will be available in ~2 minutes."
- Log action with `current_user_email` for auditability

**`oci_launch_instance(display_name: str, shape: str = "VM.Standard.E4.Flex", ocpus: float = 2, memory_gb: float = 16, compartment_id: str = "") -> str`** — **Admin only**
- Check `_is_admin()` first; return error if not admin
- Reads `default_subnet_id` and `default_image_id` from skill config
- Returns: new instance OCID, shape, estimated cost note
- Warn: "This will incur OCI compute charges. Confirm with 'yes' to proceed." — use LLM confirmation pattern (return pending message, require follow-up)

**`oci_terminate_instance(instance_id: str, preserve_boot_volume: bool = False) -> str`** — **Admin only**
- Check `_is_admin()` first
- Get instance details first to show display name in confirmation
- Call `compute.terminate_instance(instance_id, preserve_boot_volume=preserve_boot_volume)`
- Return: "Instance `<name>` (OCID: `<id>`) termination initiated. This is irreversible."

### Step 5 — Install and configure via admin chat

All credentials have been configured via admin chat. See the **Admin Chat Install Prompt** section
for the complete `install_skill` + `set_skill_config` prompts (credentials stored encrypted in DB).

To verify all keys are set:
```
get_skill_config("oci_compute")
```

### Step 6 — Test employee conversations

```
Developer: "List my running VMs in the dev compartment"
→ tech worker: oci_list_instances(lifecycle_state="RUNNING")
→ Response: table of instances with status

Developer: "My instance inst-abc123 is frozen, please restart it"
→ tech worker: oci_get_instance("inst-abc123") → confirm name
→ tech worker: oci_instance_action("inst-abc123", "SOFTRESET")
→ Response: "Instance 'dev-priya-01' soft reset initiated. Available in ~2 minutes."

Developer: "Stop the instance ocid1.instance.oc1...xyz to save costs overnight"
→ tech worker: oci_instance_action("ocid1.instance.oc1...xyz", "STOP")
→ Response: "Instance 'staging-server' STOP initiated."

Admin: "Provision a new 4-OCPU 32GB instance named 'new-hire-dev' for Priya"
→ tech worker: _is_admin() → True
→ tech worker: oci_launch_instance("new-hire-dev", "VM.Standard.E4.Flex", 4, 32)
→ Response: "Instance 'new-hire-dev' launched. OCID: ocid1.instance..."

Admin: "Terminate instance ocid1.instance...abc (it's unused)"
→ tech worker: oci_terminate_instance("ocid1.instance...abc")
→ Response: "Instance 'old-test-server' termination initiated. This is irreversible."
```

---

## Validation Checklist

- [ ] `oci.core.ComputeClient` instantiates in pod without import errors
- [ ] `oci_list_instances` returns instances list (or empty — not an error)
- [ ] `oci_instance_action("...", "SOFTRESET")` works without error on a real instance
- [ ] `oci_launch_instance` is blocked for non-admin users with correct error message
- [ ] `oci_terminate_instance` is blocked for non-admin users
- [ ] Admin can launch and terminate via chat
- [ ] Tech worker picks up the 5 new tools after skill install
- [ ] `_is_admin()` correctly reads `current_user_email` ContextVar (test with both admin and non-admin login)

---

## Security Considerations

1. **Least-privilege IAM:** OCI CLI profile used should have an IAM policy scoped to a single compartment (not tenancy-wide):
   ```
   Allow group DevSupport to manage instances in compartment dev
   Allow group DevSupport to read instances in compartment dev
   ```

2. **Non-admin employees:** Can only `list`, `get`, and perform soft actions (`SOFTRESET`, `STOP`, `START`). Cannot `launch` or `terminate`.

3. **Audit trail:** All `oci_instance_action` calls log `current_user_email` + action + instance_id to structured logs (existing Promtail/Loki pipeline captures this).

4. **Instance OCID validation:** Validate that instance IDs start with `ocid1.instance.` before passing to API to prevent injection.

---

## Error Handling in Skill Code

```python
except ValueError as e:
    return str(e)  # config incomplete message from _get_oci_config()
except oci.exceptions.ServiceError as e:
    if e.status == 404:
        return f"Instance not found: {instance_id}"
    if e.status == 403:
        return f"Permission denied. Verify OCI IAM policy allows this operation."
    return f"OCI API error {e.status}: {e.message}"
except oci.exceptions.InvalidConfig as e:
    return f"OCI config invalid: {e}. Ask admin to verify credentials with set_skill_config."
```

---

## Deployment

The skill code is too large for a single admin chat message. Deploy via `kubectl cp` instead — the
skill auto-loads without a pod restart. Admin chat is only needed to trigger the registry refresh.

### Step 1 — Copy skill file to pod

```bash
kubectl cp /tmp/oci_compute.py \
  $(kubectl get pod -l app=frontdeskai -o jsonpath='{.items[0].metadata.name}'):/shared/.frontdeskai/skills/oci_compute.py
```

### Step 2 — Trigger reload via admin chat (one short message)

```
list skills
```

`list_skills` calls `load_all_skills()` internally — the skill is registered immediately and the
tech worker gets all 5 tools injected on the next request.

### Step 3 — Test

```
list running VM instances on OCI
```

---

## Admin Chat Install Prompt (reference only — too large to paste directly)

The full skill code is at `/tmp/oci_compute.py` and `/shared/.frontdeskai/skills/oci_compute.py`.
For reference, the `install_skill` equivalent would be:

```
install_skill("oci_compute", "Manage OCI compute instances: list, status, restart, stop/start, provision, terminate", """
import oci
import os
from langchain_core.tools import tool

SKILL_META = {
    "name": "oci_compute",
    "description": "Manage OCI compute instances: list, status, restart, stop/start, provision, terminate",
    "categories": ["tech"],
    "config_keys": ["user_ocid", "fingerprint", "tenancy_id", "region", "private_key", "compartment_id", "default_subnet_id", "default_image_id"],
}

def _get_oci_config():
    from skills import skill_config
    cfg = {
        "user":        skill_config("oci_compute", "user_ocid"),
        "fingerprint": skill_config("oci_compute", "fingerprint"),
        "tenancy":     skill_config("oci_compute", "tenancy_id"),
        "region":      skill_config("oci_compute", "region"),
        "key_content": skill_config("oci_compute", "private_key"),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise ValueError(f"OCI config incomplete. Missing: {missing}. Ask admin to run set_skill_config.")
    oci.config.validate_config(cfg)
    return cfg

def _is_admin():
    try:
        from auth import current_user_email
        email = current_user_email.get()
    except Exception:
        return False
    admin_emails = [e.strip() for e in os.getenv("ADMIN_EMAILS", "admin@unigps.in").split(",")]
    return email in admin_emails

def _sc(key):
    from skills import skill_config
    return skill_config("oci_compute", key)

@tool
def oci_list_instances(lifecycle_state: str = "RUNNING") -> str:
    \"\"\"List OCI compute instances. lifecycle_state: RUNNING, STOPPED, or ALL\"\"\"
    try:
        config = _get_oci_config()
        compartment_id = _sc("compartment_id")
        if not compartment_id:
            return "compartment_id not configured. Ask admin to run set_skill_config."
        compute = oci.core.ComputeClient(config)
        kwargs = {"compartment_id": compartment_id}
        if lifecycle_state != "ALL":
            kwargs["lifecycle_state"] = lifecycle_state
        instances = compute.list_instances(**kwargs).data
        if not instances:
            return f"No instances found with state: {lifecycle_state}"
        network = oci.core.VirtualNetworkClient(config)
        lines = [f"{'Name':<30} {'Shape':<25} {'State':<12} {'Public IP'}"]
        lines.append("-" * 85)
        for i in instances:
            try:
                vnics = compute.list_vnic_attachments(compartment_id, instance_id=i.id).data
                public_ip = ""
                if vnics:
                    vnic = network.get_vnic(vnics[0].vnic_id).data
                    public_ip = vnic.public_ip or ""
            except Exception:
                public_ip = "N/A"
            lines.append(f"{i.display_name:<30} {i.shape:<25} {i.lifecycle_state:<12} {public_ip}")
        return "\\n".join(lines)
    except ValueError as e:
        return str(e)
    except oci.exceptions.ServiceError as e:
        return f"OCI error {e.status}: {e.message}"

@tool
def oci_get_instance(instance_id: str) -> str:
    \"\"\"Get details of a specific OCI compute instance by OCID or display name.\"\"\"
    try:
        config = _get_oci_config()
        compartment_id = _sc("compartment_id")
        compute = oci.core.ComputeClient(config)
        if instance_id.startswith("ocid1.instance"):
            instance = compute.get_instance(instance_id).data
        else:
            instances = compute.list_instances(compartment_id).data
            matched = [i for i in instances if instance_id.lower() in i.display_name.lower()]
            if not matched:
                return f"No instance found matching '{instance_id}'"
            instance = matched[0]
        i = instance
        sc = i.shape_config
        return "\\n".join([
            f"Name:        {i.display_name}",
            f"OCID:        {i.id}",
            f"State:       {i.lifecycle_state}",
            f"Shape:       {i.shape}",
            f"OCPUs:       {sc.ocpus if sc else 'N/A'}",
            f"Memory (GB): {sc.memory_in_gbs if sc else 'N/A'}",
            f"Region:      {i.region}",
            f"AD:          {i.availability_domain}",
            f"Launched:    {i.time_created}",
        ])
    except ValueError as e:
        return str(e)
    except oci.exceptions.ServiceError as e:
        return f"OCI error {e.status}: {e.message}"

@tool
def oci_instance_action(instance_id: str, action: str) -> str:
    \"\"\"Perform an action on an OCI instance. action: SOFTRESET, STOP, START, RESET\"\"\"
    allowed = {"SOFTRESET", "STOP", "START", "RESET"}
    if action.upper() not in allowed:
        return f"Invalid action '{action}'. Allowed: {', '.join(allowed)}"
    try:
        config = _get_oci_config()
        compartment_id = _sc("compartment_id")
        compute = oci.core.ComputeClient(config)
        if not instance_id.startswith("ocid1.instance"):
            instances = compute.list_instances(compartment_id).data
            matched = [i for i in instances if instance_id.lower() in i.display_name.lower()]
            if not matched:
                return f"No instance found matching '{instance_id}'"
            instance_id = matched[0].id
            name = matched[0].display_name
        else:
            name = compute.get_instance(instance_id).data.display_name
        compute.instance_action(instance_id, action.upper())
        msgs = {"SOFTRESET": "soft reset initiated. Available in ~2 minutes.", "STOP": "stop initiated.", "START": "start initiated.", "RESET": "hard reset initiated."}
        return f"Instance '{name}' {msgs.get(action.upper(), 'action initiated.')}"
    except ValueError as e:
        return str(e)
    except oci.exceptions.ServiceError as e:
        return f"OCI error {e.status}: {e.message}"

@tool
def oci_launch_instance(display_name: str, shape: str = "VM.Standard.E4.Flex", ocpus: float = 2, memory_gb: float = 16) -> str:
    \"\"\"Launch a new OCI compute instance. Admin only.\"\"\"
    if not _is_admin():
        return "This operation requires admin privileges."
    try:
        config = _get_oci_config()
        compartment_id = _sc("compartment_id")
        subnet_id = _sc("default_subnet_id")
        image_id = _sc("default_image_id")
        if not all([compartment_id, subnet_id, image_id]):
            return "Missing config: compartment_id, default_subnet_id, or default_image_id. Ask admin to set_skill_config."
        compute = oci.core.ComputeClient(config)
        identity = oci.identity.IdentityClient(config)
        ads = identity.list_availability_domains(compartment_id).data
        ad = ads[0].name
        details = oci.core.models.LaunchInstanceDetails(
            compartment_id=compartment_id,
            availability_domain=ad,
            display_name=display_name,
            shape=shape,
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=ocpus, memory_in_gbs=memory_gb),
            source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=image_id, source_type="image"),
            create_vnic_details=oci.core.models.CreateVnicDetails(subnet_id=subnet_id, assign_public_ip=True),
        )
        instance = compute.launch_instance(details).data
        return f"Instance '{display_name}' launched.\\nOCID: {instance.id}\\nShape: {shape} ({ocpus} OCPUs, {memory_gb}GB RAM)\\nState: {instance.lifecycle_state}"
    except ValueError as e:
        return str(e)
    except oci.exceptions.ServiceError as e:
        return f"OCI error {e.status}: {e.message}"

@tool
def oci_terminate_instance(instance_id: str) -> str:
    \"\"\"Terminate (delete) an OCI compute instance. Admin only. This is irreversible.\"\"\"
    if not _is_admin():
        return "This operation requires admin privileges."
    try:
        config = _get_oci_config()
        compartment_id = _sc("compartment_id")
        compute = oci.core.ComputeClient(config)
        if not instance_id.startswith("ocid1.instance"):
            instances = compute.list_instances(compartment_id).data
            matched = [i for i in instances if instance_id.lower() in i.display_name.lower()]
            if not matched:
                return f"No instance found matching '{instance_id}'"
            instance_id = matched[0].id
            name = matched[0].display_name
        else:
            name = compute.get_instance(instance_id).data.display_name
        compute.terminate_instance(instance_id, preserve_boot_volume=False)
        return f"Instance '{name}' (OCID: {instance_id}) termination initiated. This is irreversible."
    except ValueError as e:
        return str(e)
    except oci.exceptions.ServiceError as e:
        return f"OCI error {e.status}: {e.message}"
""")
```

After install succeeds, configure credentials via admin chat (use actual values from `~/.oci/config`
and `~/.oci/oci_api_key_rsa.pem` — do not store them in this file):

```
set_skill_config("oci_compute", "user_ocid",          "<from ~/.oci/config>")
set_skill_config("oci_compute", "fingerprint",        "<from ~/.oci/config>")
set_skill_config("oci_compute", "tenancy_id",         "<from ~/.oci/config>")
set_skill_config("oci_compute", "region",             "<from ~/.oci/config>")
set_skill_config("oci_compute", "compartment_id",     "<target compartment OCID>")
set_skill_config("oci_compute", "default_subnet_id",  "<subnet OCID>")
set_skill_config("oci_compute", "default_image_id",   "<Ubuntu image OCID>")
set_skill_config("oci_compute", "private_key",        "<PEM content>", is_secret=True)
```

Verify all keys are set:
```
get_skill_config("oci_compute")
```

Then test with:
```
list running VM instances on OCI
```

---

## Files Changed

| File | Change |
|------|--------|
| `requirements.txt` | Add `oci>=2.120.0` (shared — skip if already added by another OCI skill) |
| `/shared/.frontdeskai/skills/oci_compute.py` | New skill file (installed at runtime via admin chat) |

No changes to `deployment.yaml` — credentials live entirely in the `system_config` DB.

---

## Implementation Order Dependency

This skill shares the OCI SDK setup with `oci_support_skill` and `oci_finance_skill`. Implement in this order:
1. `oci_support_skill` — sets up `oci` SDK in requirements.txt and OCI config mount
2. `oci_finance_skill` — reuses same infra
3. `oci_compute_skill` — reuses same infra, zero additional infra changes
