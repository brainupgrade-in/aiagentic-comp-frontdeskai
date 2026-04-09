from langchain_core.tools import tool

SKILL_META = {
    "name": "oci_compute",
    "description": "Manage OCI compute instances: list, status, restart, stop/start, provision, terminate",
    "categories": ["tech", "skill_admin"],
    "config_keys": ["user_ocid", "fingerprint", "tenancy_id", "region", "private_key",
                    "compartment_id", "default_subnet_id", "default_image_id"],
}


def _get_oci_config():
    import oci
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


def _sc(key):
    from skills import skill_config
    return skill_config("oci_compute", key)


@tool
def oci_list_instances(lifecycle_state: str = "RUNNING") -> str:
    """List OCI compute instances. lifecycle_state: RUNNING, STOPPED, or ALL"""
    import oci
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
        return "\n".join(lines)
    except ValueError as e:
        return str(e)
    except oci.exceptions.ServiceError as e:
        return f"OCI error {e.status}: {e.message}"


@tool
def oci_get_instance(instance_id: str) -> str:
    """Get details of a specific OCI compute instance by OCID or display name."""
    import oci
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
        return "\n".join([
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
    """Perform an action on an OCI instance. action: SOFTRESET, STOP, START, RESET"""
    import oci
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
        msgs = {
            "SOFTRESET": "soft reset initiated. Available in ~2 minutes.",
            "STOP": "stop initiated.",
            "START": "start initiated.",
            "RESET": "hard reset initiated.",
        }
        return f"Instance '{name}' {msgs.get(action.upper(), 'action initiated.')}"
    except ValueError as e:
        return str(e)
    except oci.exceptions.ServiceError as e:
        return f"OCI error {e.status}: {e.message}"


@tool
def oci_launch_instance(display_name: str, shape: str = "VM.Standard.E4.Flex",
                        ocpus: float = 2, memory_gb: float = 16) -> str:
    """Launch a new OCI compute instance."""
    import oci
    try:
        config = _get_oci_config()
        compartment_id = _sc("compartment_id")
        subnet_id = _sc("default_subnet_id")
        image_id = _sc("default_image_id")
        if not all([compartment_id, subnet_id, image_id]):
            return "Missing config: compartment_id, default_subnet_id, or default_image_id."
        compute = oci.core.ComputeClient(config)
        identity = oci.identity.IdentityClient(config)
        ad = identity.list_availability_domains(compartment_id).data[0].name
        details = oci.core.models.LaunchInstanceDetails(
            compartment_id=compartment_id,
            availability_domain=ad,
            display_name=display_name,
            shape=shape,
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=ocpus, memory_in_gbs=memory_gb),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                image_id=image_id, source_type="image"),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=subnet_id, assign_public_ip=True),
        )
        instance = compute.launch_instance(details).data
        return (f"Instance '{display_name}' launched.\n"
                f"OCID: {instance.id}\n"
                f"Shape: {shape} ({ocpus} OCPUs, {memory_gb}GB RAM)\n"
                f"State: {instance.lifecycle_state}")
    except ValueError as e:
        return str(e)
    except oci.exceptions.ServiceError as e:
        return f"OCI error {e.status}: {e.message}"


@tool
def oci_terminate_instance(instance_id: str) -> str:
    """Terminate (delete) an OCI compute instance. This is irreversible."""
    import oci
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
