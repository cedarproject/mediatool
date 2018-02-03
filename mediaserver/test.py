import asyncio
import sys
from analyze import Analyzer

import logging
logging.basicConfig(level = logging.DEBUG)

for f in sys.argv[1:]:
    if not '://' in f: f = 'file://{}'.format(f)
    a = Analyzer('blah', f, None)

asyncio.get_event_loop().run_until_complete(a.analyze())
