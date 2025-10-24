import discord
from discord.ext import commands, tasks
from discord import app_commands, Interaction
import json
import os
import docker
import asyncio

# -------------------- CONFIG --------------------
TOKEN = ""
ADMIN_IDS = [1405866008127864852]  # Admin Discord IDs
DATA_FILE = "vps_data.json"
CREDITS_FILE = "credits.json"

# VPS Plans
VPS_PLANS = {
    "Starter": {"ram": 4, "cpu": 1, "disk": 10, "intel": 42, "amd": 83},
    "Basic": {"ram": 8, "cpu": 1, "disk": 10, "intel": 96, "amd": 164},
    "Standard": {"ram": 12, "cpu": 2, "disk": 10, "intel": 192, "amd": 320},
    "Pro": {"ram": 16, "cpu": 2, "disk": 20, "intel": 340, "amd": 340},
}

# ------------------ INIT -----------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
client_docker = docker.from_env()

# ------------------ UTILITIES ------------------
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_credits():
    if os.path.exists(CREDITS_FILE):
        with open(CREDITS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_credits(data):
    with open(CREDITS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def is_admin(user_id):
    return user_id in ADMIN_IDS

# ------------------ COMMANDS -------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)

# ---------------- CREATE VPS -------------------
@bot.tree.command(name="createvps", description="Create a VPS")
@app_commands.describe(name="VPS Name", ram="RAM in GB", cpu="CPU cores", disk="Disk in GB", user="User Discord ID")
async def createvps(interaction: Interaction, name: str, ram: int, cpu: int, disk: int, user: str):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("Only admins can create VPS.", ephemeral=True)
        return

    data = load_data()
    vps_id = str(len(data) + 1)

    # Create Docker container
    container_name = f"vps-{vps_id}"
    try:
        container = client_docker.containers.run(
            "ubuntu:22.04",
            name=container_name,
            detach=True,
            tty=True,
            stdin_open=True,
            mem_limit=f"{ram}g",
            cpus=cpu
        )
        # Install SSH
        container.exec_run("apt update && apt install -y openssh-server sudo")
        container.exec_run("mkdir -p /var/run/sshd")
        container.exec_run("echo 'root:root' | chpasswd")
        container.exec_run("sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config")
        container.exec_run("service ssh start")
    except docker.errors.APIError as e:
        await interaction.response.send_message(f"Error creating container: {e}", ephemeral=True)
        return

    # Save VPS metadata
    data[vps_id] = {
        "name": name,
        "user": user,
        "container": container_name,
        "ram": ram,
        "cpu": cpu,
        "disk": disk,
        "status": "running",
        "shared_with": []
    }
    save_data(data)

    # DM user
    user_obj = await bot.fetch_user(int(user))
    try:
        await user_obj.send(f"Your VPS `{name}` has been created!\nVPS ID: {vps_id}\nSSH: root@YOUR_SERVER_IP\nPassword: root\nRAM: {ram}GB | CPU: {cpu} cores | Disk: {disk}GB")
    except:
        pass

    await interaction.response.send_message(f"VPS `{name}` created and DM sent to user.", ephemeral=True)

# ---------------- DELETE VPS -------------------
@bot.tree.command(name="deletevps", description="Delete a VPS")
@app_commands.describe(vpsid="VPS ID")
async def deletevps(interaction: Interaction, vpsid: str):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("Only admins can delete VPS.", ephemeral=True)
        return

    data = load_data()
    if vpsid not in data:
        await interaction.response.send_message("VPS ID not found.", ephemeral=True)
        return

    container_name = data[vpsid]["container"]
    try:
        container = client_docker.containers.get(container_name)
        container.stop()
        container.remove()
    except docker.errors.NotFound:
        pass

    del data[vpsid]
    save_data(data)
    await interaction.response.send_message(f"VPS `{vpsid}` deleted.", ephemeral=True)

# ---------------- MANAGE VPS -------------------
@bot.tree.command(name="managevps", description="Manage your VPS")
@app_commands.describe(vpsid="VPS ID", action="Action to perform: start, stop, restart, info")
async def managevps(interaction: Interaction, vpsid: str, action: str):
    data = load_data()
    if vpsid not in data:
        await interaction.response.send_message("VPS ID not found.", ephemeral=True)
        return

    vps = data[vpsid]
    user_id = vps["user"]
    if str(interaction.user.id) != str(user_id) and not is_admin(interaction.user.id) and str(interaction.user.id) not in vps["shared_with"]:
        await interaction.response.send_message("You don't have access to this VPS.", ephemeral=True)
        return

    container_name = vps["container"]
    try:
        container = client_docker.containers.get(container_name)
    except docker.errors.NotFound:
        await interaction.response.send_message("Container not found.", ephemeral=True)
        return

    if action.lower() == "start":
        container.start()
        vps["status"] = "running"
        save_data(data)
        await interaction.response.send_message(f"VPS `{vpsid}` started.", ephemeral=True)
    elif action.lower() == "stop":
        container.stop()
        vps["status"] = "stopped"
        save_data(data)
        await interaction.response.send_message(f"VPS `{vpsid}` stopped.", ephemeral=True)
    elif action.lower() == "restart":
        container.restart()
        vps["status"] = "running"
        save_data(data)
        await interaction.response.send_message(f"VPS `{vpsid}` restarted.", ephemeral=True)
    elif action.lower() == "info":
        info_msg = f"Name: {vps['name']}\nVPS ID: {vpsid}\nStatus: {vps['status']}\nRAM: {vps['ram']}GB\nCPU: {vps['cpu']}\nDisk: {vps['disk']}GB\nShared With: {vps['shared_with']}"
        await interaction.response.send_message(info_msg, ephemeral=True)
    else:
        await interaction.response.send_message("Invalid action. Use start, stop, restart, info.", ephemeral=True)

# ---------------- SHARE VPS -------------------
@bot.tree.command(name="sharevps", description="Share VPS with another user")
@app_commands.describe(vpsid="VPS ID", userid="User ID to share", action="add or remove")
async def sharevps(interaction: Interaction, vpsid: str, userid: str, action: str):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("Only admins can share VPS.", ephemeral=True)
        return

    data = load_data()
    if vpsid not in data:
        await interaction.response.send_message("VPS ID not found.", ephemeral=True)
        return

    if action.lower() == "add":
        if userid not in data[vpsid]["shared_with"]:
            data[vpsid]["shared_with"].append(userid)
        save_data(data)
        await interaction.response.send_message(f"User {userid} added to shared VPS {vpsid}.", ephemeral=True)
    elif action.lower() == "remove":
        if userid in data[vpsid]["shared_with"]:
            data[vpsid]["shared_with"].remove(userid)
        save_data(data)
        await interaction.response.send_message(f"User {userid} removed from shared VPS {vpsid}.", ephemeral=True)
    else:
        await interaction.response.send_message("Invalid action. Use add or remove.", ephemeral=True)

# ---------------- VPS PLANS -------------------
@bot.tree.command(name="plans", description="Show VPS plans")
async def plans(interaction: Interaction):
    embed = discord.Embed(title="VPS Plans", color=discord.Color.blue())
    for plan, details in VPS_PLANS.items():
        embed.add_field(name=plan, value=f"RAM: {details['ram']}GB | CPU: {details['cpu']} | Disk: {details['disk']}GB | Intel: {details['intel']} credits | AMD: {details['amd']} credits", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- BOT INFO -------------------
@bot.tree.command(name="botinfo", description="Show bot info")
async def botinfo(interaction: Interaction):
    embed = discord.Embed(title="DragonCloud Bot", description="Made by PowerDev", color=discord.Color.green())
    embed.add_field(name="Features", value="VPS Management | Docker-based | Plans & Credits | DM Notifications | Admin Controls", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ------------------ RUN BOT -------------------
bot.run(TOKEN)
