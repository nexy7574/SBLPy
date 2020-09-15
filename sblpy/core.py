import asyncio
import sys
import traceback

import aiohttp
import discord
import fastapi
import uvicorn
from discord.ext import commands
from pydantic import BaseModel
import logging

__version__ = "0.1.0"
__verified__= False

_VARS = {
    "bot": None,
    "function": None,
    "cooldown": None
}


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
_DEFAULT = [
    "openbump.bot.discord.one",
    "pysbump.bot.discord.one",
    "dpgbump.bot.discord.one"
]
# these need to be updated manually.

async def construct_fake_context(bot, channel: discord.TextChannel, author):
    for msg in channel.history(limit=2):
        ctx = await bot.get_context(msg)
        ctx.command = bot.get_command("bump")
        ctx.author = author
        break

    return ctx  # says undefined, fuck off pycharm it IS defined.


class SBLP:
    """The actual SBLP client.

    To start:
    ..codeblock::
        bot = commands.[AutoSharded]Bot(...)
        bot.loop.create_task(sblp.SBLP(bot, <bump_function>, <bump_cooldown_in_ms>, auth_token="...").start())"""

    def __init__(
            self,
            bot,
            bump_function: callable,
            cooldown: int = -1, *,
            ip: str = "127.0.0.1",
            port: int = 8080,
            debug: bool = False,
            auth_token: str="No authorization token.",
            slugs: list = None
    ):
        """
        Inits the class

        :param bot: the commands.[AutoSharded]Bot
        :param bump_function: the function to run when there's a bump request. must be a coroutine.
        :param cooldown: the number of ms (int) per cooldown. Defaults to whatever the bump func returns.
        :param ip: the IP address to serve from. defaults to localhost
        :param port: the port to serve. Defaults to 8080
        :param debug: whether to enable debug messages.
        :param auth_token: The authorization needed for sending START requests

        .. Note:
            bump_function can be an alias that calls your main function, or just handles it differently.
            remember, all the bump function gets sent is the guild, member, channel and the MappedBumpRequest body.
        """
        if not __verified__:logging.warning("You are using an un-tested raw version of SBLPy. Production use is not advised.")
        if not slugs:
            slugs = _DEFAULT
        self.bot = bot
        if not asyncio.iscoroutinefunction(bump_function):
            raise TypeError("Bump Function is not a coroutine function.")
        self.function = bump_function
        self.ip = ip
        self.port = port
        self.debug = debug
        self.server = uvicorn.Server(uvicorn.Config(app, self.ip, self.port, debug=self.debug))
        self.cooldown = cooldown
        self.auth = auth_token
        self.slugs = slugs
        _VARS["bot"] = bot
        _VARS["cooldown"] = cooldown
        _VARS["function"] = bump_function

    def _log(self, message, level: str = "info"):
        levels = {"debug": logging.debug, "info": logging.info, "warning": logging.warning, "error": logging.error,
                  "critical": logging.critical}
        if self.debug:
            levels.get(level, logging.info)(f"[SBLP HTTP] {message.capitalize()}")

    async def start(self):
        self._log("server starting up...")
        try:
            await self.server.serve()
        except Exception as e:
            print(f"Error starting HTTP server: {e}", file=sys.stderr)
        else:
            self._log("Server successfully started.")

    async def kill(self):
        self._log("Stopping server...")
        await self.server.shutdown()
        self._log("Stopped server.")

    @app.get("/")
    @app.get("/sblp")
    async def test(self=None):  # ffs fastapi
        pass

    async def request(self, ctx: commands.Context=None, *, guild: discord.Guild = None,
                      channel: discord.TextChannel = None, user: discord.Member = None):
        if not ctx and not all((guild, channel, user)):
            raise TypeError("Request is missing all arguments. If ctx is not provided, guild, channel and user kwargs"
                            " must.")
        elif ctx and any((guild, channel, user)):
            raise TypeError("Request got ctx AND kwargs. If ctx is provided, guild, channel and user must not.")

        guild = guild or ctx.guild
        channel = channel or ctx.channel
        user = user or ctx.author
        session = getattr(self.bot, "session", None)

        if not session:
            self._log("Bot has no session attribute, creating new one for single request.", "debug")
            session, ours = aiohttp.ClientSession(), True
        else:
            ours = False
        payload = dict(
            type="REQUEST",
            guild=str(guild.id),
            channel=str(channel.id),
            user=str(user.id)
        )
        if ctx:
            payload["message"] = str(ctx.message.id)
        headers = dict(
            Authorization=self.auth,
            User_Agent=f"DiscordBot {self.bot.user}/{self.bot.user.id} SBLP HTTP via SBLPy v{__version__} "
                       f"aiohttp/{aiohttp.__version__} discord.py/{discord.__version__}"
        )
        for slug in self.slugs:
            self.bot.dispatch("sblp_request_start", slug)
            async with session.get("https://" + slug + "/request", daya=payload) as response:
                self._log(f"Got status {response.status} on slug {slug}")
                self.bot.dispatch("sblp_request_done", response)

    @app.post("/sblp/request")
    async def incoming(self, req: fastapi.Request, body: BumpRequest):
        raise NotImplementedError
        # auth = req.headers.get("Authorization", "")
        # if auth == "":  # self.auth:  # TODO: real authorization
        #     return fastapi.responses.JSONResponse(None, 401)  # returns a 401 unauthenticated response
        #
        # logging.warning(f"Presuming request w/ auth {auth} is legit")
        #
        # bot, function, cooldown = 0,0,0
        #
        # body = MappedBumpRequest(body, bot)
        # if not isinstance(body.guild, discord.Guild):
        #     return fastapi.responses.JSONResponse(dict(status=400, type="ERROR", ), 400)
        # bot.dispatch("sblp_bump", body.guild, body.member or body.user, body.channel, body)
        # try:
        #     await function(body.guild, body.member or body.user, body.channel, body)
        # except:
        #     traceback.print_exc()
        #     return dict(
        #         type="ERROR",
        #         response=body.channel.id,
        #         code="other",
        #         nextBump=0,
        #         message=f"Internal error while running bump function. Contact {(await self.bot.application_info()).owner}",
        #         status=500,
        #         sucess=False
        #     )
        # else:
        #     return dict(
        #         type="FINISHED",
        #         response=body.channel.id,
        #         nextBump=cooldown,
        #         message=f"{bot.user} Has successfully bumped."
        #     )



__all__ = ("MappedBumpRequest", "SBLP", "__version__")