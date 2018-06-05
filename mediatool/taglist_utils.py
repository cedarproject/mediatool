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
