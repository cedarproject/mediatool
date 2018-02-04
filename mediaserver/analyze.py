import logging
import asyncio

import os

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp, GLib

Gst.init(None)

from convert import FrameConverter

def create_taglist_getters(tag_list):
    # Generate a function for a TagList that works around GStreamer's introspected weirdness
    def gs(tag):
        found, val = tag_list.get_string(tag)
        if found: return val
        else: return None
    
    def gu(tag):
        found, val = tag_list.get_uint(tag)
        if found: return val
        else: return None
    
    def gd(tag):
        found, val = tag_list.get_double(tag)
        if found: return val
        else: return None
            
    return gs, gu, gd

class Analyzer:
    def __init__(self, _id, uri, mediaserver):
        self._id = _id
        self.uri = uri
        self.mediaserver = mediaserver
        
        self.tracks = []
        self.metadata = {}

        self.duration = None
        self.poster_time = None
        self.poster_track = None
        self.poster_sample = None
        
        self.converters = set()
        
        self.pipeline = Gst.Pipeline.new()
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self.on_message)
        
        self.decodebin = Gst.ElementFactory.make('uridecodebin', 'uridecodebin_{}'.format(self._id))
        self.decodebin.connect('pad-added', self.pad_added)
        self.decodebin.set_property('uri', self.uri)
        
        self.pipeline.add(self.decodebin)
        
    async def analyze(self):
        logging.info('Starting analysis of URI {} with id {}'.format(self.uri, self._id))
        self.pipeline.set_state(Gst.State.PLAYING)    
        self.analyzing = True
        
        while self.analyzing:
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'analyze')
            res, state, pending = self.pipeline.get_state(1)
            if state == Gst.State.PLAYING and self.duration == None:
                query = Gst.Query.new_duration(Gst.Format.TIME)
                if self.decodebin.query(query):
                    fmt, duration = query.parse_duration()
                    if duration:
                        logging.info('Media duration is {}'.format(duration))
                        self.duration = duration
                        self.poster_time = duration * 0.1 # One-tenth of the way through the video (only applies to videos)

            await asyncio.sleep(1)

        if self.poster_sample:
#        filename = os.path.join(self.mediaserver.tempdir, '{}-tagimage.jpg')
            self.poster = os.path.join('/', 'tmp', '{}-poster.jpg'.format(self._id))
            self.converters.add(FrameConverter(self.poster_sample, self.poster))

            self.thumb = os.path.join('/', 'tmp', '{}-thumb.jpg'.format(self._id))
            self.converters.add(FrameConverter(self.poster_sample, self.thumb, width = 256))
        
        while True:
            if all((c.complete for c in self.converters)): break
            else: await asyncio.sleep(1)
        
        logging.info('Finished analysis of URI {} with id {}'.format(self.uri, self._id))
        self.pipeline.set_state(Gst.State.NULL)
        
        print(self.metadata)
        print(self.tracks)
    
    def on_message(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            logging.error('GStreamer error for media {}: {}'.format(self._id, msg.parse_error()))
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'analyze')
        
        elif msg.type == Gst.MessageType.WARNING:
            logging.warning('GStreamer warning for media {}: {}'.format(self._id, msg.parse_error()))
        
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
            
            found, imagesample = tag_list.get_sample(Gst.TAG_IMAGE)
            if found and not self.tagimage_processed:
                logging.info('Found poster image in file tag')
                self.poster_sample = sample
                
        elif msg.type == Gst.MessageType.EOS:
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'analyze')
            self.analyzing = False
    
    def pad_added(self, element, pad):
        caps = pad.get_current_caps()
        
        if caps:
            struct = caps.get_structure(0)
            logging.debug('Track found with caps: {}'.format(struct.to_string()))
            
            track = {}
            suffix = '_{}_{}'.format(self._id, len(self.tracks))

            name = struct.get_name()
            _type = name.split('/')[0]
            
            if _type == 'audio':
                track['type'] = 'audio'
                track['samplerate'] = struct.get_value('rate')

                ac = Gst.ElementFactory.make('audioconvert', 'audioconvert' + suffix)
                self.pipeline.add(ac)
                pad.link(ac.get_static_pad('sink'))
                ac.set_state(Gst.State.PLAYING)

                rg = Gst.ElementFactory.make('rganalysis', 'replaygain' + suffix)
                self.pipeline.add(rg)
                ac.link(rg)
                rg.set_state(Gst.State.PLAYING)
                
                bp = Gst.ElementFactory.make('bpmdetect', 'bpmdetect' + suffix)
                self.pipeline.add(bp)
                rg.link(bp)
                bp.set_state(Gst.State.PLAYING)
                
                fake = Gst.ElementFactory.make('fakesink', 'fakesink' + suffix)
                self.pipeline.add(fake)
                bp.link(fake)
                fake.set_state(Gst.State.PLAYING)
            
            elif _type == 'video':
                track['width'] = struct.get_value('width')
                track['height'] = struct.get_value('height')
                
                found, numerator, denominator = struct.get_fraction('framerate')
                if numerator == 0: # Framerate of 0 means track is a still image
                    track['type'] = 'image'
                else:
                    track['type'] = 'video'
                    track['framerate'] = round(numerator / float(denominator), 2)
                
                if self.poster_track:
                    fake = Gst.ElementFactory.make('fakesink', 'fakesink' + suffix)
                    self.pipeline.add(fake)
                    pad.link(fake.get_static_pad('sink'))
                    fake.set_state(Gst.State.PLAYING)

                else:
                    self.poster_track = track
                    app = Gst.ElementFactory.make('appsink', 'appsink' + suffix)
                    app.set_property('emit-signals', True)
                    app.set_property('drop', True)
                    app.set_property('caps', Gst.Caps.from_string('video/x-raw'))
                    app.connect('new-sample', self.poster_track_new_sample, track)

                    self.pipeline.add(app)
                    pad.link(app.get_static_pad('sink'))
                    app.set_state(Gst.State.PLAYING)
            
            else:
                track['type'] = 'invalid'
            
            self.tracks.append(track)
    
    def poster_track_new_sample(self, appsink, track):
        # TODO make this work with grabbing image samples too
        sample = appsink.emit('pull-sample')

        if not self.poster_sample and not self.poster_time == None:
            query = Gst.Query.new_position(Gst.Format.TIME)

            if self.decodebin.query(query):
                fmt, position = query.parse_position()
                if position and position >= self.poster_time:
                    self.poster_sample = sample
        
        return Gst.FlowReturn.OK
