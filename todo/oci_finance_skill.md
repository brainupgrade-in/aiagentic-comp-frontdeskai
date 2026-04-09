# Plan: `oci_finance_skill` — Cloud Cost Visibility

**Worker:** `finance`  
**Priority:** High  
**Impact:** Employees and finance managers get instant OCI cloud spend data and cost estimates without FinOps team involvement or OCI Console access.

---

## Background

FrontDesk AI's `finance` worker currently handles `get_expense_status`, `submit_expense_claim`, and `get_payslip` (all internal HR data). This skill adds two OCI capabilities:

1. **Usage reports** — actual cloud spend broken down by service/compartment (via `oci-usage-mcp-server`)
2. **Pricing estimates** — on-demand SKU pricing queries for cost planning (via `oci-pricing-mcp-server`)

OCI MCP Servers used:
- [`oracle/mcp` → `src/oci-usage-mcp-server`](https://github.com/oracle/mcp/tree/main/src/oci-usage-mcp-server)
- [`oracle/mcp` → `src/oci-pricing-mcp-server`](https://github.com/oracle/mcp/tree/main/src/oci-pricing-mcp-server)

---

## What Gets Built

A single dynamic skill file: `/shared/.frontdeskai/skills/oci_finance.py`

### Tools exposed

| Tool | OCI Source | Description |
|------|-----------|-------------|
| `oci_get_cloud_usage` | `oci-usage-mcp-server` | Get OCI usage/spend for a date range, grouped by service |
| `oci_list_usage_reports` | `oci-usage-mcp-server` | List available usage report files in Object Storage |
| `oci_get_price` | `oci-pricing-mcp-server` | Get price for a specific OCI service by name or SKU |
| `oci_estimate_cost` | Composite | Estimate monthly cost for N units of a service |

---

## Skill File Structure

```python
# /shared/.frontdeskai/skills/oci_finance.py

SKILL_META = {
    "name": "oci_finance",
    "description": "Query OCI cloud usage/spend and pricing for cost visibility and estimation",
    "categories": ["finance", "analytics"],         # available to both workers
    "config_keys": ["user_ocid", "fingerprint", "tenancy_id", "region", "private_key"],
}

# imports: oci, datetime, urllib.request, json, skills.skill_config
# @tool functions: oci_get_cloud_usage, oci_list_usage_reports,
#                  oci_get_price, oci_estimate_cost
```

---

## Auth: Programmatic OCI Config (No File Mount Required)

Identical helper to `oci_support_skill` — credentials stored in `system_config` DB, private key Fernet-encrypted:

```python
def _get_oci_config() -> dict:
    from skills import skill_config
    cfg = {
        "user":        skill_config("oci_finance", "user_ocid"),
        "fingerprint": skill_config("oci_finance", "fingerprint"),
        "tenancy":     skill_config("oci_finance", "tenancy_id"),
        "region":      skill_config("oci_finance", "region"),
        "key_content": skill_config("oci_finance", "private_key"),
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
| `region` | OCI region identifier (e.g. `us-ashburn-1`) | No |
| `private_key` | PEM private key content | **Yes** |

> `namespace` and `bucket_name` are not required. The skill uses `oci.usage_api.UsageapiClient`
> for structured cost data directly — no usage-report Object Storage bucket needed.

---

## Implementation Steps

### Step 1 — OCI SDK (shared prerequisite)

`oci>=2.120.0` in `requirements.txt`. If `oci_support_skill` is implemented first, no changes needed — same SDK, no volume mounts required for either skill.

### Step 2 — Understand OCI Usage Report API

OCI usage reports are delivered as CSV files to an Object Storage bucket in the tenancy. Access pattern:

```python
config = _get_oci_config()  # built from skill_config() — no file needed
os_client = oci.object_storage.ObjectStorageClient(config)
objects = os_client.list_objects(namespace, "usage-report")
report = os_client.get_object(namespace, "usage-report", object_name)
```

For structured spending data (not raw CSV), use `oci.usage_api.UsageapiClient`:
```python
usage_client = oci.usage_api.UsageapiClient(config)
result = usage_client.request_summarized_usages(
    request_summarized_usages_details=oci.usage_api.models.RequestSummarizedUsagesDetails(
        tenant_id=tenancy_id,
        time_usage_started=start_date,
        time_usage_ended=end_date,
        granularity="MONTHLY",
        group_by=["service"],
        query_type="COST",
    )
)
```

### Step 3 — Understand OCI Pricing API

OCI pricing is publicly available without authentication:
```
GET https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?partNumber=<SKU>
GET https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?displayName=<name>
```

The `oci-pricing-mcp-server` wraps this. Implement directly in the skill using `urllib.request` (no OCI SDK needed for pricing — avoids auth requirement):

```python
import urllib.request, json, urllib.parse

def _fetch_oci_price(search_term: str) -> list[dict]:
    url = f"https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?displayName={urllib.parse.quote(search_term)}"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    return data.get("items", [])
```

This requires no OCI auth — public API.

### Step 4 — Write the skill file

**`oci_get_cloud_usage(start_date: str, end_date: str, group_by: str = "service") -> str`**
- Parse dates as `datetime`
- Call `UsageapiClient.request_summarized_usages(...)` with `query_type="COST"`, `granularity="MONTHLY"`
- `group_by` accepts: `"service"`, `"compartmentName"`, `"region"`, `"skuName"`
- Return formatted table: Service | Cost (USD) | Units
- Include total at bottom

**`oci_list_usage_reports(limit: int = 10) -> str`**
- Call `ObjectStorageClient.list_objects(namespace, bucket_name)`
- Return list of recent report filenames with dates

**`oci_get_price(service_name: str, currency: str = "USD") -> str`**
- Call public pricing API: `https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?displayName=<service_name>`
- Return: SKU, display name, unit of measure, price per unit, currency
- Handle common aliases: "E4.Flex" → "VM.Standard.E4.Flex", "ADB" → "Autonomous Database"

**`oci_estimate_cost(service_name: str, quantity: float, unit: str = "hours", month_hours: float = 730.0) -> str`**
- Calls `_fetch_oci_price(service_name)` internally
- Calculates: `price_per_hour * quantity * month_hours`
- Returns: monthly estimate, annual estimate, breakdown by resource count
- Example: `oci_estimate_cost("VM.Standard.E4.Flex", 8, "OCPUs")` → "8 OCPUs × $X/OCPU/hr × 730hr = $Y/month"

### Step 5 — Install and configure via admin chat

```
install_skill("oci_finance", "OCI cloud usage and pricing tools", <code>)

set_skill_config("oci_finance", "user_ocid",   "ocid1.user.oc1..xxxxxxxxxx")
set_skill_config("oci_finance", "fingerprint", "xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx")
set_skill_config("oci_finance", "tenancy_id",  "ocid1.tenancy.oc1..xxxxxxxxxx")
set_skill_config("oci_finance", "region",      "ap-mumbai-1")
set_skill_config("oci_finance", "namespace",   "your-object-storage-namespace")
set_skill_config("oci_finance", "bucket_name", "usage-report")
set_skill_config("oci_finance", "private_key", "-----BEGIN RSA PRIVATE KEY-----
MIIEo...
-----END RSA PRIVATE KEY-----", is_secret=True)
```

The same OCI user/key can be reused across all three OCI skills — credentials are scoped per skill name
in the DB (prefix `skill.oci_finance.*`) so they are configured and rotated independently.

### Step 6 — Test employee conversations

```
Employee: "What's our OCI cloud spend this month by service?"
→ finance worker: oci_get_cloud_usage("2026-04-01", "2026-04-09", "service")
→ Response: "Compute: $1,240 | Database: $890 | Object Storage: $45 | Total: $2,175"

Employee: "How much does running 10 E4.Flex VMs (8 OCPU, 128GB) cost per month?"
→ finance worker: oci_get_price("VM.Standard.E4.Flex")
→ finance worker: oci_estimate_cost("VM.Standard.E4.Flex", 80, "OCPUs")
→ Response: "80 OCPUs × $0.025/OCPU/hr × 730hr = $1,825/month for 10 VMs"

Employee: "Is our Q1 compute spend on track vs last quarter?"
→ finance worker: oci_get_cloud_usage("2026-01-01","2026-03-31","service") for Q1
→ finance worker: oci_get_cloud_usage("2025-10-01","2025-12-31","service") for Q4 2025
→ Response: formatted comparison
```

---

## Validation Checklist

- [ ] `oci.usage_api.UsageapiClient` instantiates without error in pod
- [ ] `oci_get_cloud_usage` returns data (or empty if no usage in range — not an error)
- [ ] `oci_get_price("Compute")` returns pricing rows from public API (no auth needed)
- [ ] `oci_estimate_cost` calculates correctly for known SKU
- [ ] Finance worker routes "cloud cost" intent to OCI tools (not expense tools)
- [ ] Both finance and analytics workers get tools injected (two categories in SKILL_META)
- [ ] `oci_list_usage_reports` lists objects from correct bucket

---

## Error Handling in Skill Code

```python
except oci.exceptions.ServiceError as e:
    return f"OCI API error {e.status}: {e.message}"
except oci.exceptions.InvalidConfig as e:
    return f"OCI config error: {e}. Ask admin to verify oci_finance config."
except Exception as e:
    return f"Unexpected error fetching OCI data: {e}"
```

For pricing API (no auth), catch `urllib.error.URLError`:
```python
except urllib.error.URLError as e:
    return f"Could not reach OCI pricing API: {e.reason}"
```

---

## Notes

- Usage API (`oci.usage_api`) requires IAM policy: `Allow group FinanceViewers to read usage-reports in tenancy`
- Public pricing API has no auth requirement — safe to call from any environment
- Usage reports have ~24h delay — tell employees: "data reflects usage through yesterday"
- `oci_estimate_cost` is a calculation tool (no OCI auth needed if prices fetched from public API)

---

## Files Changed

| File | Change |
|------|--------|
| `requirements.txt` | Add `oci>=2.120.0` (shared — skip if oci_support_skill already added it) |
| `/shared/.frontdeskai/skills/oci_finance.py` | New skill file (installed at runtime via admin chat) |

No changes to `deployment.yaml` — credentials live entirely in the `system_config` DB.
