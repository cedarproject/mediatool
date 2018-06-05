import logging
import asyncio

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)

from .taglist_utils import create_taglist_getters

class TrackAndTagGetter:
    def __init__(self, uri):
        self.uri = uri

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
    
    async def go(self):
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
        
        return self.tracks, self.metadata, self.duration, self.poster_sample
    
    def on_message(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'analyze')
            logging.error('GStreamer error for media {}: {}'.format(self.uri, msg.parse_error()))
            self.duration = 0
            self.tracks = []
            self.tracks_done = True
        
        elif msg.type == Gst.MessageType.WARNING:
            logging.warning('GStreamer warning for media {}: {}'.format(self.uri, msg.parse_error()))
        
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
