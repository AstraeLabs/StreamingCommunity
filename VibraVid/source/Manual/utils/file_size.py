# 19.05.25

def format_size(bytes_size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} TB"

def format_bitrate(bps):
    if bps < 1000:
        return f"{bps} bps"
    elif bps < 1000000:
        return f"{bps/1000:.0f} Kbps"
    else:
        return f"{bps/1000000:.1f} Mbps"