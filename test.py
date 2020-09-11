from sblpy import SBLP
from discord.ext import commands

bot = commands.Bot("sy!")
async def bump(*args, **kwargs):
    print(*args, **kwargs)
    return 200
bot.sblp = SBLP(
    bot,
    bump,
    10,
    debug=True
)
bot.loop.create_task(bot.sblp.start())

bot.run("NjI1NzM4ODYxNDMxMjI2NDA4.XYj6ug.tV1IS0R4dr_IqTXavRhkyk39YtU")