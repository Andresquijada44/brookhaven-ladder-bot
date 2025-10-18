
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
    ok = set_rank(data, identifier, new_rank)
    if ok:
        await interaction.response.send_message(f"Set **{identifier}** to rank **#{new_rank}**.")
    else:
        await interaction.response.send_message("Failed. Check the name/rank and bounds.", ephemeral=True)


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


@client.tree.command(
    name="report",
    description="Report a match result by rank numbers (e.g., winner 2, loser 1, score 6-4 7-5)"
)
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
    lines = []
    for h in hist:
        lines.append(
            f"R{h.get('round','?')} — **{h['winner']}** def. **{h['loser']}** ({h['score']}) — rule {h['rule']}"
        )
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


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Please set DISCORD_TOKEN env var.")
    client.run(token)
