import asyncio
import sys

import logging
#logging.basicConfig(level = logging.DEBUG)

from .analyze import Analyzer

import gbulb
gbulb.install()

async def go():
    a = Analyzer(sys.argv[1], sys.argv[2])
    print(await a.analyze())

asyncio.get_event_loop().run_until_complete(go())
