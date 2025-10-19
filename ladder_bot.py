# Brookhaven Tennis Academy — Ladder Bot
# Discord bot to manage a simple 1v1 ladder with weekly pairings and rank updates.

# ---------- Py 3.13 compatibility (audioop removed) ----------
import sys, types
if sys.version_info >= (3, 13) and "audioop" not in sys.modules:
    sys.modules["audioop"] = types.ModuleType("audioop")

# Windows event loop fix for aio libs (keep this once)
import sys
import asyncio
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- Persistent storage (keep this once) ---
import os, json

# Use /data in Railway (persistent volume), or DATA_DIR env var locally
DATA_DIR = os.getenv("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "ladder_data.json")

def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"players": [], "pairings": [], "round": 0, "history": []}
    except json.JSONDecodeError:
        # If file is corrupted, start fresh (or you can raise)
        return {"players": [], "pairings": [], "round": 0, "history": []}

def save_data(data: dict):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DATA_FILE)

import re
from datetime import datetime, timezone, date
from typing import List, Dict, Optional, Tuple

import discord
from discord import app_commands



# ===================== AI (optional) =====================
AI_ENABLED = True
AI_MAX_TOKENS = 500
AI_MODEL = "gpt-4o-mini"
ALLOWED_AI_CHANNEL_IDS: set[int] = set()  # e.g. {123456789012345678}

try:
    from openai import OpenAI
    _ai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
except Exception:
    _ai_client = None  # bot still works without AI

async def run_ai(prompt: str) -> str:
    if not AI_ENABLED or _ai_client is None:
        return "AI is disabled or not configured."
    try:
        resp = _ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": "You are a concise tennis assistant for a junior ladder program."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=AI_MAX_TOKENS,
            temperature=0.6,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"AI error: {e}"

# ===================== CONFIG =====================
GUILD_ID = 880307122947125249
LADDER_NAME = "Brookhaven Tennis Academy Ladder"
DATA_FILE = "ladder_data.json"
START_DATE = date(2025, 10, 17)   # set to today's date to test immediately
TIMEZONE = "America/Chicago"

# Ladder rules: "SWAP_ONLY" or "ONE_STEP_ALWAYS"
LADDER_RULE = "SWAP_ONLY"

# Role required for admin commands (set None to allow anyone)
ADMIN_ROLE_NAME = "Ladder Admin"

# ---- PRIVACY: allow only specific Discord users to use any commands ----
ALLOWED_USER_IDS = {692200166580551760}  # <— your user ID

async def _only_allowed(interaction: discord.Interaction) -> bool:
    # Return True to allow; False (or raise) to block.
    return interaction.user.id in ALLOWED_USER_IDS

@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # Friendly message if someone else tries to use a command
    if isinstance(error, app_commands.CheckFailure):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ This bot is private.", ephemeral=True)
            else:
                await interaction.followup.send("❌ This bot is private.", ephemeral=True)
        except Exception:
            pass

# ===================== STORAGE =====================
def load_data() -> Dict:
    if not os.path.exists(DATA_FILE):
        return {"players": [], "pairings": [], "round": 0, "history": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data: Dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def player_display(p: Dict) -> str:
    return f"<@{p['user_id']}>" if p.get("user_id") else p["name"]

# ===================== LADDER LOGIC =====================
def get_ladder(data: Dict) -> List[Dict]:
    return data.get("players", [])

def set_ladder(data: Dict, players: List[Dict]):
    data["players"] = players

def add_player(data: Dict, name: str, user_id: Optional[int]) -> int:
    players = get_ladder(data)
    players.append({"name": name, "user_id": user_id})
    save_data(data)
    return len(players)

def remove_player(data: Dict, identifier: str) -> bool:
    players = get_ladder(data)
    if identifier.isdigit():
        idx = int(identifier) - 1
        if 0 <= idx < len(players):
            del players[idx]
            save_data(data)
            return True
        return False
    lowered = identifier.strip().lower()
    for i, p in enumerate(players):
        if p["name"].lower() == lowered:
            del players[i]
            save_data(data)
            return True
    return False

def find_player_index(data: Dict, identifier: str) -> Optional[int]:
    """Accept rank number, @mention, exact name, or unique partial (case-insensitive)."""
    players = data.get("players", [])
    n = len(players)
    s = identifier.strip()

    if s.isdigit():
        i = int(s) - 1
        return i if 0 <= i < n else None

    m = re.match(r"<@!?(\d+)>", s)
    if m:
        uid = int(m.group(1))
        for i, p in enumerate(players):
            if p.get("user_id") == uid:
                return i
        return None

    lowered = s.lower()
    for i, p in enumerate(players):
        if p["name"].lower() == lowered:
            return i

    matches = [i for i, p in enumerate(players) if lowered in p["name"].lower()]
    return matches[0] if len(matches) == 1 else None

def generate_pairings(data: Dict) -> List[Tuple[int, int]]:
    players = get_ladder(data)
    pairings = []
    i = 0
    while i < len(players):
        if i + 1 < len(players):
            pairings.append((i + 1, i + 2))  # 1-based ranks
            i += 2
        else:
            pairings.append((i + 1, 0))      # 0 = BYE
            i += 1
    data["pairings"] = pairings
    data["round"] = data.get("round", 0) + 1
    save_data(data)
    return pairings

def apply_result(data: Dict, winner_rank: int, loser_rank: int, score: str, reporter_id: Optional[int]):
    players = get_ladder(data)
    n = len(players)
    if not (1 <= winner_rank <= n and 1 <= loser_rank <= n):
        raise ValueError("Invalid ranks")

    winner_idx = winner_rank - 1
    loser_idx = loser_rank - 1

    history_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "round": data.get("round", 0),
        "winner_rank_pre": winner_rank,
        "loser_rank_pre": loser_rank,
        "winner": players[winner_idx]["name"],
        "loser": players[loser_idx]["name"],
        "score": score,
        "reporter_id": reporter_id,
        "rule": LADDER_RULE,
    }

    if LADDER_RULE == "SWAP_ONLY":
        if winner_idx > loser_idx:
            players[winner_idx], players[loser_idx] = players[loser_idx], players[winner_idx]
    elif LADDER_RULE == "ONE_STEP_ALWAYS":
        if winner_idx > 0:
            players[winner_idx - 1], players[winner_idx] = players[winner_idx], players[winner_idx - 1]
            if loser_idx == winner_idx - 1:
                loser_idx = winner_idx
                winner_idx -= 1
            else:
                winner_idx -= 1
        if loser_idx < len(players) - 1:
            players[loser_idx + 1], players[loser_idx] = players[loser_idx], players[loser_idx + 1]

    data.setdefault("history", []).append(history_entry)
    save_data(data)

def set_rank(data: Dict, identifier: str, new_rank: int) -> Tuple[bool, str]:
    players = get_ladder(data)
    n = len(players)
    if n == 0:
        return False, "No players on the ladder yet. Use /ladder_add first."
    if not (1 <= new_rank <= n):
        return False, f"New rank must be between 1 and {n}."

    idx = find_player_index(data, identifier)
    if idx is None:
        names = ", ".join(f"#{i+1} {p['name']}" for i, p in enumerate(players))
        return False, (
            f"Couldn’t identify **{identifier}**.\n"
            f"Try rank number, exact name, @mention, or a longer partial.\n"
            f"Current ladder: {names}"
        )

    p = players.pop(idx)
    players.insert(new_rank - 1, p)
    save_data(data)
    return True, f"Moved **{p['name']}** to rank **#{new_rank}**."

# ===================== EMBEDS =====================
def format_ladder_embed(players: List[Dict]) -> discord.Embed:
    title = f"{LADDER_NAME} — Current Ladder"
    desc = "\n".join(f"**#{i}**  {player_display(p)}" for i, p in enumerate(players, start=1))
    embed = discord.Embed(title=title, description=desc, color=0x2b7cff)
    embed.set_footer(text=f"Pairs update via /pairings • TZ: {TIMEZONE}")
    return embed

def format_pairings_embed(pairings: List[Tuple[int, int]], players: List[Dict], rnd: int) -> discord.Embed:
    lines = []
    for a, b in pairings:
        if b == 0:
            lines.append(f"**#{a}** {players[a-1]['name']} — **BYE**")
        else:
            lines.append(f"**#{a}** {players[a-1]['name']}  **vs**  **#{b}** {players[b-1]['name']}")
    return discord.Embed(title=f"Round {rnd} Pairings", description="\n".join(lines), color=0x00b894)

# ===================== PERMISSIONS =====================
def is_admin(member: discord.Member) -> bool:
    if ADMIN_ROLE_NAME is None:
        return True
    return any(r.name == ADMIN_ROLE_NAME for r in member.roles)

# ===================== DISCORD BOT =====================
class LadderBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = False
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self): 

# Make all slash commands private to ALLOWED_USER_IDS
        self.tree.add_check(_only_allowed)

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

# instantiate AFTER class definition
data = load_data()
client = LadderBot()

# ---------- Slash Commands ----------
@client.tree.command(name="ladder_show", description="Show the current ladder")
async def ladder_show(interaction: discord.Interaction):
    await interaction.response.defer(thinking=False, ephemeral=False)
    players = get_ladder(data)
    if not players:
        await interaction.followup.send("No players yet. Use /ladder_add to add players.")
        return
    await interaction.followup.send(embed=format_ladder_embed(players))

@client.tree.command(name="ladder_add", description="Add a player to the bottom of the ladder")
@app_commands.describe(name="Display name for the player", user="(Optional) Link a Discord user to this player")
async def ladder_add(interaction: discord.Interaction, name: str, user: Optional[discord.Member] = None):
    if not is_admin(interaction.user):
        await interaction.response.send_message("You need the Ladder Admin role to use this.", ephemeral=True)
        return
    uid = user.id if user else None
    rank = add_player(data, name=name, user_id=uid)
    await interaction.response.send_message(f"Added **{name}** at rank **#{rank}**.")

@client.tree.command(name="ladder_remove", description="Remove a player by name or current rank number")
async def ladder_remove(interaction: discord.Interaction, identifier: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("You need the Ladder Admin role to use this.", ephemeral=True)
        return
    ok = remove_player(data, identifier)
    if ok:
        await interaction.response.send_message(f"Removed **{identifier}** from ladder.")
    else:
        await interaction.response.send_message("Couldn't find that player/rank.", ephemeral=True)

@client.tree.command(name="ladder_setrank", description="Set a player's rank (1 = top)")
async def ladder_setrank(interaction: discord.Interaction, identifier: str, new_rank: int):
    if not is_admin(interaction.user):
        await interaction.response.send_message("You need the Ladder Admin role to use this.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False, thinking=False)
    ok, msg = set_rank(data, identifier, new_rank)
    if ok:
        await interaction.followup.send(msg)
        await interaction.followup.send(embed=format_ladder_embed(get_ladder(data)))
    else:
        try:
            await interaction.user.send(msg)
            await interaction.followup.send("Couldn’t update rank. I DM’d you details.")
        except Exception:
            await interaction.followup.send("Couldn’t update rank. Check the identifier and bounds.")

@client.tree.command(name="pairings", description="Generate and show new round pairings (adjacent ranks)")
async def pairings(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("You need the Ladder Admin role to use this.", ephemeral=True)
        return
    today = date.today()
    if today < START_DATE:
        await interaction.response.send_message(
            f"Pairings start on {START_DATE.isoformat()}. Today is {today.isoformat()}.", ephemeral=True
        )
        return
    ps = generate_pairings(data)
    embed = format_pairings_embed(ps, get_ladder(data), data.get("round", 0))
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="report", description="Report a match result by rank numbers (winner, loser, score)")
async def report(interaction: discord.Interaction, winner_rank: int, loser_rank: int, score: str):
    try:
        apply_result(data, winner_rank, loser_rank, score, reporter_id=interaction.user.id)
    except Exception as e:
        await interaction.response.send_message(f"Error: {e}", ephemeral=True)
        return
    await interaction.response.send_message(
        f"Result recorded: **#{winner_rank} beat #{loser_rank}** ({score}). Ladder updated."
    )
    await interaction.followup.send(embed=format_ladder_embed(get_ladder(data)))

@client.tree.command(name="history", description="Show the last 10 reported results")
async def history(interaction: discord.Interaction):
    hist = data.get("history", [])[-10:]
    if not hist:
        await interaction.response.send_message("No results yet.")
        return
    lines = [
        f"R{h.get('round','?')} — **{h['winner']}** def. **{h['loser']}** ({h['score']}) — rule {h['rule']}"
        for h in hist
    ]
    embed = discord.Embed(title="Recent Results", description="\n".join(lines), color=0x6c5ce7)
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="config_rule", description="Set ladder promotion rule: SWAP_ONLY or ONE_STEP_ALWAYS")
async def config_rule(interaction: discord.Interaction, rule: str):
    global LADDER_RULE
    if not is_admin(interaction.user):
        await interaction.response.send_message("You need the Ladder Admin role to use this.", ephemeral=True)
        return
    rule = rule.upper().strip()
    if rule not in {"SWAP_ONLY", "ONE_STEP_ALWAYS"}:
        await interaction.response.send_message("Invalid rule. Use SWAP_ONLY or ONE_STEP_ALWAYS.", ephemeral=True)
        return
    LADDER_RULE = rule
    await interaction.response.send_message(f"Ladder rule set to **{LADDER_RULE}**.")

# ---------- AI Slash Commands (AFTER client is created) ----------
@client.tree.command(name="ai", description="Ask the tennis assistant (coaching tips, summaries, ideas).")
@app_commands.describe(prompt="What do you want?")
async def ai(interaction: discord.Interaction, prompt: str):
    if ALLOWED_AI_CHANNEL_IDS and interaction.channel_id not in ALLOWED_AI_CHANNEL_IDS:
        await interaction.response.send_message("AI is disabled in this channel.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=False)
    reply = await run_ai(prompt)
    if len(reply) > 1900:
        reply = reply[:1900] + "…"
    await interaction.followup.send(reply)

@client.tree.command(name="coach", description="3 short practice drills for a group.")
@app_commands.describe(for_group="e.g., 12U, beginners, varsity doubles")
async def coach(interaction: discord.Interaction, for_group: str):
    await interaction.response.defer(thinking=True)
    prompt = f"Give 3 concise practice drills for {for_group} tennis players. 1–2 sentences each."
    await interaction.followup.send(await run_ai(prompt))

@client.tree.command(name="summarize_round", description="Summarize recent results in 2–3 sentences.")
async def summarize_round(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    hist = data.get("history", [])[-10:]
    if not hist:
        await interaction.followup.send("No recent results to summarize.")
        return
    lines = [f"R{h.get('round','?')}: {h['winner']} def {h['loser']} {h['score']}" for h in hist]
    prompt = "Summarize these junior ladder results in 2-3 sentences:\n" + "\n".join(lines)
    await interaction.followup.send(await run_ai(prompt))

# ---------- Run ----------
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Please set DISCORD_TOKEN env var.")
    client.run(token)

