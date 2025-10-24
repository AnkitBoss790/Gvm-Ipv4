# File: ipv4_vps_bot_admin.py
"""
IPv4 Docker VPS Bot — Final (with dynamic Admin system & jail)
Author: PowerDev
Version: 2.1

Save as: ipv4_vps_bot_admin.py
DISCORD_TOKEN=your_discord_token

Notes:
- Edit CONFIG below if you need to change networks / options.
- This script maintains two JSON files:
    - vps_db.json  (stores VPS metadata)
    - config.json  (stores admin_ids list and admin-only flag)
"""

import os
import json
import asyncio
import secrets
import string
import tempfile
import shutil
from pathlib import Path
from typing import Optional, List

import docker
from docker.types import IPAMConfig, IPAMPool
import discord
from discord.ext import commands
from discord import ui
from dotenv import load_dotenv

# ---------------- CONFIG (EDIT BEFORE RUNNING) ----------------
PARENT_INTERFACE = "eth0"               # host NIC used for macvlan
MACVLAN_NETWORK_NAME = "pub_macvlan_net"
MACVLAN_SUBNET = "45.45.45.0/24"        # replace with your routed subnet
MACVLAN_GATEWAY = "45.45.45.1"
IP_POOL_START = 10                      # .10 .. .END
IP_POOL_END = 50
BASE_IMAGE_TAG = "ipv4_vps_base:22.04"  # built if missing (Ubuntu+openssh)
DEFAULT_IMAGE = BASE_IMAGE_TAG
VPS_DB_PATH = Path("vps_db.json")
CONFIG_PATH = Path("config.json")
DEFAULT_ROOT_PASSWORD_LENGTH = 12
COMMAND_PREFIX = "!"
BOT_AUTHOR = "PowerDev"
BOT_VERSION = "2.1"
# If True, creation & deletion & listall are restricted to admins (dynamic admin list).
ADMIN_ONLY_CREATE_DELETE = True
# ----------------------------------------------------------------

DISCORD_TOKEN = ""
# Docker client
docker_client = docker.from_env()

# Ensure DB + config exist
if not VPS_DB_PATH.exists():
    VPS_DB_PATH.write_text(json.dumps({"vps": []}, indent=2))

if not CONFIG_PATH.exists():
    default_cfg = {
        "admin_ids": [],            # add Discord user IDs here or use !addadmin to add dynamically
        "admin_only_create_delete": ADMIN_ONLY_CREATE_DELETE
    }
    CONFIG_PATH.write_text(json.dumps(default_cfg, indent=2))

# ---------------- Helpers ----------------
def load_db() -> dict:
    return json.loads(VPS_DB_PATH.read_text())

def save_db(data: dict):
    VPS_DB_PATH.write_text(json.dumps(data, indent=2))

def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())

def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

def gen_password(length=DEFAULT_ROOT_PASSWORD_LENGTH) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def find_free_ip() -> Optional[str]:
    db = load_db()
    used_ips = {v['ip'] for v in db['vps']}
    try:
        net = docker_client.networks.get(MACVLAN_NETWORK_NAME)
        for c_id, attrs in (net.attrs.get('Containers') or {}).items():
            ip = attrs.get('IPv4Address', '').split('/')[0]
            if ip:
                used_ips.add(ip)
    except docker.errors.NotFound:
        pass
    base = MACVLAN_SUBNET.split('/')[0].rsplit('.', 1)[0]
    for i in range(IP_POOL_START, IP_POOL_END + 1):
        candidate = f"{base}.{i}"
        if candidate not in used_ips and candidate != MACVLAN_GATEWAY:
            return candidate
    return None

def ensure_macvlan_sync(parent_iface=PARENT_INTERFACE):
    """Create macvlan network synchronously if missing."""
    try:
        docker_client.networks.get(MACVLAN_NETWORK_NAME)
        return
    except docker.errors.NotFound:
        pass
    ipam_pool = IPAMPool(subnet=MACVLAN_SUBNET, gateway=MACVLAN_GATEWAY)
    ipam_conf = IPAMConfig(pool_configs=[ipam_pool])
    docker_client.networks.create(
        name=MACVLAN_NETWORK_NAME,
        driver='macvlan',
        options={"parent": parent_iface},
        ipam=ipam_conf,
        check_duplicate=True,
    )

def build_base_image_sync():
    """Build a small ubuntu:22.04 image with sshd if BASE_IMAGE_TAG does not exist."""
    print("[+] Building base image:", BASE_IMAGE_TAG)
    tmpdir = tempfile.mkdtemp(prefix="ipv4_vps_build_")
    dockerfile = f"""
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y && apt-get install -y openssh-server passwd ca-certificates && \\
    mkdir -p /var/run/sshd && \\
    sed -i 's/^#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config || true && \\
    sed -i 's/^#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config || true
EXPOSE 22
CMD ["/usr/sbin/sshd","-D"]
"""
    df_path = Path(tmpdir) / "Dockerfile"
    df_path.write_text(dockerfile)
    try:
        image, logs = docker_client.images.build(path=tmpdir, tag=BASE_IMAGE_TAG)
        print("[+] Base image built.")
    except Exception as e:
        print("[!] Failed building base image:", e)
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def create_container_sync(name: str, ip: str, root_password: str, image: str, jail: bool=True) -> str:
    """Create container and apply 'jail' security options. Returns container id."""
    ensure_macvlan_sync()
    # Ensure image exists
    try:
        docker_client.images.get(image)
    except docker.errors.ImageNotFound:
        if image == BASE_IMAGE_TAG:
            build_base_image_sync()
        else:
            docker_client.images.pull(image)

    container_name = f"vps_{name}_{secrets.token_hex(4)}"
    kwargs = {}
    if jail:
        kwargs.update({
            "read_only": True,
            "cap_drop": ['ALL'],
            "security_opt": ['no-new-privileges'],
            "tmpfs": {'/tmp': ''},
        })

    container = docker_client.containers.run(
        image,
        command="/usr/sbin/sshd -D",
        detach=True,
        name=container_name,
        tty=True,
        network=MACVLAN_NETWORK_NAME,
        ipv4_address=ip,
        hostname=container_name,
        **kwargs
    )

    # set root password
    try:
        container.exec_run(f"bash -lc \"echo 'root:{root_password}' | chpasswd\"", user='root')
        container.exec_run("service ssh restart || service sshd restart || true", user='root')
    except Exception as e:
        print("[!] Warning: failed to set root password:", e)

    return container.id

# ---------------- Discord bot ----------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# Helper: check dynamic admin
def is_dynamic_admin(user: discord.User) -> bool:
    cfg = load_config()
    admin_ids = cfg.get("admin_ids", [])
    if user.id in admin_ids:
        return True
    return False

def admin_allowed(member: discord.Member) -> bool:
    # Allowed if user has Discord admin perms OR present in dynamic admin list
    return member.guild_permissions.administrator or is_dynamic_admin(member)

# UI for management
class VPSManageView(ui.View):
    def __init__(self, vps_entry: dict, timeout: int = 600):
        super().__init__(timeout=timeout)
        self.vps_entry = vps_entry

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        allowed = (interaction.user.id == self.vps_entry['owner'] or interaction.user.id in self.vps_entry.get('shared_with', []) or admin_allowed(interaction.user))
        if not allowed:
            await interaction.response.send_message("You don't have permission to use these buttons.", ephemeral=True)
            return False
        return True

    @ui.button(label="Start", style=discord.ButtonStyle.success, custom_id="vps_start")
    async def start_button(self, button: ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            cont = docker_client.containers.get(self.vps_entry['id'])
            cont.start()
            await interaction.followup.send(f"Started {self.vps_entry['name']} ({self.vps_entry['ip']}).")
        except docker.errors.NotFound:
            await interaction.followup.send("Container not found.")
        except Exception as e:
            await interaction.followup.send(f"Failed to start: {e}")

    @ui.button(label="Stop", style=discord.ButtonStyle.danger, custom_id="vps_stop")
    async def stop_button(self, button: ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            cont = docker_client.containers.get(self.vps_entry['id'])
            cont.stop()
            await interaction.followup.send(f"Stopped {self.vps_entry['name']}.")
        except docker.errors.NotFound:
            await interaction.followup.send("Container not found.")
        except Exception as e:
            await interaction.followup.send(f"Failed to stop: {e}")

    @ui.button(label="Restart", style=discord.ButtonStyle.primary, custom_id="vps_restart")
    async def restart_button(self, button: ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            cont = docker_client.containers.get(self.vps_entry['id'])
            cont.restart()
            await interaction.followup.send(f"Restarted {self.vps_entry['name']}.")
        except docker.errors.NotFound:
            await interaction.followup.send("Container not found.")
        except Exception as e:
            await interaction.followup.send(f"Failed to restart: {e}")

    @ui.button(label="SSH Info", style=discord.ButtonStyle.secondary, custom_id="vps_sshinfo")
    async def sshinfo_button(self, button: ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        info = f"IP: `{self.vps_entry['ip']}`\nUser: `root`\nPassword: `{self.vps_entry.get('root_pass')}`\nSSH: `ssh root@{self.vps_entry['ip']}`"
        await interaction.followup.send(info, ephemeral=True)

# Bot events
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")
    # ensure base image exists (build lazily)
    try:
        docker_client.images.get(BASE_IMAGE_TAG)
    except docker.errors.ImageNotFound:
        print("[+] Base image not found. Building...")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, build_base_image_sync)
            print("[+] Base image built.")
        except Exception as e:
            print("[!] Error building base image:", e)
    print("Bot ready. Prefix commands:", COMMAND_PREFIX)

# ---------------- Commands (prefix) ----------------

@bot.command(name="botinfo")
async def cmd_botinfo(ctx: commands.Context):
    cfg = load_config()
    admins = cfg.get("admin_ids", [])
    embed = discord.Embed(title="IPv4 Docker VPS Bot", description=f"Made by {BOT_AUTHOR}", color=0x2F3136)
    embed.add_field(name="Version", value=BOT_VERSION, inline=True)
    embed.add_field(name="Admin-only create/delete", value=str(cfg.get("admin_only_create_delete", ADMIN_ONLY_CREATE_DELETE)), inline=True)
    embed.add_field(name="Admins", value=", ".join([f"<@{a}>" for a in admins]) or "(none)", inline=False)
    embed.add_field(name="Commands", value="!createvps !deletevps !listvps !listall !manage !sharevps !sendvps !addadmin !removeadmin !adminlist", inline=False)
    await ctx.send(embed=embed)

# createvps (admin-only if configured)
@bot.command(name="createvps")
async def cmd_createvps(ctx: commands.Context, name: str, image: Optional[str] = None):
    cfg = load_config()
    if cfg.get("admin_only_create_delete", ADMIN_ONLY_CREATE_DELETE) and not admin_allowed(ctx.author):
        await ctx.send("Only admins can create VPS (admin-only enabled).")
        return
    if ' ' in name:
        await ctx.send("VPS name cannot contain spaces.")
        return
    image = image or DEFAULT_IMAGE
    await ctx.send(f"⏳ Provisioning VPS `{name}` — assigning IPv4 and creating container. Please wait...")

    ip = find_free_ip()
    if ip is None:
        await ctx.send("No free IPs available in pool.")
        return
    root_pass = gen_password()
    loop = asyncio.get_running_loop()
    try:
        container_id = await loop.run_in_executor(None, lambda: create_container_sync(name, ip, root_pass, image, jail=True))
    except Exception as e:
        await ctx.send(f"Failed to create container: {e}")
        return

    db = load_db()
    entry = {
        "id": container_id,
        "name": name,
        "owner": ctx.author.id,
        "ip": ip,
        "root_pass": root_pass,
        "shared_with": []
    }
    db['vps'].append(entry)
    save_db(db)

    embed = discord.Embed(title=f"VPS Created — {name}", description=f"IP: `{ip}`", color=0x2ECC71)
    embed.add_field(name="Container ID", value=container_id, inline=False)
    embed.set_footer(text=f"Owned by {ctx.author} • Made by {BOT_AUTHOR}")
    view = VPSManageView(entry)
    await ctx.send(embed=embed, view=view)
    try:
        await ctx.author.send(f"✅ VPS created.\nName: {name}\nIP: {ip}\nSSH: ssh root@{ip}\nPassword: {root_pass}\nContainer ID: {container_id}")
    except Exception:
        pass

@bot.command(name="listvps")
async def cmd_listvps(ctx: commands.Context):
    db = load_db()
    owned = [v for v in db['vps'] if v['owner'] == ctx.author.id or ctx.author.id in v.get('shared_with', []) or admin_allowed(ctx.author)]
    if not owned:
        await ctx.send("You don't own or have access to any VPS from this bot.")
        return
    for v in owned:
        embed = discord.Embed(title=f"{v['name']} ({v['ip']})", color=0x3498DB)
        embed.add_field(name="Container ID", value=v['id'], inline=False)
        embed.add_field(name="Owner", value=f"<@{v['owner']}>", inline=True)
        embed.add_field(name="Shared with", value=", ".join([f"<@{u}>" for u in v.get('shared_with', [])]) or "None", inline=True)
        view = VPSManageView(v)
        await ctx.send(embed=embed, view=view)

@bot.command(name="listall")
async def cmd_listall(ctx: commands.Context):
    cfg = load_config()
    if cfg.get("admin_only_create_delete", ADMIN_ONLY_CREATE_DELETE) and not admin_allowed(ctx.author):
        await ctx.send("Only admins can list all VPS.")
        return
    db = load_db()
    if not db['vps']:
        await ctx.send("(no VPS created yet)")
        return
    all_text = json.dumps(db['vps'], indent=2)
    if len(all_text) > 1900:
        p = Path("vps_all.json")
        p.write_text(all_text)
        await ctx.send(file=discord.File(str(p)))
        p.unlink(missing_ok=True)
    else:
        await ctx.send(f"```\n{all_text}\n```")

@bot.command(name="deletevps")
async def cmd_deletevps(ctx: commands.Context, vps_id: str):
    cfg = load_config()
    if cfg.get("admin_only_create_delete", ADMIN_ONLY_CREATE_DELETE) and not admin_allowed(ctx.author):
        await ctx.send("Only admins can delete VPS (admin-only enabled).")
        return
    db = load_db()
    target = None
    for v in db['vps']:
        if v['id'].startswith(vps_id) or v['id'] == vps_id:
            target = v
            break
    if not target:
        await ctx.send("VPS not found.")
        return
    allowed = (ctx.author.guild_permissions.administrator or ctx.author.id == target['owner'] or admin_allowed(ctx.author))
    if not allowed:
        await ctx.send("You don't have permission to delete this VPS.")
        return
    try:
        cont = docker_client.containers.get(target['id'])
        cont.stop(timeout=5)
        cont.remove()
    except docker.errors.NotFound:
        pass
    except Exception as e:
        await ctx.send(f"Failed to remove container: {e}")
        return
    db['vps'] = [v for v in db['vps'] if v['id'] != target['id']]
    save_db(db)
    await ctx.send(f"✅ VPS `{target['name']}` deleted.")

@bot.command(name="manage")
async def cmd_manage(ctx: commands.Context, vps_id: str, action: str, *, exec_command: Optional[str] = None):
    db = load_db()
    target = None
    for v in db['vps']:
        if v['id'].startswith(vps_id) or v['id'] == vps_id:
            target = v
            break
    if not target:
        await ctx.send("VPS not found.")
        return
    allowed = (ctx.author.id == target['owner'] or ctx.author.guild_permissions.administrator or ctx.author.id in target.get('shared_with', []) or admin_allowed(ctx.author))
    if not allowed:
        await ctx.send("You don't have permission to manage this VPS.")
        return
    try:
        container = docker_client.containers.get(target['id'])
    except docker.errors.NotFound:
        await ctx.send("Container not found on host.")
        return
    action = action.lower()
    try:
        if action == "start":
            container.start()
            await ctx.send("Started.")
        elif action == "stop":
            container.stop()
            await ctx.send("Stopped.")
        elif action == "restart":
            container.restart()
            await ctx.send("Restarted.")
        elif action == "info":
            await ctx.send(f"Name: {target['name']}\nIP: {target['ip']}\nStatus: {container.status}\nOwner: <@{target['owner']}>")
        elif action == "exec":
            if not exec_command:
                await ctx.send("Provide a command to exec.")
                return
            rc, out = container.exec_run(exec_command, user='root', demux=True)
            if isinstance(out, tuple):
                out_text = (out[0] or b'').decode('utf-8', errors='ignore') + (out[1] or b'').decode('utf-8', errors='ignore')
            else:
                out_text = (out or b'').decode('utf-8', errors='ignore')
            if len(out_text) > 1900:
                out_text = out_text[:1900] + "\n... (truncated)"
            await ctx.send(f"Exec (rc={rc}):```\n{out_text}\n```")
        else:
            await ctx.send("Unknown action. Use start|stop|restart|info|exec.")
    except Exception as e:
        await ctx.send(f"Action failed: {e}")

@bot.command(name="sharevps")
async def cmd_sharevps(ctx: commands.Context, vps_id: str, op: str, user_id: int):
    db = load_db()
    target = None
    for v in db['vps']:
        if v['id'].startswith(vps_id) or v['id'] == vps_id:
            target = v
            break
    if not target:
        await ctx.send("VPS not found.")
        return
    if ctx.author.id != target['owner'] and not admin_allowed(ctx.author):
        await ctx.send("Only the owner or an admin can change sharing.")
        return
    op = op.lower()
    shared: List[int] = target.get('shared_with', [])
    if op == "add":
        if user_id in shared:
            await ctx.send("User already has access.")
            return
        shared.append(user_id)
        target['shared_with'] = shared
        save_db(db)
        await ctx.send(f"Added <@{user_id}> to shared access.")
    elif op == "remove":
        if user_id not in shared:
            await ctx.send("User does not have shared access.")
            return
        shared.remove(user_id)
        target['shared_with'] = shared
        save_db(db)
        await ctx.send(f"Removed <@{user_id}> from shared access.")
    else:
        await ctx.send("Invalid op. Use add or remove.")

@bot.command(name="sendvps")
async def cmd_sendvps(ctx: commands.Context, vps_id: str, new_owner_id: int):
    db = load_db()
    target = None
    for v in db['vps']:
        if v['id'].startswith(vps_id) or v['id'] == vps_id:
            target = v
            break
    if not target:
        await ctx.send("VPS not found.")
        return
    if ctx.author.id != target['owner'] and not admin_allowed(ctx.author):
        await ctx.send("Only the owner or an admin can transfer ownership.")
        return
    old = target['owner']
    target['owner'] = new_owner_id
    if new_owner_id in target.get('shared_with', []):
        target['shared_with'].remove(new_owner_id)
    save_db(db)
    await ctx.send(f"Transferred ownership from <@{old}> to <@{new_owner_id}>.")
    try:
        user = await bot.fetch_user(new_owner_id)
        await user.send(f"You are now the owner of VPS '{target['name']}' (IP: {target['ip']}).")
    except Exception:
        pass

# ---------------- Admin management (dynamic) ----------------
@bot.command(name="addadmin")
async def cmd_addadmin(ctx: commands.Context, user_id: int):
    # only existing admins or discord server admins can add
    if not admin_allowed(ctx.author):
        await ctx.send("Only existing admins may add new admins.")
        return
    cfg = load_config()
    admin_ids = cfg.get("admin_ids", [])
    if user_id in admin_ids:
        await ctx.send("That user is already an admin (dynamic list).")
        return
    admin_ids.append(user_id)
    cfg["admin_ids"] = admin_ids
    save_config(cfg)
    await ctx.send(f"Added <@{user_id}> as admin.")

@bot.command(name="removeadmin")
async def cmd_removeadmin(ctx: commands.Context, user_id: int):
    if not admin_allowed(ctx.author):
        await ctx.send("Only existing admins may remove admins.")
        return
    cfg = load_config()
    admin_ids = cfg.get("admin_ids", [])
    if user_id not in admin_ids:
        await ctx.send("That user is not in the admin list.")
        return
    admin_ids.remove(user_id)
    cfg["admin_ids"] = admin_ids
    save_config(cfg)
    await ctx.send(f"Removed <@{user_id}> from admin list.")

@bot.command(name="adminlist")
async def cmd_adminlist(ctx: commands.Context):
    cfg = load_config()
    admin_ids = cfg.get("admin_ids", [])
    if not admin_allowed(ctx.author):
        await ctx.send("Only admins can view the admin list.")
        return
    if not admin_ids:
        await ctx.send("Admin list is empty.")
        return
    await ctx.send("Admins: " + ", ".join([f"<@{a}>" for a in admin_ids]))

# Run bot
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
