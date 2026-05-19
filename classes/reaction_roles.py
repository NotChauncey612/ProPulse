import discord
from discord.ext import commands

from .storage import load_json, save_json


DATA_PATH = "data/reaction_roles.json"
UPDATE_PINGS_EMOJI = "\u2705"
UPDATE_PINGS_ROLE_NAME = "Update Pings"


class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = self.load_config()

    def load_config(self):
        data = load_json(DATA_PATH, default={})
        if not isinstance(data, dict):
            data = {}
        panels = data.get("panels", {})
        if not isinstance(panels, dict):
            panels = {}
        data["panels"] = panels
        return data

    def save_config(self):
        save_json(DATA_PATH, self.config)

    def get_panel(self, message_id):
        return self.config.get("panels", {}).get(str(message_id))

    def save_panel(self, guild_id, channel_id, message_id, roles):
        self.config.setdefault("panels", {})[str(message_id)] = {
            "guild_id": int(guild_id),
            "channel_id": int(channel_id),
            "message_id": int(message_id),
            "roles": {str(emoji): int(role_id) for emoji, role_id in roles.items()},
        }
        self.save_config()

    def find_configured_role_id(self, guild, emoji):
        for panel in self.config.get("panels", {}).values():
            if int(panel.get("guild_id", 0)) != guild.id:
                continue
            role_id = panel.get("roles", {}).get(str(emoji))
            if role_id:
                return int(role_id)
        return None

    async def get_or_create_update_pings_role(self, guild):
        role_id = self.find_configured_role_id(guild, UPDATE_PINGS_EMOJI)
        if role_id:
            role = guild.get_role(role_id)
            if role:
                return role, None

        role = discord.utils.get(guild.roles, name=UPDATE_PINGS_ROLE_NAME)
        if role:
            return role, None

        me = guild.me or guild.get_member(self.bot.user.id)
        if not me or not me.guild_permissions.manage_roles:
            return None, "I need Manage Roles permission to create the Update Pings role."

        try:
            role = await guild.create_role(
                name=UPDATE_PINGS_ROLE_NAME,
                mentionable=True,
                reason="Create reaction role for update pings",
            )
        except discord.Forbidden:
            return None, "I need Manage Roles permission to create the Update Pings role."
        except discord.HTTPException as exc:
            return None, f"Discord rejected the role creation: {exc}"

        return role, None

    def can_manage_role(self, guild, role):
        me = guild.me or guild.get_member(self.bot.user.id)
        if not me or not me.guild_permissions.manage_roles:
            return False
        return role < me.top_role

    async def update_member_role(self, guild, member, role, should_add):
        if not self.can_manage_role(guild, role):
            return

        try:
            if should_add:
                if role not in member.roles:
                    await member.add_roles(role, reason="Reaction role selected")
            else:
                if role in member.roles:
                    await member.remove_roles(role, reason="Reaction role removed")
        except (discord.Forbidden, discord.HTTPException):
            return

    async def handle_reaction_payload(self, payload, should_add):
        if payload.guild_id is None:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        panel = self.get_panel(payload.message_id)
        if not panel or int(panel.get("guild_id", 0)) != payload.guild_id:
            return

        role_id = panel.get("roles", {}).get(str(payload.emoji))
        if not role_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        role = guild.get_role(int(role_id))
        if not role:
            return

        member = payload.member
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        if member.bot:
            return

        await self.update_member_role(guild, member, role, should_add)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self.handle_reaction_payload(payload, should_add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self.handle_reaction_payload(payload, should_add=False)

    @commands.command(aliases=["rr"])
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def reactionroles(self, ctx, channel: discord.TextChannel = None):
        """Create a reaction-role panel for update pings."""
        channel = channel or ctx.channel
        role, error = await self.get_or_create_update_pings_role(ctx.guild)
        if error:
            await ctx.send(error)
            return

        if not self.can_manage_role(ctx.guild, role):
            await ctx.send(
                "I need Manage Roles permission, and my bot role must be above "
                f"the `{role.name}` role."
            )
            return

        embed = discord.Embed(
            title="Notification Roles",
            description=(
                f"React with {UPDATE_PINGS_EMOJI} to get {role.mention}.\n"
                f"Remove your reaction to remove the role."
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name=f"{UPDATE_PINGS_EMOJI} Update pings",
            value="Get pinged when updates are announced.",
            inline=False,
        )

        try:
            message = await channel.send(embed=embed)
            await message.add_reaction(UPDATE_PINGS_EMOJI)
        except discord.Forbidden:
            await ctx.send(
                f"I need permission to send messages and add reactions in {channel.mention}."
            )
            return
        except discord.HTTPException as exc:
            await ctx.send(f"Discord rejected the reaction-role panel: {exc}")
            return

        self.save_panel(
            guild_id=ctx.guild.id,
            channel_id=channel.id,
            message_id=message.id,
            roles={UPDATE_PINGS_EMOJI: role.id},
        )
        await ctx.send(f"Reaction-role panel created in {channel.mention}.")


async def setup(bot):
    await bot.add_cog(ReactionRoles(bot))
