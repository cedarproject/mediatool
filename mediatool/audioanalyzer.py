import logging
import asyncio

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

from .taglist_utils import create_taglist_getters

class AudioAnalyzer:
    '''Analyzes audio for ReplayGain normalization and BPM detection'''
    def __init__(self, uri, duration):
        self.complete = False
        self.progress = 0
        
        self.duration = duration
        
        self.replaygain = None
        self.bpm = None
        
        self.pipeline = Gst.parse_launch(
            'uridecodebin uri="{}" caps="audio/x-raw" expose-all-streams=false ! audioconvert ! rganalysis forced=false ! bpmdetect ! fakesink'.format(uri)
        )
        
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self.on_message)
                
    def on_message(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            logging.error('GStreamer error for audio analyzer: {}'.format(msg.parse_error()))
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'audioanalyzer')
        
        elif msg.type == Gst.MessageType.WARNING:
            logging.warning('GStreamer warning for audio analyzer: {}'.format(msg.parse_error()))
        
        elif msg.type == Gst.MessageType.TAG:
            tag_list = msg.parse_tag()
            
            gs, gu, gd = create_taglist_getters(tag_list)
            
            replaygain = gd(Gst.TAG_TRACK_GAIN)
            if replaygain: self.replaygain = replaygain
            
            bpm = gd(Gst.TAG_BEATS_PER_MINUTE)
            if bpm: self.bpm = round(bpm, 1)

        elif msg.type == Gst.MessageType.EOS:
            self.complete = True

    async def analyze(self):
        self.pipeline.set_state(Gst.State.PLAYING)
        
        while not self.complete:
            await asyncio.sleep(0.25)
        
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'audioanalyzer')
        self.pipeline.set_state(Gst.State.NULL)
        
        return self.replaygain, self.bpm
