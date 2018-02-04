import json
import asyncio
import websockets

import gbulb
gbulb.install()

import tempfile

import logging
logging.basicConfig(level = logging.DEBUG)

# TODO command-line args, config file, etc.

import analyze

class MediaServer:
    def __init__(self):
        self.websocket = None
        self.tempdir = tempfile.mkdtemp(prefix = 'cedarmediaserver')
                
    def start(self):
        asyncio.run_until_complete(self.socket_loop())

    async def socket_loop(self):
        while True:
            async with websockets.connect('ws://localhost:3003') as websocket:
                self.websocket = None
                
                try:
                    await self.handle_recv(websocket)
                except websockets.ConnectionClosed:
                    # Do magic to handle running while not connected, cache processing results and submit after reconect
                    continue
    
    async def handle_recv(self, websocket):
        while True:
            msg = await websocket.recv()
            
    def send(self, msg):
        if (not type(msg) is str):
            msg = json.dumps(msg)
        
        asyncio.ensure_future(self.websocket.send(msg))

if __name__ == '__main__':
    mediaserver = MediaServer()
    mediaserver.start()
