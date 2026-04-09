# Skill: oci_compute

Manage Oracle Cloud Infrastructure (OCI) compute instances directly through FrontDesk AI chat.

## Metadata

| Field | Value |
|-------|-------|
| Name | `oci_compute` |
| Categories | `tech`, `skill_admin` |
| Source | `skills/oci_compute.py` |

## Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `oci_list_instances` | List compute instances | `lifecycle_state`: `RUNNING` (default), `STOPPED`, or `ALL` |
| `oci_get_instance` | Get details of a specific instance | `instance_id`: OCID or display name |
| `oci_instance_action` | Restart, stop, or start an instance | `instance_id`: OCID or name; `action`: `SOFTRESET`, `STOP`, `START`, `RESET` |
| `oci_launch_instance` | Launch a new compute instance | `display_name`, `shape` (default `VM.Standard.E4.Flex`), `ocpus` (default 2), `memory_gb` (default 16) |
| `oci_terminate_instance` | Permanently delete an instance | `instance_id`: OCID or display name |

## Configuration

All config keys are set via admin chat: `set skill config for oci_compute, key = <key>, value = <value>`

| Key | Description | Secret |
|-----|-------------|--------|
| `user_ocid` | OCI user OCID (`ocid1.user.oc1..`) | No |
| `fingerprint` | API key fingerprint (`xx:xx:xx:...`) | No |
| `tenancy_id` | Tenancy OCID (`ocid1.tenancy.oc1..`) | No |
| `region` | OCI region identifier (e.g. `us-ashburn-1`) | No |
| `private_key` | PEM content of the OCI API private key | Yes (encrypted) |
| `compartment_id` | Target compartment OCID | No |
| `default_subnet_id` | Subnet OCID used when launching instances | No |
| `default_image_id` | OS image OCID used when launching instances | No |

### OCI API Key Setup (one-time)

1. OCI Console → Profile → API Keys → Add API Key → generate or paste public key
2. Copy the fingerprint and download/note the private key PEM
3. Find your user OCID at Profile → User Settings
4. Find your tenancy OCID at Profile → Tenancy

## Installation

The skill file is deployed to the pod PVC at `/shared/.frontdeskai/skills/oci_compute.py`.
It auto-loads on pod startup. After updating the file, restart the pod:

```bash
kubectl cp skills/oci_compute.py <pod>:/shared/.frontdeskai/skills/oci_compute.py
kubectl rollout restart deployment/frontdeskai
```

## IAM Policy Requirements

The OCI user needs the following IAM policies in the target compartment:

```
Allow user <username> to manage instances in compartment <compartment-name>
Allow user <username> to use subnets in compartment <compartment-name>
Allow user <username> to use vnics in compartment <compartment-name>
Allow user <username> to use images in compartment <compartment-name>
```

Read-only operations (list, get) work with `inspect` level permissions. Launch and terminate require `manage`.

## Example Chat Prompts

```
list all running OCI instances
list all vm instances running or being created
show details for instance frontdeskai-dev-01
restart the instance named frontdeskai-dev-01
stop instance ocid1.instance.oc1.iad.xxxxx
launch a new OCI instance named frontdeskai-dev-01, 2 OCPUs, 16GB RAM
terminate instance frontdeskai-dev-01
```

## Notes

- Lazy `import oci` inside each tool — the OCI SDK is not loaded at startup to avoid OOM
- Instance lookup by display name is case-insensitive and supports partial matches
- `oci_terminate_instance` is irreversible and does not preserve the boot volume
- Config values are read at call time from the encrypted `system_config` DB table
