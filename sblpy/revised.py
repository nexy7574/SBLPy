__version__ = "0.0.1"
__verified__= False

import asyncio
import logging
import time
import traceback

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

app = fastapi.FastAPI()

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
    __slots__ = ("type", "guild", "channel", "member", "user", "_intentblocked")

    def __init__(self, raw: BumpRequest, bot: commands.Bot = None):
        # 0.9.3 - Check intents for upcoming discord.py
        if discord.version_info.minor >= 5 and not _VARS["ignore_intents"]:
            intents = bot._connection._intents
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

    def __getattr__(self, name):
        return getattr(self.guild, name.lower(), None) or getattr(self.channel, )


class Client:
    def __init__(self, bot, bump_function: typing.Union[callable, str], *, bump_cooldown: int = 3600,
                 require_authentication: bool = True, auth_config_path: str = None):
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
        await asyncio.sleep(self.cooldown)
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

    def __verify_config_file(self):
        pass

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
        if self.require_auth:  # 0.9.2: authentication added
            if not self.auth:
                logging.critical("Require authentication is enabled but no authentication exists. Forced to reject incoming"
                                 " SBLP request.")
                self.bot.dispatch("sblp_request_rejected", id=req.client.host, reason="Auth error (see logs/console)")
                return fastapi.responses.JSONResponse(
                    {
                        "status": 501,
                        "message": "Authentication is enabled but has not been loaded. unable to proceed with request.",
                        "success": False
                    },
                    501
                )
            elif not req.headers.get("Authorization"):
                self.bot.dispatch("sblp_request_rejected", id=req.client.host, reason="Invalid Auth Header - not provided")
                return fastapi.responses.JSONResponse(
                    {
                        "status": 401,
                        "message": "Please provide an authentication header",
                        "success": False
                    },
                    401
                )
            else:
                for key, value in self.auth.items():
                    if value == req.headers["Authorization"]:
                        break
                else:
                    self.bot.dispatch("sblp_request_rejected", id=req.client.host, reason="Invalid Auth Token")
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
    client: Client = get_vars("client", "")[0]   # FIXME: linter is telling me to kill myself because this is apparently always None.
    if client is None:
        raise errors.StateException(False, True, message="Client doesn't even exist yet. How tf did you get here?")
    else:
        logging.debug(f"Calling client.request on {id(client)} for {req.client.host}")
        if (t:=req.headers.get("maxwait"), "60") and t.isdigit():  # 0.9.3 - set default to 60 seconds
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
