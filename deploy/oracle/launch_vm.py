"""Launches a second Always Free test VM (A1.Flex 1 OCPU / 6 GB, same image and subnet as strauss-drive-server)
whose cloud-init installs Docker, downloads the code bundle (no secrets) from the private bucket, writes the
vm-test settings (mode 600), starts the stack, runs the system test and uploads its result to the bucket.
Secrets are read from the DPAPI store and only placed in the instance's user_data; nothing is printed."""
import base64
import json
import os
import subprocess

import oci

from oci_ops import CFG, compute, network, find_vm

NAME = os.environ.get("VM_NAME", "strauss-drive-test2")
DB_SECRET = os.environ.get("DB_SECRET", "neon-vm-test-url")
STATE = os.path.expanduser(os.environ.get("VM_STATE", "~/strauss-edge-server/.test2.json"))


def secret(name: str) -> str:
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                          f". C:/Users/user/strauss-secrets/secrets.ps1; Get-StraussSecretValue '{name}'"],
                         capture_output=True, text=True, check=True).stdout
    return "".join(ch for ch in out if ch.isprintable()).strip()


env = "\n".join([
    f"DATABASE_URL={secret(DB_SECRET)}",
    f"EDGE_PROXY_SECRET={secret('edge-proxy-secret')}",
    f"REP_ACCESS_TOKEN={secret('edge-rep-access-token')}",
    f"EMAIL_ENCRYPTION_KEY={secret('edge-email-encryption-key')}",
    f"GEMINI_API_KEY={secret('gemini-api-key')}",
    "CONFIG_MODE=strict", "LLM_MODE=llm_first", "LLM_CHAIN=gemini:gemini-3.5-flash-lite,gemini:gemini-3.6-flash",
    "PAYSLIP_ENABLED=true", "LOG_LEVEL=INFO"]
    # mail (EMAIL_MODE=smtp): the existing outbox and verification flow; links lead to the public Cloudflare link
    + ([f"EMAIL_BACKEND=smtp", f"EMAIL_SMTP_HOST={secret('smtp-host')}", f"EMAIL_SMTP_PORT={secret('smtp-port')}",
        f"EMAIL_SMTP_USERNAME={secret('smtp-username')}", f"EMAIL_SMTP_PASSWORD={secret('smtp-password')}",
        f"EMAIL_FROM={secret('smtp-from')}", "EMAIL_SMTP_STARTTLS=true", "EMAIL_FAKE_NOTICE=false",
        "EMAIL_PUBLIC_APP_URL=https://strauss-drive.strauss-drive-edge.workers.dev"]
       if os.environ.get("EMAIL_MODE") == "smtp" else ["EMAIL_BACKEND=disabled"]) + [""])
code_url = open(os.path.expanduser("~/strauss-edge-server/.par-code")).read().strip()
results_url = open(os.path.expanduser("~/strauss-edge-server/.par-results")).read().strip()

duck = secret("duckdns-token")
indent = lambda text: "\n".join("      " + line for line in text.splitlines())
user_data = f"""#cloud-config
package_update: true
packages: [docker.io, docker-compose-v2, unattended-upgrades, fail2ban]
write_files:
  - path: /etc/apt/apt.conf.d/52strauss-auto-reboot
    content: |
      Unattended-Upgrade::Automatic-Reboot "true";
      Unattended-Upgrade::Automatic-Reboot-Time "04:30";
  - path: /etc/ssh/sshd_config.d/90-strauss.conf
    content: |
      PasswordAuthentication no
      KbdInteractiveAuthentication no
      PermitRootLogin no
  - path: /etc/docker/daemon.json
    content: |
      {{ "log-driver": "json-file", "log-opts": {{ "max-size": "10m", "max-file": "3" }} }}
  - path: /etc/cron.d/strauss-heal
    content: |
      # an app marked unhealthy by its Docker healthcheck is restarted (Docker itself restarts a crashed one)
      */5 * * * * root cd /srv/strauss && docker compose ps app 2>/dev/null | grep -q '(unhealthy)' && docker compose restart app >/dev/null 2>&1
  - path: /srv/strauss/secrets/duckdns-token
    permissions: '0600'
    content: {duck}
  - path: /etc/cron.d/strauss-duckdns
    content: |
      # keep strauss-drive.duckdns.org on this VM (token read from a file, never on a command line)
      @reboot root sleep 30; curl -sf -m 30 --get --data-urlencode "token@/srv/strauss/secrets/duckdns-token" -d domains=strauss-drive -d ip= https://www.duckdns.org/update >/dev/null
  - path: /srv/strauss/secrets/app.env
    permissions: '0600'
    content: |
{indent(env)}
runcmd:
  - systemctl enable --now docker fail2ban unattended-upgrades
  - usermod -aG docker ubuntu
  - iptables -I INPUT 5 -m state --state NEW -p tcp --dport 80 -j ACCEPT
  - iptables -I INPUT 5 -m state --state NEW -p tcp --dport 443 -j ACCEPT
  - netfilter-persistent save
  - chmod 700 /srv/strauss/secrets
  - [bash, -c, 'curl -sf -m 30 --get --data-urlencode "token@/srv/strauss/secrets/duckdns-token" -d domains=strauss-drive -d ip= https://www.duckdns.org/update; sleep 20']
  - [bash, -c, 'set -o pipefail; curl -sf -m 600 "{code_url}" | tar -xz -C /srv && mkdir -p /srv/strauss/src && tar -x -C /srv/strauss/src -f /srv/strauss/src.tar && rm -f /srv/strauss/src.tar']
  - [bash, -c, 'bash /srv/strauss/vm-setup-and-test.sh > /var/log/strauss-test.log 2>&1; curl -sf -m 60 -X PUT --data-binary @/var/log/strauss-test.log "{results_url}"']
"""

ref = find_vm()
va = compute.list_vnic_attachments(ref.compartment_id, instance_id=ref.id).data[0]
subnet_id = network.get_vnic(va.vnic_id).data.subnet_id
pub = open(os.path.expanduser("~/strauss-secrets/ssh/oracle_ed25519.pub")).read().strip()
existing = [i for i in compute.list_instances(ref.compartment_id).data
            if i.display_name == NAME and i.lifecycle_state not in ("TERMINATED", "TERMINATING")]
if existing:
    print("already exists:", NAME, existing[0].lifecycle_state)
    raise SystemExit(0)
inst = compute.launch_instance(oci.core.models.LaunchInstanceDetails(
    compartment_id=ref.compartment_id, availability_domain=ref.availability_domain, display_name=NAME,
    shape="VM.Standard.A1.Flex", shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=1, memory_in_gbs=6),
    source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=ref.source_details.image_id),
    create_vnic_details=oci.core.models.CreateVnicDetails(subnet_id=subnet_id, assign_public_ip=True),
    metadata={"ssh_authorized_keys": pub, "user_data": base64.b64encode(user_data.encode()).decode()},
    agent_config=oci.core.models.LaunchInstanceAgentConfigDetails(is_monitoring_disabled=False, is_management_disabled=False),
)).data
json.dump({"id": inst.id, "name": NAME}, open(STATE, "w"))
print("launched:", NAME, inst.lifecycle_state, "| shape A1.Flex 1 OCPU / 6 GB (Always Free)")
