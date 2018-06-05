import os
import json
import logging
import tempfile

import asyncio
import aiohttp
import socket

from .tracks import TrackAndTagGetter
from .convert import FrameConverter
from .grabber import FrameGrabber
from .audioanalyzer import AudioAnalyzer

class Analyzer:
    def __init__(self, _id, uri):
        self._id = _id
        self.uri = uri
        
        self.tracks = []
        self.metadata = {}
        self.duration = None
        
        self.poster_sample = None
    
    def progress(self, amount):
        print(json.dumps({'progress': amount}))
    
    async def download(self, uri, filename, force_ipv4 = False):
        if force_ipv4:
            conn = aiohttp.TCPConnector(family = socket.AF_INET)
        else:
            conn = aiohttp.TCPConnector()
        
        async with aiohttp.ClientSession(connector = conn) as session:
            async with session.get(uri) as resp:
                with open(filename, 'wb') as fd:
                    while True:
                        chunk = await resp.content.read(10**6)
                        if not chunk:
                            break
                        fd.write(chunk)

    async def analyze(self):
        self.progress(0)
        
        logging.info('Downloading URI {} to cache'.format(self.uri))
        _dir = tempfile.gettempdir()

        ext = self.uri.split('.')[-1]
        filename = os.path.join(_dir, '{}.{}'.format(self._id, ext))
        
        try:
            await self.download(self.uri, filename)
        except aiohttp.client_exceptions.ClientConnectorError:
            await self.download(self.uri, filename, force_ipv4 = True)
        
        self.uri = 'file://{}'.format(filename)
        
        logging.info('Finished downloading URI to cache')
        
        self.progress(1 / 4)
        
        logging.info('Starting analysis of URI {}'.format(self.uri))
        
        self.tracks, self.metadata, self.duration, self.poster_sample = \
            await TrackAndTagGetter(self.uri).go()
        
        logging.info('Completed track and tag analysis')
        
        self.progress(2 / 4)
        
        # Start audio analysis
        audiofuture = None
        if any(t['type'] == 'audio' for t in self.tracks) and \
           (self.metadata.get('replaygain') == None or self.metadata.get('bpm') == None):
            aa = AudioAnalyzer(self.uri, self.duration)
            audiofuture = asyncio.ensure_future(aa.analyze())
            logging.info('Starting audio analysis job')

        # If metadata didn't contain a tag image, grab a frame from a video or image track if available
        posterfuture = None
        if not self.poster_sample:
            best = None
            
            # Overly-complex track-selection logic on the tiny chance it gets fed media files with multiple video and image tracks
            for track in self.tracks:
                if track['type'] == 'video':
                    if not best: best = track                        
                    elif best['type'] == 'image': best = track
                    elif best['type'] == 'video':
                        if track['width'] * track['height'] > best['width'] * best['height']:
                            best = track
                
                elif track['type'] == 'image':
                    if not best: best = track                        
                    elif best['type'] == 'image' and track['width'] * track['height'] > \
                                                     best['width']  * best['height']:
                        best = track
            
            if best:
                logging.info('Starting poster frame grab job')
                if best['type'] == 'video':
                    # Grab a frame one-tenth of the way through the track
                    pt = self.duration * 0.1
                elif best['type'] == 'image':
                    # Grab the first frame 'cause that's the only one. Duh.
                    pt = 0
                
                grabber = FrameGrabber(self.uri, best['caps'], pt)
                posterfuture = asyncio.ensure_future(grabber.grab())
                

        # Wait for audio tagging to complete, if in progress
        if audiofuture:
            replaygain, bpm = await audiofuture
            
            if self.metadata.get('replaygain') == None:
                self.metadata['replaygain'] = replaygain
            
            if self.metadata.get('bpm') == None:
                self.metadata['bpm'] = bpm
            
            logging.info('Audio analysis complete')

        # Wait for frame grabbing to complete, if in progress
        if posterfuture:
            self.poster_sample = await posterfuture
            logging.info('Poster frame grabbing complete')
        
        self.progress(3 / 4)
        
        if self.poster_sample:
            logging.info('Starting poster and thumb frame convert and save jobs')
            
            postertitle = '{}.poster.jpg'.format(self._id)
            posterpath = os.path.join(_dir, postertitle)
            pfc = FrameConverter(self.poster_sample, posterpath, max_width = 1920)
            pfuture = asyncio.ensure_future(pfc.convert())

            thumbtitle = '{}.thumb.jpg'.format(self._id)
            thumbpath = os.path.join(_dir, thumbtitle)
            tfc = FrameConverter(self.poster_sample, thumbpath, width = 256)
            tfuture = asyncio.ensure_future(tfc.convert())
            
            await pfuture, tfuture
            logging.info('Created poster and thumbnail images')
        else:
            posterpath = ''
            thumbpath = ''
                
        logging.info('Finished analysis of URI {}'.format(self.uri))
        
        self.progress(4 / 4)
        
        _type = 'invalid'

        for track in self.tracks:
            del track['caps']
            
            # Determine the media type based on track types
            if track['type'] == 'video':
                _type = 'video'
            elif track['type'] == 'audio' and not _type == 'video':
                _type = 'audio'
            elif track['type'] == 'image' and not _type == 'video' and not _type == 'audio':
                _type = 'image'
        
        # Delete cached file
        os.unlink(filename)
        
        return json.dumps({
            'result': {
                'type': _type,
                'tracks': self.tracks,
                'metadata': self.metadata,
                'poster': posterpath,
                'thumb': thumbpath
            }
        })
