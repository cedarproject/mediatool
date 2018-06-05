import logging
import asyncio

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

class FrameGrabber:
    '''Grabs a frame from a video track'''
    def __init__(self, uri, targetcaps, targettime):
        self.complete = False
        self.sample = None
        
        self.targetcaps = targetcaps
        self.targettime = targettime
        
        self.appsink = None
        self.seeked = targettime == 0 # Don't seek if targettime is 0

        self.pipeline = Gst.Pipeline.new()
        
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self.on_message)
        
        self.decodebin = Gst.ElementFactory.make('uridecodebin')
        self.decodebin.set_property('uri', uri)
        self.decodebin.connect('pad-added', self.pad_added)
        
        self.pipeline.add(self.decodebin)
        
    def pad_added(self, decodebin, pad):
        caps = pad.get_current_caps()
        
        if caps:
            if caps.is_equal(self.targetcaps) and not self.appsink:
                self.appsink = Gst.ElementFactory.make('appsink')
                self.appsink.set_property('emit-signals', True)
                self.appsink.set_property('drop', True)
                self.appsink.set_property('caps', Gst.Caps.from_string('video/x-raw'))
                self.appsink.connect('new-preroll', self.grab_frame)
                
                self.pipeline.add(self.appsink)
                pad.link(self.appsink.get_static_pad('sink'))
                self.appsink.set_state(Gst.State.PAUSED)
            else:
                fake = Gst.ElementFactory.make('fakesink')
                self.pipeline.add(fake)
                pad.link(fake.get_static_pad('sink'))
                fake.set_state(Gst.State.PAUSED)
                    
    def grab_frame(self, appsink):
        sample = appsink.emit('pull-preroll')
        if sample and self.seeked and not self.complete:
            self.sample = sample
            self.complete = True
        
        return Gst.FlowReturn.OK

    def on_message(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            logging.error('GStreamer error for frame grabber: {}'.format(msg.parse_error()))
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'framegrabber')
        
        elif msg.type == Gst.MessageType.WARNING:
            logging.warning('GStreamer warning for frame grabber: {}'.format(msg.parse_error()))

    async def grab(self):
        self.pipeline.set_state(Gst.State.PAUSED)
        
        while not self.complete:
            if not self.seeked:
                res, state, pending = self.pipeline.get_state(10)
                if res == Gst.StateChangeReturn.SUCCESS and state == Gst.State.PAUSED:
                    self.pipeline.seek_simple(Gst.Format.TIME,
                        Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, self.targettime)

                    self.seeked = True
        
            await asyncio.sleep(0.25)
        
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, 'framegrabber')
        self.pipeline.set_state(Gst.State.NULL)
        
        return self.sample
