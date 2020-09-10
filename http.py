import asyncio
import traceback

import fastapi
from pydantic import BaseModel
import discord
from discord.ext.commands import Bot
import uvicorn
import sys
__version__ = "0.1.0"


class BumpRequest(BaseModel):
    type: str  # REQUEST
    guild: str
    channel: str
    user: str

class MappedBumpRequest:
    """The Mapped BumpRequest.

    This class contains integers instead of snowflakes, or resolved object if bot is passed.
    ---
    values:
    - guild: Union[discord.Guild, int]
    - channel: Union[discord.TextChannel, int]
    - member: Union[discord.Member, None]
    - user: Union[discord.User, int]"""
    __slots__ = ("type", "guild", "channel", "member", "user")
    def __init__(self, raw: BumpRequest, bot = None):
        self.type = "REQUEST"
        self.guild = int(raw.guild)
        self.channel = int(raw.channel)
        self.user = int(raw.user)
        if bot:
            self.guild = bot.get_guild(self.guild) or self.guild  # or for fallbacks.
            self.channel = bot.get_channel(self.channel) or self.channel
            self.user = bot.get_user or self.channel
        if isinstance(self.guild, discord.Guild):
            self.member = self.guild.get_member(int(raw.user))
        else:
            self.member = None


app = fastapi.FastAPI()


class SBLP:
    """The actual SBLP client.

    To start:
    ..codeblock::
        bot = commands.[AutoSharded]Bot(...)
        bot.loop.create_task(sblp.SBLP(bot, <bump_function>, <bump_cooldown_in_ms>).start())"""
    def __init__(self, bot, bump_function: callable, cooldown: int = -1, *, ip: str = "127.0.0.1",
                 port: int = 8080, debug: bool = False):
        """
        Inits the class

        :param bot: the commands.[AutoSharded]Bot
        :param bump_function: the function to run when there's a bump request. must be a coroutine.
        :param cooldown: the number of ms (int) per cooldown. Defaults to whatever the bump func returns.
        :param ip: the IP address to serve from. defaults to localhost
        :param port: the port to serve. Defaults to 8080
        :param debug: whether to enable debug messages.

        .. Note:
            bump_function can be an alias that calls your main function, or just handles it differently.
            remember, all the bump function gets sent is the guild, member, channel and the MappedBumpRequest body.
        """
        self.bot = bot
        if not asyncio.iscoroutinefunction(bump_function):
            raise TypeError("Bump Function is not a coroutine function.")
        self.function = bump_function
        self.ip = ip
        self.port = port
        self.debug = debug
        app.__init__(version=__version__, debug=self.debug)
        self.server = uvicorn.Server(uvicorn.Config(app, self.ip, self.port, debug=self.debug))
        self.cooldown = cooldown

    def _log(self, message):
        if self.debug:
            print(f"[SBLP HTTP] {message}")

    async def start(self):
        self._log("SBLPHTTP server starting up...")
        try:
            await self.server.serve()
        except Exception as e:
            print(f"Error starting HTTP server: {e}", file=sys.stderr)
        else:
            self._log("Server successfully started.")

    async def kill(self):
        await self.server.shutdown()

    @app.post("/sblp/request")
    async def incoming(self, req: fastapi.Request, body: BumpRequest):
        self._log(f"New request from somewered")
        body = MappedBumpRequest(body, self.bot)
        self.bot.dispatch("sblp_bump", body.guild, body.member or body.user, body.channel, body)
        try:
            await self.function(body.guild, body.member or body.user, body.channel, body)
        except:
            traceback.print_exc()
            return dict(
                type="ERROR",
                response=body.channel.id,
                code="other",
                nextBump=0,
                message=f"Internal error while running bump function. Contact {(await self.bot.application_info()).owner}",
                status=500,
                sucess=False
            )
        else:
            return dict(
                type="FINISHED",
                response=body.channel.id,
                nextBump=self.cooldown,
                message=f"{self.bot.user} Has successfully bumped."
            )
