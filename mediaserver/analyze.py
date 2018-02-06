import os
import json
import logging
import asyncio

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)

from .convert import FrameConverter
from .grabber import FrameGrabber
from .audioanalyzer import AudioAnalyzer

from .taglist_utils import create_taglist_getters

class Analyzer:
    # TODO this should be split into two classes, one that does all the management stuff in analyze() and one to do the inital tracks data grab.
    def __init__(self, directory, filename):
        self.directory = directory
        self.filename = filename
        self.uri = 'file:///{}/{}'.format(directory, filename)
        
        self.tracks = []
        self.metadata = {}
        
        self.tracks_done = False
        self.duration = None
        self.poster_sample = None
        
        self.pipeline = Gst.Pipeline.new()
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self.on_message)
        
        self.decodebin = Gst.ElementFactory.make('uridecodebin')
        self.decodebin.connect('pad-added', self.pad_added)
        self.decodebin.connect('no-more-pads', self.no_more_pads)
        self.decodebin.set_property('uri', self.uri)
        
        self.pipeline.add(self.decodebin)
    
    def progress(self, amount):
        print({'progress': amount})
        
    async def analyze(self):
        # TODO handle invalid files properly
        self.progress(0)
        
        logging.info('Starting analysis of URI {}'.format(self.uri))
        self.pipeline.set_state(Gst.State.PLAYING)
        
        # Use decodebin to determine the media container's contents and get any metadata
        while self.duration == None or not self.tracks_done:
            res, state, pending = self.pipeline.get_state(10)
            if self.duration == None and res == Gst.StateChangeReturn.SUCCESS and \
                   (state == Gst.State.PAUSED or state == Gst.State.PLAYING):               
                res, duration = self.decodebin.query_duration(Gst.Format.TIME)
                if res:
                    logging.info('Media duration is {}'.format(duration))
                    self.duration = duration
                else:
                    self.duration = 0
            
            await asyncio.sleep(0.25)
        
        self.pipeline.set_state(Gst.State.NULL)
        logging.info('Completed track and tag analysis')
        
        self.progress(1 / 3)
        
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
            # TODO actually do thing
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
        
        self.progress(2 / 3)
        
        if self.poster_sample:
            logging.info('Starting poster and thumb frame convert and save jobs')
            # TODO get directory to stuff these info from command-line args or whatever
            postertitle = '{}.poster.jpg'.format(self.filename)
            posterpath = os.path.join(self.directory, postertitle)
            pfc = FrameConverter(self.poster_sample, posterpath, max_width = 1920)
            pfuture = asyncio.ensure_future(pfc.convert())

            thumbtitle = '{}.thumb.jpg'.format(self.filename)
            thumbpath = os.path.join(self.directory, thumbtitle)
            tfc = FrameConverter(self.poster_sample, thumbpath, width = 256)
            tfuture = asyncio.ensure_future(tfc.convert())
            
            await pfuture, tfuture
            logging.info('Created poster and thumbnail images')
        else:
            postertitle = ''
            thumbtitle = ''
                
        logging.info('Finished analysis of URI {}'.format(self.uri))
        
        self.progress(3 / 3)
        
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
            
        
        return {
            'result': {
                'type': _type,
                'tracks': self.tracks,
                'metadata': self.metadata,
                'poster': postertitle,
                'thumb': thumbtitle
            }
        }
    
    def on_message(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'analyze')
            logging.error('GStreamer error for media {}: {}'.format(self.filename, msg.parse_error()))
            self.duration = 0
            self.tracks = []
            self.tracks_done = True
        
        elif msg.type == Gst.MessageType.WARNING:
            logging.warning('GStreamer warning for media {}: {}'.format(self.filename, msg.parse_error()))
        
        elif msg.type == Gst.MessageType.TAG:
            tag_list = msg.parse_tag()
            
            gs, gu, gd = create_taglist_getters(tag_list)
            
            metadata = {
                'album': gs(Gst.TAG_ALBUM),
                'artist': gs(Gst.TAG_ARTIST),
                'composer': gs(Gst.TAG_COMPOSER),
                'genre': gs(Gst.TAG_GENRE),
                'license': gs(Gst.TAG_LICENSE),
                'performer': gs(Gst.TAG_PERFORMER),
                'title': gs(Gst.TAG_TITLE),
                'track-number': gu(Gst.TAG_TRACK_NUMBER),
                'channel-mode': gs('channel-mode'),
                'replaygain': gd(Gst.TAG_TRACK_GAIN),
            }
            
            bpm = gd(Gst.TAG_BEATS_PER_MINUTE)
            if bpm: metadata['bpm'] = round(bpm, 1)
            
            for k, v in metadata.items():
                if not v == None: self.metadata[k] = v
            
            if not self.poster_sample:
                found, sample = tag_list.get_sample(Gst.TAG_IMAGE)
                if found:
                    logging.info('Found poster image in file tag')
                    self.poster_sample = sample
                
        elif msg.type == Gst.MessageType.EOS:
            self.analyzing = False
    
    def pad_added(self, decodebin, pad):
        caps = pad.get_current_caps()
        
        if caps:
            struct = caps.get_structure(0)
            logging.debug('Track found with caps: {}'.format(struct.to_string()))
            
            track = {'caps': caps}
            suffix = '_{}'.format(len(self.tracks))

            name = struct.get_name()
            _type = name.split('/')[0]
            
            if _type == 'audio':
                track['type'] = 'audio'
                track['samplerate'] = struct.get_value('rate')
            
            elif _type == 'video':
                track['width'] = struct.get_value('width')
                track['height'] = struct.get_value('height')
                
                found, numerator, denominator = struct.get_fraction('framerate')
                if numerator == 0: # Framerate of 0 means track is a still image
                    track['type'] = 'image'
                else:
                    track['type'] = 'video'
                    track['framerate'] = round(numerator / float(denominator), 2)
                
            else:
                track['type'] = 'invalid'
            
            self.tracks.append(track)
            
            fake = Gst.ElementFactory.make('fakesink', 'fakesink' + suffix)
            self.pipeline.add(fake)
            pad.link(fake.get_static_pad('sink'))
            fake.set_state(Gst.State.PLAYING)
    
    def no_more_pads(self, decodebin):
        self.tracks_done = True
