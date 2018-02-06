import logging
import asyncio

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp

class FrameConverter:
    '''Converts a Gst.Sample to a JPG file with specified dimensions'''
    def __init__(self, sample, dest, width = None, height = None, max_width = None):
        self.complete = False
        
        self.sample = sample
        self.srccaps = sample.get_caps()
        self.struct = self.srccaps.get_structure(0)
        
        self.dest = dest
        
        srcwidth = self.struct.get_value('width')
        srcheight = self.struct.get_value('height')
        
        if width == None:
            self.width = srcwidth
            self.height = srcheight
        elif height == None:
            self.width = width
            ratio = srcwidth / srcheight
            self.height = int(round(width / ratio))
        else:
            self.width = width
            self.height = height
        
        if max_width:
            ratio = self.width / self.height
            self.width = max_width
            self.height = int(round(max_width / ratio))
        
        if self.struct.get_name() == 'image/jpeg' and width and height:
            self.pipeline = Gst.parse_launch(
                'appsrc name=appsrc caps="{}" emit-signals=true ! filesink location={}'.format(
                    self.srccaps.to_string(), self.dest
                )
        )
        
        else:
            self.pipeline = Gst.parse_launch(
                'appsrc name=appsrc caps="{}" emit-signals=true ! decodebin ! videoconvert ! \
                 videoscale method="lanczos" ! jpegenc ! {} ! filesink location="{}"'.format(
                    self.srccaps.to_string(),
                    'image/jpeg, width={}, height={}, pixel-aspect-ratio=1/1'.format(self.width, self.height),
                    self.dest
                )
            )

        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self.on_message)
        
        self.appsrc = self.pipeline.get_by_name('appsrc')
        self.appsrc.connect('need-data', self.need_data)

    def need_data(self, appsrc, arg1):
        if self.sample:
            appsrc.push_sample(self.sample)
            self.sample = None
        else:
            appsrc.end_of_stream()
    
    def on_message(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            logging.error('GStreamer error for frame converter {}: {}'.format(self.dest, msg.parse_error()))
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'frameconverter')
        
        elif msg.type == Gst.MessageType.WARNING:
            logging.warning('GStreamer warning for frame converter {}: {}'.format(self.dest, msg.parse_error()))

        elif msg.type == Gst.MessageType.EOS:
            self.complete = True
    
    async def convert(self):
        self.pipeline.set_state(Gst.State.PLAYING)

        while not self.complete:
            await asyncio.sleep(0.25)
        
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'frameconverter')
        self.pipeline.set_state(Gst.State.NULL)
