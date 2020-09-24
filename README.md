# SBLPy
Your favourite, and only, python wrapper for the "very popular" 
[sblp](https://example.com).


## Features:
* Simple [setup](#Setup)
* Fast and low-power HTTP server, that won't slow your bot down
* Easy to use `request` functions, making starting SBLP requests easier
* Automatic handling of inputted data
* probably more

## Setup
This module doesn't need the bot to be ready when you initilise it.

```python
from discord.ext import commands
from sblpy import revised

async def handler(bumpBody):
    # For simplicity, we'll define the bump function here. DONT DO THIS!
    async def bump(ctx=None, **kwargs):
        ctx = ctx or kwargs.get("body")
        ...  # do bumping stuff
        return len(bumped_to)  # can return anything
    return await bump(body=bumpBody)

bot = commands.Bot(...)  # can be AutoSharded
bot.sblp = revised.Client(
    bot, 
    handler,  # Note that this function doesn't get a context object.
    bump_cooldown=3600  # how many seconds between each bump
)
```
### Starting and Stopping the server
Carrying on from the last example,
```python
bot.sblp.init_server()  # you can change the open port via port=1234
bot.sblp.start_server() # close the server
bot.sblp.stop_server()
```

### Functionless Clients
since SBLPy also uses commands.Bot's `dispatch` system, you don't *technically* need to provide a function when creating Client().

In client:
```python
body = MappedBumpRequest(body, self.bot)
self.bot.dispatch("sblp_request_start", body)
```

elsewhere:
```python
@commands.Cog.listener()
async def on_sblp_request_start(body):
    ...
```

![request.mp4](https://github.com/EEKIM10/SBLPy/blob/master/.assets/request.mp4)

------------------------------
## Changelog
0.9.3:
* In-built cooldown handling
* Fixed some invalid return codes
* Fixed authentication not auto-loading
* ~~made the module complain~~ cancel all the running tasks if you destroy the instance
* bugfixes
* future-proofing for intents
* Added `valid` property to `MappedBumpRequest` so you can verify if a request is valid or not

0.9.2:

* Changelog started
* Added basic authentication support
* bugfixes
