# Brookhaven Tennis Academy — Ladder Bot
# Discord bot to manage a simple 1v1 ladder with weekly pairings and rank updates.
"""Discord ladder bot for Brookhaven Tennis Academy.

This module defines the bot, ladder storage helpers, and Discord slash
commands. It is intentionally self‑contained so single‑file deploys on
Railway/Render remain simple while keeping the code organized.

It also includes **optional self-tests** you can run locally without Discord by
setting `RUN_LADDER_TESTS=1`. These tests verify the ladder logic.

If you see `SystemExit: Please set DISCORD_TOKEN env var.`, set the
`DISCORD_TOKEN` environment variable as shown at the bottom of this file.
"""

from __future__ import annotations

# ===================== Python & OS compatibility =====================
import sys
import types
import asyncio
import os
import json
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from copy import deepcopy
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---- Python 3.13 shim: some envs remove the audioop module
if sys.version_info >= (3, 13) and "audioop" not in sys.modules:
    sys.modules["audioop"] = types.ModuleType("audioop")

# ---- Windows event loop fix (harmless elsewhere)
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ===================== Third‑party =====================
import discord
from discord import app_commands

# ===================== Constants & Configuration =====================
#⚠️ Set this to your server's Guild ID to speed up command syncs
GUILD_ID = 880307122947125249

LADDER_NAME = "Brookhaven Tennis Academy Ladder"
TIMEZONE = "America/Chicago"

# First day pairings are allowed to be generated
START_DATE = date(2025, 10, 17)

# Ladder rules: "SWAP_ONLY" or "ONE_STEP_ALWAYS"
DEFAULT_LADDER_RULE = "SWAP_ONLY"

# Role required for admin commands (set to None to allow anyone)
ADMIN_ROLE_NAME: Optional[str] = "Ladder Admin"

# Restrict all commands to these Discord user IDs (empty = public)
ALLOWED_USER_IDS: set[int] = {692200166580551760}

# Persistent storage directory (Railway uses /data). You can override locally.
DATA_DIR = os.getenv("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "ladder_data.json")

# ===================== Storage Layer =====================
DEFAULT_STATE: Dict[str, object] = {
    "players": [],      # list[{"name": str, "user_id": Optional[int]}]
    "pairings": [],     # list[(rank_a, rank_b)] with 0 = BYE
    "round": 0,
    "history": [],
}


class LadderRepository:
    """Handle JSON persistence for ladder state."""

    def __init__(self, file_path: str, default: Dict[str, object]) -> None:
        self.file_path = file_path
        self._default = default

    def load(self) -> Dict[str, object]:
        try:
            with open(self.file_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            data = deepcopy(self._default)
        except json.JSONDecodeError:
            data = deepcopy(self._default)
        return data

    def save(self, state: Dict[str, object]) -> None:
        tmp_path = self.file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp_path, self.file_path)


# ===================== Domain =====================
Player = Dict[str, Optional[int]]  # store "name" (str) and optional "user_id"


def _player_display(player: Player) -> str:
    user_id = player.get("user_id")
    if user_id:
        return f"<@{user_id}>"
    return str(player["name"])


@dataclass(slots=True)
class Pairing:
    """Represents a pairing by 1-based rank numbers."""

    first_rank: int
    second_rank: int  # zero indicates a bye

    def describe(self, players: Sequence[Player]) -> str:
        if self.second_rank == 0:
            return f"**#{self.first_rank}** {players[self.first_rank - 1]['name']} — **BYE**"
        return (
            "**#{a}** {a_name}  **vs**  **#{b}** {b_name}".format(
                a=self.first_rank,
                a_name=players[self.first_rank - 1]["name"],
                b=self.second_rank,
                b_name=players[self.second_rank - 1]["name"],
            )
        )


class LadderService:
    """Business logic for ladder operations backed by a repository."""

    def __init__(self, repository: LadderRepository, *, rule: str) -> None:
        self._repository = repository
        self._state = repository.load()
        self._rule = rule

    # ---------- Persistence helpers ----------
    def _players(self) -> List[Player]:
        return self._state.setdefault("players", [])  # type: ignore[return-value]

    def _history(self) -> List[Dict[str, object]]:
        return self._state.setdefault("history", [])  # type: ignore[return-value]

    def save(self) -> None:
        self._repository.save(self._state)

    # ---------- Public API ----------
    @property
    def players(self) -> List[Player]:
        return self._players()

    @property
    def rule(self) -> str:
        return self._rule

    @rule.setter
    def rule(self, value: str) -> None:
        self._rule = value

    @property
    def round(self) -> int:
        return int(self._state.get("round", 0))

    def add_player(self, name: str, user_id: Optional[int]) -> int:
        player = {"name": name, "user_id": user_id}
        self.players.append(player)
        self.save()
        return len(self.players)

    def remove_player(self, identifier: str) -> bool:
        idx = self._resolve_player_index(identifier)
        if idx is None:
            return False
        del self.players[idx]
        self.save()
        return True

    def set_rank(self, identifier: str, new_rank: int) -> Tuple[bool, str]:
        players = self.players
        total = len(players)
        if total == 0:
            return False, "No players on the ladder yet. Use /ladder_add first."
        if not (1 <= new_rank <= total):
            return False, f"New rank must be between 1 and {total}."

        idx = self._resolve_player_index(identifier)
        if idx is None:
            listing = ", ".join(f"#{i + 1} {p['name']}" for i, p in enumerate(players))
            return (
                False,
                (
                    f"Couldn’t identify **{identifier}**.\n"
                    "Try rank number, exact name, @mention, or a longer partial.\n"
                    f"Current ladder: {listing}"
                ),
            )

        player = players.pop(idx)
        players.insert(new_rank - 1, player)
        self.save()
        return True, f"Moved **{player['name']}** to rank **#{new_rank}**."

    def generate_pairings(self) -> List[Pairing]:
        players = self.players
        pairings: List[Pairing] = []
        i = 0
        while i < len(players):
            if i + 1 < len(players):
                pairings.append(Pairing(i + 1, i + 2))
                i += 2
            else:
                pairings.append(Pairing(i + 1, 0))
                i += 1
        self._state["pairings"] = [(p.first_rank, p.second_rank) for p in pairings]
        self._state["round"] = self.round + 1
        self.save()
        return pairings

    def record_result(
        self,
        *,
        winner_rank: int,
        loser_rank: int,
        score: str,
        reporter_id: Optional[int],
    ) -> None:
        players = self.players
        total = len(players)
        if not (1 <= winner_rank <= total and 1 <= loser_rank <= total):
            raise ValueError("Invalid ranks")

        winner_idx = winner_rank - 1
        loser_idx = loser_rank - 1

        history_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "round": self.round,
            "winner_rank_pre": winner_rank,
            "loser_rank_pre": loser_rank,
            "winner": players[winner_idx]["name"],
            "loser": players[loser_idx]["name"],
            "score": score,
            "reporter_id": reporter_id,
            "rule": self.rule,
        }

        if self.rule == "SWAP_ONLY":
            if winner_idx > loser_idx:
                players[winner_idx], players[loser_idx] = players[loser_idx], players[winner_idx]
        elif self.rule == "ONE_STEP_ALWAYS":
            if winner_idx > 0:
                players[winner_idx - 1], players[winner_idx] = (
                    players[winner_idx],
                    players[winner_idx - 1],
                )
                if loser_idx == winner_idx - 1:
                    loser_idx = winner_idx
                    winner_idx -= 1
                else:
                    winner_idx -= 1
            if loser_idx < len(players) - 1:
                players[loser_idx + 1], players[loser_idx] = (
                    players[loser_idx],
                    players[loser_idx + 1],
                )
        else:
            raise ValueError(f"Unsupported ladder rule: {self.rule}")

        self._history().append(history_entry)
        self.save()

    def recent_history(self, limit: int = 10) -> List[Dict[str, object]]:
        return self._history()[-limit:]

    # ---------- Resolution helpers ----------
    def _resolve_player_index(self, identifier: str) -> Optional[int]:
        players = self.players
        text = identifier.strip()
        if not text:
            return None

        # Rank number
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(players):
                return idx
            return None

        # @mention
        mention_match = re.fullmatch(r"<@!?(\d+)>", text)
        if mention_match:
            target_id = int(mention_match.group(1))
            for i, player in enumerate(players):
                if player.get("user_id") == target_id:
                    return i
            return None

        # Exact or unique partial name
        lowered = text.lower()
        for i, player in enumerate(players):
            if player["name"].lower() == lowered:
                return i
        matches = [i for i, player in enumerate(players) if lowered in player["name"].lower()]
        if len(matches) == 1:
            return matches[0]
        return None


# ===================== Embeds =====================

def ladder_embed(players: Sequence[Player]) -> discord.Embed:
    title = f"{LADDER_NAME} — Current Ladder"
    description = "\n".join(
        f"**#{idx}**  {_player_display(player)}" for idx, player in enumerate(players, start=1)
    )
    embed = discord.Embed(title=title, description=description, color=0x2B7CFF)
    embed.set_footer(text=f"Pairs update via /pairings • TZ: {TIMEZONE}")
    return embed


def pairings_embed(pairings: Iterable[Pairing], players: Sequence[Player], round_no: int) -> discord.Embed:
    lines = [pairing.describe(players) for pairing in pairings]
    embed = discord.Embed(
        title=f"Round {round_no} Pairings",
        description="\n".join(lines) if lines else "No players yet.",
        color=0x00B894,
    )
    return embed


# ===================== Optional AI helpers =====================
AI_ENABLED = True
AI_MAX_TOKENS = 500
AI_MODEL = "gpt-4o-mini"
ALLOWED_AI_CHANNEL_IDS: set[int] = set()  # e.g. {123456789012345678}

try:  # optional dependency — bot works without it
    from openai import OpenAI  # type: ignore
    _ai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
except Exception:
    _ai_client = None


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
    except Exception as exc:
        return f"AI error: {exc}"


# ===================== Permissions =====================

def is_admin(member: discord.Member) -> bool:
    if ADMIN_ROLE_NAME is None:
        return True
    return any(role.name == ADMIN_ROLE_NAME for role in member.roles)


def only_allowed(interaction: discord.Interaction) -> bool:
    if not ALLOWED_USER_IDS:  # public
        return True
    return interaction.user.id in ALLOWED_USER_IDS


# ===================== Discord Bot =====================

class LadderBot(discord.Client):
    def __init__(self, service: LadderService):
        intents = discord.Intents.default()
        intents.message_content = False
        intents.members = True
        super().__init__(intents=intents)
        self.service = service
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        # Make all slash commands private to ALLOWED_USER_IDS (if set)
        self.tree.add_check(only_allowed)
        # Fast guild‑only sync if a GUILD_ID is provided
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


# Instantiate storage & bot
_repository = LadderRepository(DATA_FILE, DEFAULT_STATE)
_service = LadderService(_repository, rule=DEFAULT_LADDER_RULE)
client = LadderBot(_service)


# ---------- Error handler ----------
@client.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.CheckFailure):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ This bot is private.", ephemeral=True)
            else:
                await interaction.followup.send("❌ This bot is private.", ephemeral=True)
        except Exception:
            pass


# ===================== Slash Commands =====================

@client.tree.command(name="ladder_show", description="Show the current ladder")
async def ladder_show(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=False, ephemeral=False)
    players = _service.players
    if not players:
        await interaction.followup.send("No players yet. Use /ladder_add to add players.")
        return
    await interaction.followup.send(embed=ladder_embed(players))


@client.tree.command(name="ladder_add", description="Add a player to the bottom of the ladder")
@app_commands.describe(name="Display name for the player", user="(Optional) Link a Discord user to this player")
async def ladder_add(
    interaction: discord.Interaction, name: str, user: Optional[discord.Member] = None
) -> None:
    if not is_admin(interaction.user):
        await interaction.response.send_message("You need the Ladder Admin role to use this.", ephemeral=True)
        return
    rank = _service.add_player(name=name, user_id=(user.id if user else None))
    await interaction.response.send_message(f"Added **{name}** at rank **#{rank}**.")


@client.tree.command(name="ladder_remove", description="Remove a player by name or current rank number")
async def ladder_remove(interaction: discord.Interaction, identifier: str) -> None:
    if not is_admin(interaction.user):
        await interaction.response.send_message("You need the Ladder Admin role to use this.", ephemeral=True)
        return
    if _service.remove_player(identifier):
        await interaction.response.send_message(f"Removed **{identifier}** from ladder.")
    else:
        await interaction.response.send_message("Couldn't find that player/rank.", ephemeral=True)


@client.tree.command(name="ladder_setrank", description="Set a player's rank (1 = top)")
async def ladder_setrank(interaction: discord.Interaction, identifier: str, new_rank: int) -> None:
    if not is_admin(interaction.user):
        await interaction.response.send_message("You need the Ladder Admin role to use this.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False, thinking=False)
    ok, message = _service.set_rank(identifier, new_rank)
    if ok:
        await interaction.followup.send(message)
        await interaction.followup.send(embed=ladder_embed(_service.players))
    else:
        try:
            await interaction.user.send(message)
            await interaction.followup.send("Couldn’t update rank. I DM’d you details.")
        except Exception:
            await interaction.followup.send("Couldn’t update rank. Check the identifier and bounds.")


@client.tree.command(name="pairings", description="Generate and show new round pairings (adjacent ranks)")
async def pairings(interaction: discord.Interaction) -> None:
    if not is_admin(interaction.user):
        await interaction.response.send_message("You need the Ladder Admin role to use this.", ephemeral=True)
        return
    today = date.today()
    if today < START_DATE:
        await interaction.response.send_message(
            f"Pairings start on {START_DATE.isoformat()}. Today is {today.isoformat()}.",
            ephemeral=True,
        )
        return
    pairings_list = _service.generate_pairings()
    await interaction.response.send_message(
        embed=pairings_embed(pairings_list, _service.players, _service.round)
    )


@client.tree.command(name="report", description="Report a match result by rank numbers (winner, loser, score)")
async def report(
    interaction: discord.Interaction, winner_rank: int, loser_rank: int, score: str
) -> None:
    try:
        _service.record_result(
            winner_rank=winner_rank,
            loser_rank=loser_rank,
            score=score,
            reporter_id=interaction.user.id,
        )
    except Exception as exc:
        await interaction.response.send_message(f"Error: {exc}", ephemeral=True)
        return
    await interaction.response.send_message(
        f"Result recorded: **#{winner_rank} beat #{loser_rank}** ({score}). Ladder updated."
    )
    await interaction.followup.send(embed=ladder_embed(_service.players))


@client.tree.command(name="history", description="Show the last 10 reported results")
async def history(interaction: discord.Interaction) -> None:
    recent = _service.recent_history()
    if not recent:
        await interaction.response.send_message("No results yet.")
        return
    lines = [
        (
            f"R{entry.get('round', '?')} — **{entry['winner']}** def. "
            f"**{entry['loser']}** ({entry['score']}) — rule {entry['rule']}"
        )
        for entry in recent
    ]
    embed = discord.Embed(title="Recent Results", description="\n".join(lines), color=0x6C5CE7)
    await interaction.response.send_message(embed=embed)


@client.tree.command(name="config_rule", description="Set ladder promotion rule: SWAP_ONLY or ONE_STEP_ALWAYS")
async def config_rule(interaction: discord.Interaction, rule: str) -> None:
    if not is_admin(interaction.user):
        await interaction.response.send_message("You need the Ladder Admin role to use this.", ephemeral=True)
        return
    rule_upper = rule.upper().strip()
    if rule_upper not in {"SWAP_ONLY", "ONE_STEP_ALWAYS"}:
        await interaction.response.send_message(
            "Invalid rule. Use SWAP_ONLY or ONE_STEP_ALWAYS.", ephemeral=True
        )
        return
    _service.rule = rule_upper
    await interaction.response.send_message(f"Ladder rule set to **{_service.rule}**.")


# ---------- AI Slash Commands (optional) ----------
@client.tree.command(name="ai", description="Ask the tennis assistant (coaching tips, summaries, ideas).")
@app_commands.describe(prompt="What do you want?")
async def ai(interaction: discord.Interaction, prompt: str) -> None:
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
async def coach(interaction: discord.Interaction, for_group: str) -> None:
    await interaction.response.defer(thinking=True)
    prompt = f"Give 3 concise practice drills for {for_group} tennis players. 1–2 sentences each."
    await interaction.followup.send(await run_ai(prompt))


@client.tree.command(name="summarize_round", description="Summarize recent results in 2–3 sentences.")
async def summarize_round(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    history_items = _service.recent_history()
    if not history_items:
        await interaction.followup.send("No recent results to summarize.")
        return
    lines = [
        f"R{entry.get('round', '?')}: {entry['winner']} def {entry['loser']} {entry['score']}"
        for entry in history_items
    ]
    prompt = "Summarize these junior ladder results in 2-3 sentences:\n" + "\n".join(lines)
    await interaction.followup.send(await run_ai(prompt))


# ===================== Self‑tests (no Discord needed) =====================

def _run_ladder_self_tests() -> None:
    """Minimal unit tests for LadderService and helpers.
    Run with: `RUN_LADDER_TESTS=1 python ladder_bot.py`
    """
    import shutil

    # Use a temporary file so we don't touch production data
    tmpdir = tempfile.mkdtemp(prefix="ladder_tests_")
    try:
        test_file = os.path.join(tmpdir, "state.json")
        repo = LadderRepository(test_file, DEFAULT_STATE)
        svc = LadderService(repo, rule="SWAP_ONLY")

        # --- Test: add players ---
        assert svc.add_player("Alice", None) == 1
        assert svc.add_player("Bob", None) == 2
        assert svc.add_player("Charlie", None) == 3
        assert [p["name"] for p in svc.players] == ["Alice", "Bob", "Charlie"]

        # --- Test: generate pairings with odd player count (BYE) ---
        pairs = svc.generate_pairings()
        assert [(p.first_rank, p.second_rank) for p in pairs] == [(1, 2), (3, 0)]
        assert svc.round == 1

        # --- Test: record_result under SWAP_ONLY (underdog beats higher -> swap) ---
        svc.record_result(winner_rank=2, loser_rank=1, score="6-3 6-4", reporter_id=123)
        assert [p["name"] for p in svc.players] == ["Bob", "Alice", "Charlie"]
        assert len(svc.recent_history()) >= 1

        # --- Test: set_rank using name and bounds ---
        ok, msg = svc.set_rank("Charlie", 1)
        assert ok and "Moved **Charlie** to rank **#1**" in msg
        assert [p["name"] for p in svc.players] == ["Charlie", "Bob", "Alice"]

        # --- Test: remove by rank number ---
        assert svc.remove_player("2") is True
        assert [p["name"] for p in svc.players] == ["Charlie", "Alice"]

        # --- Test: ONE_STEP_ALWAYS rule behavior ---
        svc.rule = "ONE_STEP_ALWAYS"
        # Make sure we have at least 3 players again
        svc.add_player("Diego", None)
        # Winner at rank 3 should move up one; loser at rank 2 should move down one
        # Current order: Charlie (1), Alice (2), Diego (3)
        svc.record_result(winner_rank=3, loser_rank=2, score="7-6 6-7 10-8", reporter_id=None)
        assert [p["name"] for p in svc.players] == ["Charlie", "Diego", "Alice"]

        # --- Added tests: identifier resolution & bounds ---
        # Unique partial name resolution
        ok, msg = svc.set_rank("Ali", 2)  # matches "Alice"
        assert ok and "Alice" in msg
        # Out-of-bounds rank
        ok, msg = svc.set_rank("Charlie", 99)
        assert ok is False and "between 1 and" in msg
        # Unknown identifier
        ok, msg = svc.set_rank("Nonexistent", 1)
        assert ok is False and "Couldn’t identify" in msg
        # Invalid record_result ranks
        try:
            svc.record_result(winner_rank=10, loser_rank=1, score="6-0", reporter_id=None)
            raise AssertionError("record_result must raise on invalid ranks")
        except ValueError:
            pass

        print("✅ Ladder self-tests passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===================== Entrypoint =====================

def _print_env_help_and_exit() -> None:
    msg = (
        "\n[!] DISCORD_TOKEN is not set. The bot cannot connect to Discord.\n\n"
        "Set the token and run again. Examples:\n\n"
        "# macOS/Linux (bash)\n"
        "export DISCORD_TOKEN=YOUR_BOT_TOKEN\n"
        "python ladder_bot.py\n\n"
        "# Windows (PowerShell)\n"
        "$Env:DISCORD_TOKEN='YOUR_BOT_TOKEN'\n"
        "python ladder_bot.py\n\n"
        "# Railway (Project → Variables)\n"
        "Add a variable named DISCORD_TOKEN with your bot token value.\n"
    )
    # Write directly to stderr for compatibility with older Python builds
    sys.stderr.write(msg + "\n")
    raise SystemExit("Please set DISCORD_TOKEN env var.")


if __name__ == "__main__":
    if os.getenv("RUN_LADDER_TESTS") == "1":
        _run_ladder_self_tests()
        raise SystemExit(0)

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        _print_env_help_and_exit()
    client.run(token)
