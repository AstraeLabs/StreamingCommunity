# 14.01.26


VIDEO_CODEC_MAP = {

    # H.264 / AVC
    'avc1': 'H.264',
    'avc1.42E00A': 'H.264',
    'avc1.42E01E': 'H.264',
    'avc1.4D401E': 'H.264',
    'avc1.4D401F': 'H.264',
    'avc1.640028': 'H.264',
    'avc1.640029': 'H.264',
    'avc1.64002A': 'H.264',
    'avc1.640032': 'H.264',
    'avc1.640033': 'H.264',
    'h264': 'H.264',
    'x264': 'H.264',
    
    # H.265 / HEVC
    'hvc1': 'H.265',
    'hev1': 'H.265',
    'hvc1.1.6.L93.90': 'H.265',
    'hvc1.1.6.L120.90': 'H.265',
    'hvc1.1.6.L150.90': 'H.265',
    'hvc1.2.4.L120.90': 'H.265',
    'hevc': 'H.265',
    'h265': 'H.265',
    'x265': 'H.265',
    
    # VP8
    'vp8': 'VP8',
    'VP80': 'VP8',
    
    # VP9
    'vp9': 'VP9',
    'vp09': 'VP9',
    'vp09.00.41.08': 'VP9',
    'vp09.02.41.10': 'VP9',
    'VP90': 'VP9',
    
    # AV1
    'av1': 'AV1',
    'av01': 'AV1',
    'av01.0.05M.08': 'AV1',
    'av01.0.08M.08': 'AV1',
    'av01.0.12M.10': 'AV1',
    
    # Dolby Vision
    'dvhe': 'Dolby Vision',
    'dvh1': 'Dolby Vision',
    
    # Altri codec video
    'mpeg4': 'MPEG-4',
    'mp4v': 'MPEG-4',
    'xvid': 'Xvid',
    'divx': 'DivX',
    'theora': 'Theora',
    'wmv3': 'WMV',
    'vc1': 'VC-1',
    'mjpeg': 'MJPEG',
    'prores': 'ProRes',
    'dnxhd': 'DNxHD',
    'dnxhr': 'DNxHR',
}

AUDIO_CODEC_MAP = {
    
    # AAC
    'mp4a': 'AAC',
    'mp4a.40.2': 'AAC',
    'mp4a.40.5': 'AAC',
    'mp4a.40.29': 'AAC',
    'aac': 'AAC',
    
    # MP3
    'mp3': 'MP3',
    'mp4a.69': 'MP3',
    'mp4a.6B': 'MP3',
    '.mp3': 'MP3',
    
    # Opus
    'opus': 'Opus',
    'Opus': 'Opus',
    
    # Vorbis
    'vorbis': 'Vorbis',
    'vorb': 'Vorbis',
    
    # AC-3 / E-AC-3
    'ac3': 'AC-3',
    'ac-3': 'AC-3',
    'eac3': 'E-AC-3',
    'ec-3': 'E-AC-3',
    
    # DTS
    'dts': 'DTS',
    'dtsc': 'DTS',
    'dtse': 'DTS',
    'dtsh': 'DTS',
    
    # FLAC
    'flac': 'FLAC',
    'fLaC': 'FLAC',
    
    # PCM
    'pcm': 'PCM',
    'lpcm': 'PCM',
    'pcm_s16le': 'PCM',
    'pcm_s24le': 'PCM',
    
    # Altri
    'alac': 'ALAC',
    'wma': 'WMA',
    'wmav2': 'WMA',
    'amr': 'AMR',
    'speex': 'Speex',
}

SUBTITLE_CODEC_MAP = {
    'stpp.ttml.im1t': 'TTML',
    'stpp': 'TTML',
    'ttml': 'TTML',
    'wvtt': 'VTT',
    'vtt': 'VTT',
    'webvtt': 'VTT',
    'srt': 'SRT',
    'tx3g': 'SRT',
    'ass': 'ASS',
    'ssa': 'SSA'
}

CHANNEL_LAYOUT_MAP = {
    'A000': '2',
    'A001': '1',
    'A002': '2.1',
    'F801': '5.1',
    'F803': '7.1',
    'F805': '7.1',
    'F809': '5.1'
}


def get_video_codec_name(codec_string):
    """Get the human-readable name of a video codec."""
    if codec_string in VIDEO_CODEC_MAP:
        return VIDEO_CODEC_MAP[codec_string]
    
    for key, value in VIDEO_CODEC_MAP.items():
        if codec_string.startswith(key):
            return value
    
    return codec_string

def get_audio_codec_name(codec_string):
    """Get the human-readable name of an audio codec."""
    if codec_string in AUDIO_CODEC_MAP:
        return AUDIO_CODEC_MAP[codec_string]
    
    for key, value in AUDIO_CODEC_MAP.items():
        if codec_string.startswith(key):
            return value
    
    return codec_string

def get_subtitle_codec_name(codec_string):
    """Get the human-readable name of a subtitle codec."""
    if codec_string in SUBTITLE_CODEC_MAP:
        return SUBTITLE_CODEC_MAP[codec_string]
    
    for key, value in SUBTITLE_CODEC_MAP.items():
        if codec_string.startswith(key):
            return value
    
    return codec_string

def get_channel_layout_name(channel_string):
    """Get the human-readable name of a channel layout."""
    if channel_string in CHANNEL_LAYOUT_MAP:
        return CHANNEL_LAYOUT_MAP[channel_string.strip()]
    
    return channel_string

def get_codec_type(codec_string):
    """Get the type of codec: 'Video', 'Audio', 'Subtitle' or 'Unknown'."""
    for key in VIDEO_CODEC_MAP.keys():
        if codec_string.startswith(key):
            return 'Video'
    
    for key in AUDIO_CODEC_MAP.keys():
        if codec_string.startswith(key):
            return 'Audio'

    for key in SUBTITLE_CODEC_MAP.keys():
        if codec_string.startswith(key):
            return 'Subtitle'
    
    return 'Unknown'