"""Oracle Cloud operations for the STRAUSS DRIVE VM over the OCI API (HTTPS): no SSH needed.
Config: ~/strauss-secrets/oci/config. Prints identifiers and states only, never keys or secrets.
    python oci_ops.py status            the VM, its agent plugins, the VCN ingress rules
    python oci_ops.py run <script-file> [timeout-s]   run a bash script on the VM via Run Command, print its output
"""
import os
import sys
import time

import oci

CFG = oci.config.from_file(os.path.expanduser("~/strauss-secrets/oci/config"))
TENANCY = CFG["tenancy"]
VM_NAME = "strauss-drive-server"
compute = oci.core.ComputeClient(CFG)
network = oci.core.VirtualNetworkClient(CFG)


def find_vm():
    for c in [TENANCY] + [x.id for x in oci.pagination.list_call_get_all_results(
            oci.identity.IdentityClient(CFG).list_compartments, TENANCY, compartment_id_in_subtree=True).data]:
        for i in compute.list_instances(c).data:
            if i.display_name == VM_NAME and i.lifecycle_state != "TERMINATED":
                return i
    raise SystemExit(f"no instance named {VM_NAME}")


def status():
    vm = find_vm()
    print(f"vm: {vm.display_name} {vm.lifecycle_state} shape={vm.shape} "
          f"ocpu={vm.shape_config.ocpus} mem={vm.shape_config.memory_in_gbs}GB region={vm.region}")
    plugins = {p.name: p.desired_state for p in (vm.agent_config.plugins_config or [])}
    print("agent: management_disabled=", vm.agent_config.is_management_disabled,
          "| Run Command plugin desired:", plugins.get("Compute Instance Run Command"))
    try:
        ag = oci.compute_instance_agent.PluginClient(CFG)
        live = {p.name: p.status for p in ag.list_instance_agent_plugins(vm.compartment_id, vm.id).data}
        print("agent plugins running:", {k: v for k, v in live.items() if "Run Command" in k or "Monitoring" in k})
    except oci.exceptions.ServiceError as e:
        print("agent plugin status: unavailable", e.status)
    for va in compute.list_vnic_attachments(vm.compartment_id, instance_id=vm.id).data:
        vnic = network.get_vnic(va.vnic_id).data
        subnet = network.get_subnet(vnic.subnet_id).data
        print("public ip:", vnic.public_ip, "| subnet:", subnet.display_name)
        for sl_id in subnet.security_list_ids:
            sl = network.get_security_list(sl_id).data
            ports = sorted({(r.tcp_options.destination_port_range.min if r.tcp_options and r.tcp_options.destination_port_range else "all")
                            for r in sl.ingress_security_rules if r.protocol in ("6", "all")}, key=str)
            print(f"security list {sl.display_name}: tcp ingress ports {ports}")
    return vm


def run(script_path, timeout=900):
    vm = find_vm()
    agent = oci.compute_instance_agent.ComputeInstanceAgentClient(CFG)
    script = open(script_path, encoding="utf-8").read()
    details = oci.compute_instance_agent.models.CreateInstanceAgentCommandDetails(
        compartment_id=vm.compartment_id, execution_time_out_in_seconds=int(timeout), display_name="strauss-deploy",
        target=oci.compute_instance_agent.models.InstanceAgentCommandTarget(instance_id=vm.id),
        content=oci.compute_instance_agent.models.InstanceAgentCommandContent(
            source=oci.compute_instance_agent.models.InstanceAgentCommandSourceViaTextDetails(source_type="TEXT", text=script),
            output=oci.compute_instance_agent.models.InstanceAgentCommandOutputViaTextDetails(output_type="TEXT")))
    cmd = agent.create_instance_agent_command(details).data
    deadline = time.time() + int(timeout) + 120
    while time.time() < deadline:
        ex = agent.get_instance_agent_command_execution(cmd.id, vm.id).data
        if ex.lifecycle_state in ("SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELED"):
            print(f"[run command: {ex.lifecycle_state}, exit {ex.content.exit_code}]")
            print(ex.content.text or "")
            return ex.content.exit_code
        time.sleep(5)
    print("[run command: no result before the deadline]")
    return 1


if __name__ == "__main__":
    if sys.argv[1] == "status":
        status()
    elif sys.argv[1] == "run":
        sys.exit(run(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 900) or 0)
