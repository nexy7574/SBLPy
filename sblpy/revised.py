__version__ = "1.0.1"
__verified__= False

import asyncio
import logging
import traceback
from datetime import timedelta, datetime

import aiohttp
import discord
import fastapi
import typing
import uvicorn
import os
import json

from discord.ext import commands
from pydantic import BaseModel

from . import errors

_VARS = {
    "client": None,
    "ignore_intents": False
}

def set_vars(**kwargs):
    """
    Sets the variables to be used by the HTTPClient and webserver. This should only be called by :class:Client.

    :param kwargs: the (key, value) pairs to update the dict with.
    :return: the variables object
    """
    _VARS.update(kwargs)
    return _VARS

def get_vars(*keys):
    """
    Returns a single/list of values for each variable.

    :param keys: the keys to request. usually "bot", "function" and "cooldown".
    :return:
    """
    ret = []
    for key in keys:
        ret.append(_VARS.get(key.lower()))
    if len(ret) == 1:
        ret = ret[0]
    elif not ret:
        return None
    return ret

def get_var(key):
    """get_vars, but only ever returns one."""
    var = get_vars(key)
    if isinstance(var, list):
        var = var[0]
    return var

app = fastapi.FastAPI()

class BumpRequest(BaseModel):
    type: typing.Optional[str] = "REQUEST"  # REQUEST
    guild: str
    channel: str
    user: str


class ErrorCode:
    # MISSING_SETUP = 0
    # COOLDOWN = 1
    # AUTOBUMP = 2
    # NOT_FOUND = 3
    # OTHER = 4
    def __init__(self, code: str):
        code = code.upper()
        self.MISSING_SETUP = code == "MISSING_SETUP"
        self.COOLDOWN = code == "COOLDOWN"
        self.AUTOBUMP = code == "AUTOBUMP"
        self.NOT_FOUND = code == "NOT_FOUND"
        self.OTHER = code == "OTHER"


class MappedBumpRequest:
    """The Mapped BumpRequest.

    This class contains integers instead of snowflakes, or resolved object if bot is passed.
    ---
    values:
    - guild: Union[discord.Guild, int]
    - channel: Union[discord.TextChannel, int]
    - member: Union[discord.Member, None]
    - user: Union[discord.User, int]"""
    __slots__ = ("type", "guild", "channel", "member", "user", "_intentblocked")

    def __init__(self, raw: BumpRequest, bot: commands.Bot = None):
        # 0.9.3 - Check intents for upcoming discord.py
        if discord.version_info.minor >= 5 and not _VARS["ignore_intents"]:
            intents = bot.intents  # 1.0.0 - Change to the public interface
            if not intents.members:
                logging.warning("Members intent is required to get the author (member) instance for bump requests.\n"
                                "If you `sblpy.setvar('ignore_intents', True)`, you can suppress this warning.")
                self._intentblocked = True
        else:
            self._intentblocked = False
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
            
    @property
    def valid(self):
        """Returns True if this is a valid request.

        A valid request is when the channel, user and guild are all resolved.

        NOTE::
            if the member is not resolved, but was not blocked by intents(*), this will return false.
            if the member couldn't be resolved (because of intents(*)), this will still be valid (assuming other)"""
        if self._intentblocked:
            return self.channel and self.user and self.guild
        else:
            return self.channel and self.user and self.guild and self.member

    @valid.setter
    def valid(self, value):
        raise AttributeError("self.valid can not be set")
    
    async def send(self, *args, **kwargs):
        """Sends a message to source channel"""
        if not isinstance(self.channel, discord.TextChannel):
            raise TypeError("Expected value TextChannel for self.channel, got int")
        if not self.channel.permissions_for(self.guild.me).send_messages:
            raise commands.BotMissingPermissions("send_messages")  # 0.9.2  - fixed missing import
        return await self.channel.send(*args, **kwargs)

    def __getattr__(self, name):  # 1.0.0 - Enable dynamic attributes
        # NOTE: this may be removed in the future if the behavior becomes too ambiguous or messy.
        name = name.lower()
        return getattr(self.guild,name,None) or getattr(self.channel,name,None) or \
               getattr(self.member if self.member else self.user,name)


class BumpFinishedResponse:
    """This is what is returned when a REQUEST finishes, successfully."""
    __slots__ = ("response", "nextBump", "message", "amount")
    def __init__(self, *, response: str, nextBump: int, message: str = None, amount: int = None):
        self.response = int(response)
        self.nextBump = datetime.utcnow() + timedelta(seconds=nextBump//1000)
        self.message = message or ""
        self.amount = amount or -1


class BumpErrorResponse:
    """This is returned when a REQUEST fails (server-side)."""
    __slots__ = ("response", "code", "nextBump", "message")
    def __init__(self, *, response: str, code: str, nextBump: int = None, message: str):
        self.response = int(response)
        self.code = ErrorCode(code)
        self.nextBump = (datetime.utcnow() + timedelta(seconds=nextBump//1000)) if nextBump else datetime.utcnow()
        self.message = message or "No Error Message Specified."


class Client:
    def __init__(self, bot, bump_function: typing.Union[callable, str], *, bump_cooldown: int = 3600,
                 require_authentication: bool = True, auth_config_path: str = None):
        if os.path.exists("./.sblpy/auth_config.json") and not auth_config_path:
            auth_config_path = "./.sblpy/auth_config.json"  # 1.0.0 - Automated detection of config files
        self.ready = False
        self.server = None
        self.config = None
        self.task = None
        self.tasks = []
        self.func = bump_function
        self.bot = bot
        self.cooldown = bump_cooldown
        set_vars(client=self)
        self.auth = None
        self.require_auth = require_authentication
        self.auth_path = auth_config_path

        if not auth_config_path and require_authentication:
            logging.critical("Auth config for SBLPY has been enabled, yet no auth config file was provided.\n"
                            "Please ensure you load it later down the line with client.load_config.")
        if self.auth_path:
            self.load_config(self.auth_path)  # 0.9.3 - fixed config not autoloading

        self.on_cooldown = {}

    async def _handle_cooldown(self, channel):
        self.on_cooldown[channel] = self.cooldown
        while self.on_cooldown.get(channel,0):
            await asyncio.sleep(1)
            self.on_cooldown[channel] -= 1
        try:
            del self.on_cooldown[channel]
        except KeyError:
            pass

    def __del__(self):  # 0.9.3 - Stop port overflow by not actually closing
        if self.tasks or self.task:
            logging.warning("Don't forget to close the server before destroying a client instance.")
            [x.cancel() for x in self.tasks]
            self.task.cancel()

    def init_server(self, host: str = "127.0.0.1", port: int = 1234, *, reload_on_file_edit: bool = False):
        """Initializes the internal server for use"""
        if self.ready:
            raise errors.StateException(True, False, message="Server is already initialized")
        self.config = uvicorn.Config(
            app,
            host,
            port,
            use_colors=False,
            reload=reload_on_file_edit
        )
        self.server = uvicorn.Server(self.config)
        self.ready = True
        return True

    def start_server(self):
        """Starts the internal server, allowing incoming requests."""
        if self.task:
            raise errors.StateException(
                True,
                False,
                message=f"Internal task is already running. Try `Client.stop_server()` first."
            )
        elif not self.ready:
            raise errors.StateException(
                False,
                True,
                message=f"Server is not initialized!"
            )
        if not self.auth and self.require_auth:
            logging.warning("SBLPy has been told to require authentication when receiving requests, but no authentication"
                            " configuration has been set nor loaded. Please see Client.load_config, or Client."
                            "add_auth(url,auth_password).")
        self.task = asyncio.get_event_loop().create_task(self.server.serve())
        # self.task = asyncio.create_task(self.server.serve())
        return True

    def stop_server(self):
        """Stops the internal server, denying incoming requests."""
        if not self.task:
            raise errors.StateException(
                False,
                True,
                message=f"Internal task is not already running. Try `Client.start_server()` first."
            )
        try:
            self.task.cancel()
        except:
            pass
        finally:
            return True

    async def _parse_function(self):
        if isinstance(self.func, str):
            func = self.bot.get_command(self.func.lower())
            if not self.func:
                raise TypeError(f"Command '{self.func}' doesn't exist. Unable to retrieve callback.")
            return func.callback
        else:
            return self.func

    async def request(self, req: fastapi.Request, body: BumpRequest):
        """The internal function. DO NOT CALL THIS!"""
        if not self.bot.is_ready():  # 1.0.1 - Added waiting
            return fastapi.responses.JSONResponse(
                {
                    "status": 503,
                    "message": "Bot is not ready yet. Try again in a few seconds.",
                    "success": False
                },
                503
            )
        if self.require_auth:  # 0.9.2: authentication added
            # 1.0.0 - Fix `id` typo
            if not self.auth:
                logging.critical("Require authentication is enabled but no authentication exists. Forced to reject incoming"
                                 " SBLP request.")
                self.bot.dispatch("sblp_request_rejected", ip=req.client.host, reason="Auth error (see logs/console)")
                return fastapi.responses.JSONResponse(
                    {
                        "status": 501,
                        "message": "Authentication is enabled but has not been loaded. unable to proceed with request.",
                        "success": False
                    },
                    501
                )
            elif not req.headers.get("Authorization"):
                self.bot.dispatch("sblp_request_rejected", ip=req.client.host, reason="Invalid Auth Header - not provided")
                return fastapi.responses.JSONResponse(
                    {
                        "status": 401,
                        "message": "Please provide an authentication header",
                        "success": False
                    },
                    401
                )
            else:
                token = req.headers["Authorization"]
                if token.startswith("Bearer "):
                    token = token[7:]
                for key, value in self.auth.items():
                    if value == token:
                        break
                else:
                    self.bot.dispatch("sblp_request_rejected", ip=req.client.host, reason="Invalid Auth Token")
                    return fastapi.responses.JSONResponse(
                        {
                            "status": 401,
                            "message": "Please provide a valid authentication header",
                            "success": False
                        },
                        401
                    )
        body = MappedBumpRequest(body, self.bot)
        if body.channel in self.on_cooldown.keys():
            return fastapi.responses.JSONResponse(
                {
                    "status": 429,
                    "message": "On cooldown!",
                    "success": False,
                    "code": "COOLDOWN",
                    "nextBump": self.on_cooldown[body.channel]
                }
            )
        else:
            self.bot.loop.create_task(self._handle_cooldown(body.channel))
        self.bot.dispatch("sblp_request_start", body)
        try:
            res = await discord.utils.maybe_coroutine(await self._parse_function(), body=body, bot=self.bot)
        except Exception as e:
            self.bot.dispatch("sblp_request_failed", body=body, error=e)
            traceback.print_exc()
            return fastapi.responses.JSONResponse(
                {
                    "type": "ERROR",
                    "code": "OTHER",
                    "message": f"Internal Error: {e}"
                },
                500
            )
        else:
            if isinstance(res, int):
                bumped_to = res
            else:
                bumped_to = -1
            self.bot.dispatch("sblp_request_finished", body)
            return fastapi.responses.JSONResponse(
                {
                    "type": "FINISHED",
                    "response": "0",
                    "amount": bumped_to,
                    "nextBump": self.cooldown  # 0.9.3 - Fixed false return Type. || unfixed
                },
                200
            )

    def load_config(self, path: str = None):
        """Loads the configuration file that stores authentication information.

        the config file is a dictionary of {hostname: token} pairs.
        e.g:
        JSON::
        {
            "foo": "bar",
            "barn": "Boop"
        }"""
        path = path or self.auth_path
        if not path:
            raise TypeError("No authentication config file path was provided.")
        try:
            with open(path) as rfile:
                data = json.load(rfile)
            self.auth_path = path
        except FileNotFoundError as e:
            raise FileNotFoundError(f"Configuration file {path} doesn't exist.") from e
        except json.JSONDecodeError as e:
            raise errors.JSONLoadError(path) from e
        else:
            self.auth = data
            return data

    def add_auth(self, url: str, token: str):
        """
        Adds an authentication pair to the configuration.

        :param url: the SBLP slug that this token belongs to
        :param token: the authentication token for a specific bot
        :return:
        """
        if not self.auth:
            self.auth = {}
        if not self.auth_path:
            logging.warning("add_auth called, however no configuration file to save to. This will need to be called every"
                            " time you plan to create this class.")
        else:
            with open(self.auth_path, "w+") as wfile:
                json.dump(self.auth, wfile, ensure_ascii=True)
        self.auth[url] = token
        logging.debug(f"Added token {token} to auth config, with slug {url}")
        return self.auth

@app.post("/sblp/request")
async def sblp_request(req: fastapi.Request, body: BumpRequest):
    # 0.9.2 - Added actual handling
    logging.debug(f"Received SBLP request from {req.client.host}")
    client: Client = get_var("client")
    if client is None:
        raise errors.StateException(False, True, message="Client doesn't even exist yet. How tf did you get here?")
    else:
        logging.debug(f"Calling client.request on {id(client)} for {req.client.host}")
        if (t:=str(req.headers.get("maxwait","60"))) and t.isdigit():  # 0.9.3 - set default to 60 seconds
            task = asyncio.create_task(client.request(req, body))
            try:
                result = await asyncio.wait_for(task, timeout=float(t))
            except asyncio.TimeoutError:
                task.cancel(f"Timeout of {t} requested by the client was exceeded.")
                return fastapi.responses.PlainTextResponse(f"Timeout of {t} seconds was exceeded internally, "
                                                           f"and as such the task has been cancelled.", 504)
        else:
            result = await client.request(req, body)
        # updated in 0.9.2: I forgot to actually return the object.
        if req.headers.get("Accept", "application/json") == "application/json":
            if not isinstance(result, fastapi.responses.JSONResponse):
                return fastapi.responses.JSONResponse(
                    {
                        "status": 417,
                        "message": "Response type could not be converted to application/json",
                        "success": False
                    },
                    417
                )
        return result

async def _send(session,url,payload,headers):
    async with session.post(url, daya=payload, headers=headers) as resp:
        try:
            data = await resp.json()
        except Exception as e:
            logging.error(f"Error while decoding response from {url}: {e}. Status code: {resp.status}")
            return False  # instead of re-raising the error
        else:
            if data["type"].upper() == "FINISHED":
                return BumpFinishedResponse(**data)
            else:
                return BumpErrorResponse(**data)

async def new_request(ctx,*urls: str,token:str):
    """
    Makes a request to all the other bump bots on SBLP.


    :param token: your bot's SBLP token
    :param urls: A list of URLs to request.
    :param ctx: the context

    This is an async iterator. This means you have to `async for x in new_request(...)`.
    """
    # 1.0.0 - Request function
    bot = get_var("client").bot
    payload = {
        "type": "REQUEST",
        "guild": str(ctx.guild.id),
        "channel": str(ctx.channel.id),
        "user": str(ctx.author.id)
    }
    headers = {
        "User-Agent": f"SBLPy/{__version__} discord.py/{discord.__version__} {bot.user.name}",
        "maxwait": get_var("timeout") or "60",
        "Accept": "application/json",
        "Authorization": token,
        "Content-Type": "application/json",
        # "Content-Length": len(payload)
    }
    session = aiohttp.ClientSession()
    for url in urls:
        if not url.startswith("http"):
            url = "http://" + url
        logging.debug(f"sending POST request to {url} with data {payload}")
        yield await asyncio.wait_for(_send(session,url,payload,headers), float(headers["maxwait"]))
