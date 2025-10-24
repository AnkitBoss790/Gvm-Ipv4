import discord
from discord.ext import commands
from discord import app_commands, Interaction
import json, os, docker, random, asyncio

# ---------------- CONFIG ----------------
TOKEN = "YOUR_BOT_TOKEN"
ADMIN_IDS = [1405866008127864852]  # Admin Discord IDs
DATA_FILE = "vps_data.json"
CREDITS_FILE = "credits.json"

VPS_PLANS = {
    "Starter": {"ram": 4, "cpu": 1, "disk": 10, "intel": 42, "amd": 83},
    "Basic": {"ram": 8, "cpu": 1, "disk": 10, "intel": 96, "amd": 164},
    "Standard": {"ram": 12, "cpu": 2, "disk": 10, "intel": 192, "amd": 320},
    "Pro": {"ram": 16, "cpu": 2, "disk": 20, "intel": 220, "amd": 340},
}

# ---------------- INIT ----------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
client_docker = docker.from_env()

# ---------------- UTIL ----------------
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

def save_credits(credits):
    with open(CREDITS_FILE, "w") as f:
        json.dump(credits, f, indent=4)

def is_admin(user_id):
    return user_id in ADMIN_IDS

# ---------------- EVENTS ----------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="By PowerDev | /help"))
    try:
        synced = await bot.tree.sync()
        print(f"🔗 Synced {len(synced)} commands.")
    except Exception as e:
        print(f"❌ Sync error: {e}")

# ---------------- CREATE VPS ----------------
@bot.tree.command(name="createvps", description="Create a VPS for a user (Admin only)")
@app_commands.describe(name="VPS Name", ram="RAM (GB)", cpu="CPU cores", disk="Disk (GB)", user="User Discord ID")
async def createvps(interaction: Interaction, name: str, ram: int, cpu: int, disk: int, user: str):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("🚫 Only admins can create VPS.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    data = load_data()
    vps_id = str(len(data) + 1)
    container_name = f"vps-{vps_id}"
    ssh_port = random.randint(20000, 60000)

    # create SSH Dockerfile if not exists
    dockerfile_content = '''
    FROM ubuntu:22.04
    RUN apt update && apt install -y openssh-server sudo
    RUN mkdir /var/run/sshd
    RUN echo 'root:root' | chpasswd
    RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
    EXPOSE 22
    CMD ["/usr/sbin/sshd", "-D"]
    '''
    with open("Dockerfile", "w") as f:
        f.write(dockerfile_content)

    os.system("docker build -t powerdev-vps .")

    try:
        container = client_docker.containers.run(
            "powerdev-vps",
            name=container_name,
            detach=True,
            tty=True,
            stdin_open=True,
            mem_limit=f"{ram}g",
            cpus=cpu,
            ports={"22/tcp": ssh_port},
            command="/usr/sbin/sshd -D"
        )

        data[vps_id] = {
            "name": name,
            "user": user,
            "container": container_name,
            "ram": ram,
            "cpu": cpu,
            "disk": disk,
            "status": "running",
            "shared_with": [],
            "ssh_port": ssh_port
        }
        save_data(data)

        # DM user
        try:
            user_obj = await bot.fetch_user(int(user))
            embed = discord.Embed(
                title="🖥️ VPS Created!",
                description=(
                    f"**VPS ID:** {vps_id}\n"
                    f"**Name:** {name}\n"
                    f"**RAM:** {ram}GB\n"
                    f"**CPU:** {cpu}\n"
                    f"**Disk:** {disk}GB\n"
                    f"**SSH Port:** {ssh_port}\n"
                    f"**Username:** root\n"
                    f"**Password:** root\n\n"
                    f"Use `/managevps vpsid:{vps_id}` to view or control your VPS."
                ),
                color=discord.Color.green()
            )
            embed.set_footer(text="Made by PowerDev ⚡")
            await user_obj.send(embed=embed)
        except:
            pass

        await interaction.followup.send(f"✅ VPS `{name}` created successfully. SSH Port: `{ssh_port}`", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Error creating VPS: `{e}`", ephemeral=True)

# ---------------- DELETE VPS ----------------
@bot.tree.command(name="deletevps", description="Delete a VPS (Admin only)")
@app_commands.describe(vpsid="VPS ID")
async def deletevps(interaction: Interaction, vpsid: str):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("🚫 Only admins can delete VPS.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    data = load_data()
    if vpsid not in data:
        await interaction.followup.send("❌ VPS ID not found.", ephemeral=True)
        return

    try:
        container = client_docker.containers.get(data[vpsid]["container"])
        container.stop()
        container.remove()
    except docker.errors.NotFound:
        pass

    del data[vpsid]
    save_data(data)
    await interaction.followup.send(f"🗑️ VPS `{vpsid}` deleted successfully.", ephemeral=True)

# ---------------- MANAGE VPS ----------------
@bot.tree.command(name="managevps", description="Manage VPS (Start / Stop / Restart / Info)")
@app_commands.describe(vpsid="VPS ID", action="start/stop/restart/info")
async def managevps(interaction: Interaction, vpsid: str, action: str):
    await interaction.response.defer(thinking=True)
    data = load_data()

    if vpsid not in data:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return

    vps = data[vpsid]
    if (
        str(interaction.user.id) != str(vps["user"])
        and not is_admin(interaction.user.id)
        and str(interaction.user.id) not in vps["shared_with"]
    ):
        await interaction.followup.send("🚫 You don’t have access to this VPS.", ephemeral=True)
        return

    try:
        container = client_docker.containers.get(vps["container"])
    except docker.errors.NotFound:
        await interaction.followup.send("❌ Container not found.", ephemeral=True)
        return

    act = action.lower()
    if act == "start":
        container.start()
        vps["status"] = "running"
    elif act == "stop":
        container.stop()
        vps["status"] = "stopped"
    elif act == "restart":
        container.restart()
        vps["status"] = "running"
    elif act == "info":
        embed = discord.Embed(title=f"🖥️ VPS Info: {vps['name']}", color=discord.Color.blurple())
        embed.add_field(name="VPS ID", value=vpsid)
        embed.add_field(name="Status", value=vps["status"])
        embed.add_field(name="RAM", value=f"{vps['ram']}GB")
        embed.add_field(name="CPU", value=f"{vps['cpu']}")
        embed.add_field(name="Disk", value=f"{vps['disk']}GB")
        embed.add_field(name="SSH Port", value=str(vps["ssh_port"]))
        embed.add_field(name="Shared With", value=", ".join(vps["shared_with"]) or "None")
        embed.set_footer(text="Made by PowerDev ⚡")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    else:
        await interaction.followup.send("⚠️ Invalid action. Use: start / stop / restart / info", ephemeral=True)
        return

    save_data(data)
    await interaction.followup.send(f"✅ VPS `{vpsid}` {act}ed successfully.", ephemeral=True)

# ---------------- SHARE VPS ----------------
@bot.tree.command(name="sharevps", description="Add or remove a shared user (Admin only)")
@app_commands.describe(vpsid="VPS ID", action="add/remove", userid="User ID")
async def sharevps(interaction: Interaction, vpsid: str, action: str, userid: str):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("🚫 Only admins can manage sharing.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    data = load_data()
    if vpsid not in data:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return

    if action.lower() == "add":
        if userid not in data[vpsid]["shared_with"]:
            data[vpsid]["shared_with"].append(userid)
    elif action.lower() == "remove":
        if userid in data[vpsid]["shared_with"]:
            data[vpsid]["shared_with"].remove(userid)
    else:
        await interaction.followup.send("⚠️ Action must be add or remove.", ephemeral=True)
        return

    save_data(data)
    await interaction.followup.send(f"✅ VPS `{vpsid}` updated successfully.", ephemeral=True)

# ---------------- VPS PLANS ----------------
@bot.tree.command(name="plans", description="Show all VPS plans")
async def plans(interaction: Interaction):
    embed = discord.Embed(title="💎 VPS Plans", color=discord.Color.gold())
    for plan, p in VPS_PLANS.items():
        embed.add_field(
            name=f"📦 {plan}",
            value=f"**RAM:** {p['ram']}GB\n**CPU:** {p['cpu']}\n**Disk:** {p['disk']}GB\n💰 Intel: {p['intel']} credits\n💰 AMD: {p['amd']} credits",
            inline=False
        )
    embed.set_footer(text="Made by PowerDev ⚡")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- HELP ----------------
@bot.tree.command(name="help", description="Show help menu")
async def help_cmd(interaction: Interaction):
    embed = discord.Embed(title="🧭 VPS Bot — Help Menu", color=discord.Color.blue())
    embed.add_field(name="/createvps", value="Create a VPS (Admin only)", inline=False)
    embed.add_field(name="/deletevps", value="Delete a VPS (Admin only)", inline=False)
    embed.add_field(name="/managevps", value="Start / Stop / Restart / Info", inline=False)
    embed.add_field(name="/sharevps", value="Share VPS with a user (Admin only)", inline=False)
    embed.add_field(name="/plans", value="View VPS plans", inline=False)
    embed.add_field(name="/botinfo", value="Show bot information", inline=False)
    embed.set_footer(text="Made by PowerDev ⚡")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- BOT INFO ----------------
@bot.tree.command(name="botinfo", description="Show bot information")
async def botinfo(interaction: Interaction):
    embed = discord.Embed(
        title="🤖 PowerDev VPS Bot",
        description="A complete VPS management system built with Python + Docker + SSH.",
        color=discord.Color.green()
    )
    embed.add_field(name="Version", value="v4.0 Stable", inline=True)
    embed.add_field(name="Language", value="Python 3.11", inline=True)
    embed.add_field(name="Framework", value="discord.py", inline=True)
    embed.set_footer(text="Made by PowerDev ⚡")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- RUN ----------------
bot.run(TOKEN)
