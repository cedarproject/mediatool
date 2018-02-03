import logging
import asyncio

import gbulb
gbulb.install()

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstVideo, GLib

Gst.init(None)

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
            await asyncio.sleep(1)
        
        logging.info('Finished analysis of URI {} with id {}'.format(self.uri, self._id))
        self.pipeline.set_state(Gst.State.NULL)
        
        print(self.metadata)
        print(self.tracks)
    
    def on_message(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            logging.error('GStreamer error for media {}: {}'.format(self._id, msg.parse_error()))
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'analyze')
        
        if msg.type == Gst.MessageType.WARNING:
            logging.warning('GStreamer warning for media {}: {}'.format(self._id, msg.parse_error()))
        
        if msg.type == Gst.MessageType.TAG:
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
            
            # TODO: get album art from taglist, make poster/thumbnails for image/videos, actually hook up to cedarserver
            
        if msg.type == Gst.MessageType.EOS:
            logging.debug('eos')
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
                
                fake = Gst.ElementFactory.make('fakesink', 'fakesink' + suffix)
                self.pipeline.add(fake)
                pad.link(fake.get_static_pad('sink'))
                fake.set_state(Gst.State.PLAYING)
            
            else:
                track['type'] = 'inavlid'
            
            self.tracks.append(track)
