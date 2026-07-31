# 10.07.26

import io
import mmap
import struct
import sys


class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def remaining(self):
        return len(self.data) - self.pos

    def read(self, n):
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self):
        v = struct.unpack_from(">H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def s16(self):
        v = struct.unpack_from(">h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u24(self):
        b = self.read(3)
        return (b[0] << 16) | (b[1] << 8) | b[2]

    def u32(self):
        v = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def s32(self):
        v = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u64(self):
        v = struct.unpack_from(">Q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def u8_array(self, n):
        n = min(n, len(self.data) - self.pos)
        vals = self.data[self.pos : self.pos + n]
        self.pos += n
        return list(vals)

    def u16_array(self, n):
        n = min(n, (len(self.data) - self.pos) // 2)
        vals = struct.unpack_from(f">{n}H", self.data, self.pos)
        self.pos += 2 * n
        return list(vals)

    def u32_array(self, n):
        n = min(n, (len(self.data) - self.pos) // 4)
        vals = struct.unpack_from(f">{n}I", self.data, self.pos)
        self.pos += 4 * n
        return list(vals)

    def u64_array(self, n):
        n = min(n, (len(self.data) - self.pos) // 8)
        vals = struct.unpack_from(f">{n}Q", self.data, self.pos)
        self.pos += 8 * n
        return list(vals)

    def tuples(self, fmt, n):
        item_size = struct.calcsize(fmt)
        n = min(n, (len(self.data) - self.pos) // item_size)
        size = item_size * n
        chunk = self.data[self.pos : self.pos + size]
        self.pos += size
        return list(struct.iter_unpack(fmt, chunk))

    def fourcc(self):
        return self.read(4).decode("latin-1")

    def cstring(self):
        start = self.pos
        end = self.data.find(b"\x00", start)
        if end == -1:
            end = len(self.data)
        s = self.data[start:end].decode("utf-8", errors="replace")
        self.pos = end + 1
        return s


FULL_BOX_TYPES = {
    "mvhd",
    "tkhd",
    "mdhd",
    "hdlr",
    "vmhd",
    "smhd",
    "nmhd",
    "hmhd",
    "dref",
    "url ",
    "urn ",
    "stsd",
    "stts",
    "ctts",
    "stsc",
    "stsz",
    "stz2",
    "stco",
    "co64",
    "stss",
    "elst",
    "mfhd",
    "tfhd",
    "tfdt",
    "trun",
    "sidx",
    "mehd",
    "trex",
    "meta",
    "esds",
    "pssh",
    "schm",
    "tenc",
    "saiz",
    "saio",
    "senc",
    "iods",
    "leva",
    "emsg",
    "keys",
}

CONTAINER_TYPES = {
    "moov",
    "trak",
    "mdia",
    "minf",
    "dinf",
    "stbl",
    "edts",
    "mvex",
    "moof",
    "traf",
    "mfra",
    "udta",
    "ipro",
    "sinf",
    "schi",
}

_NO_PAYLOAD_SLICE = CONTAINER_TYPES | {"meta", "tref", "mdat", "free", "skip", "wide"}

TRACK_REFERENCE_TYPES = {
    "hint",
    "cdsc",
    "font",
    "hind",
    "vdep",
    "vplx",
    "subt",
    "thmb",
    "chap",
    "sync",
    "tmcd",
    "iled",
    "mpod",
}

ILST_NAME_TAGS = {
    "\xa9nam": "name",
    "\xa9ART": "artist",
    "\xa9alb": "album",
    "\xa9day": "date",
    "\xa9cmt": "comment",
    "\xa9gen": "genre",
    "\xa9wrt": "composer",
    "\xa9too": "tool",
    "\xa9grp": "grouping",
    "\xa9lyr": "lyrics",
    "trkn": "track_number",
    "disk": "disk_number",
    "cpil": "compilation",
    "tmpo": "tempo",
    "covr": "cover_art",
}

VIDEO_SAMPLE_ENTRY_TYPES = {"avc1", "avc3", "hev1", "hvc1", "mp4v", "s263", "vp09", "av01", "encv"}
AUDIO_SAMPLE_ENTRY_TYPES = {"mp4a", "ac-3", "ec-3", "opus", "alac", "samr", "sawb", "enca"}


class Atom:
    def __init__(self, atype, header_size, total_size, version=0, flags=0):
        self.type = atype
        self.header_size = header_size
        self.total_size = total_size
        self.version = version
        self.flags = flags
        self.data = {}
        self.children = []


def parse_box(buf: bytes, offset: int, limit: int):
    r = Reader(buf, offset)
    size32 = r.u32()
    box_type = r.fourcc()
    header_size = 8

    if size32 == 1:
        size64 = r.u64()
        header_size = 16
        total_size = size64
    elif size32 == 0:
        total_size = limit - offset
    else:
        total_size = size32

    if box_type == "uuid":
        r.read(16)
        header_size += 16

    is_full = box_type in FULL_BOX_TYPES
    version = 0
    flags = 0
    if is_full:
        version = r.u8()
        flags = r.u24()
        header_size += 4

    atom = Atom(box_type, header_size, total_size, version, flags)
    payload_size = total_size - header_size
    payload_start = offset + header_size
    if box_type in _NO_PAYLOAD_SLICE:
        payload = b""
    else:
        payload = buf[payload_start : payload_start + payload_size]

    _decode_payload(atom, buf, payload_start, payload, payload_size)

    return atom, offset + total_size


def _parse_children(buf, start, size):
    children = []
    end = start + size
    off = start
    while off < end:
        child, off = parse_box(buf, off, end)
        children.append(child)
    return children


def _parse_sample_entries(buf, start, size):
    children = []
    end = start + size
    off = start
    while off < end:
        child, off = parse_sample_entry(buf, off, end)
        children.append(child)
    return children


def _parse_track_reference(buf, offset, limit):
    r = Reader(buf, offset)
    size32 = r.u32()
    box_type = r.fourcc()
    header_size = 8
    if size32 == 1:
        total_size = r.u64()
        header_size = 16
    else:
        total_size = size32
    atom = Atom(box_type, header_size, total_size)
    payload_size = total_size - header_size
    p = Reader(buf, offset + header_size)
    count = payload_size // 4
    atom.data["track_ids"] = [p.u32() for _ in range(count)]
    return atom, offset + total_size


def parse_sample_entry(buf, offset, limit):
    r = Reader(buf, offset)
    size32 = r.u32()
    box_type = r.fourcc()
    header_size = 8
    if size32 == 1:
        total_size = r.u64()
        header_size = 16
    else:
        total_size = size32

    atom = Atom(box_type, header_size, total_size)
    p = Reader(buf, offset + header_size)
    p.read(6)  # reserved
    data_reference_index = p.u16()
    atom.data["data_reference_index"] = data_reference_index

    if box_type in VIDEO_SAMPLE_ENTRY_TYPES:
        p.read(16)
        width = p.u16()
        height = p.u16()
        p.read(4 + 4)
        p.read(4)
        p.read(2)
        compressor = p.read(32)

        name_len = compressor[0]
        compressor_name = compressor[1 : 1 + name_len].decode("utf-8", errors="replace")
        p.read(2)
        p.read(2)
        atom.data["width"] = width
        atom.data["height"] = height
        atom.data["compressor"] = compressor_name
        atom.data["_kind"] = "video"
    elif box_type in AUDIO_SAMPLE_ENTRY_TYPES:
        entry_version = p.u16()
        p.read(2)
        p.read(4)
        channel_count = p.u16()
        sample_size = p.u16()
        p.read(2)
        p.read(2)
        sample_rate = p.u32() >> 16
        atom.data["channel_count"] = channel_count
        atom.data["sample_size"] = sample_size
        atom.data["sample_rate"] = sample_rate
        atom.data["_kind"] = "audio"
        atom.data["version"] = entry_version
        if entry_version == 1:
            atom.data["samples_per_packet"] = p.u32()
            atom.data["bytes_per_packet"] = p.u32()
            atom.data["bytes_per_frame"] = p.u32()
            atom.data["bytes_per_sample"] = p.u32()
        elif entry_version == 2:
            atom.data["size_of_struct_only"] = p.u32()
            atom.data["sample_rate_64"] = struct.unpack(">d", p.read(8))[0]
            atom.data["channel_count"] = p.u32()
            p.read(4)  # always 0x7F000000
            atom.data["const_bits_per_channel"] = p.u32()
            atom.data["format_specific_flags"] = p.u32()
            atom.data["const_bytes_per_audio_packet"] = p.u32()
            atom.data["const_lpcm_frames_per_audio_packet"] = p.u32()
    else:
        atom.data["_kind"] = None

    children_start = p.pos
    children_size = (offset + total_size) - children_start
    atom.children = _parse_children(buf, children_start, children_size)

    return atom, offset + total_size


def _hex_brackets(data: bytes) -> str:
    return "[" + " ".join(f"{b:02x}" for b in data) + "]"


def _decode_descriptor(buf, offset, limit):
    tag = buf[offset]
    pos = offset + 1
    size = 0
    while True:
        b = buf[pos]
        pos += 1
        size = (size << 7) | (b & 0x7F)
        if not (b & 0x80):
            break
    header_size = pos - offset
    payload_start = pos
    payload_end = payload_start + size
    payload = buf[payload_start:payload_end]

    node = {
        "tag": tag,
        "header_size": header_size,
        "size": size,
        "fields": [],
        "children": [],
        "raw": payload,
    }

    if tag == 0x03:
        node["name"] = "ESDescriptor"
        p = Reader(payload)
        es_id = p.u16()
        flags_byte = p.u8()
        stream_dependence = (flags_byte >> 7) & 1
        url_flag = (flags_byte >> 6) & 1
        ocr_stream = (flags_byte >> 5) & 1
        stream_priority = flags_byte & 0x1F
        if stream_dependence:
            p.read(2)
        if url_flag:
            url_len = p.u8()
            p.read(url_len)
        if ocr_stream:
            p.read(2)
        node["fields"] = [("es_id", es_id), ("stream_priority", stream_priority)]
        cpos = payload_start + p.pos
        while cpos < payload_end:
            child, cpos = _decode_descriptor(buf, cpos, payload_end)
            node["children"].append(child)

    elif tag == 0x04:
        node["name"] = "DecoderConfig"
        p = Reader(payload)
        object_type = p.u8()
        b1 = p.u8()
        stream_type = (b1 >> 2) & 0x3F
        up_stream = (b1 >> 1) & 0x1
        buffer_size = p.u24()
        max_bitrate = p.u32()
        avg_bitrate = p.u32()
        node["fields"] = [
            ("stream_type", stream_type),
            ("object_type", object_type),
            ("up_stream", up_stream),
            ("buffer_size", buffer_size),
            ("max_bitrate", max_bitrate),
            ("avg_bitrate", avg_bitrate),
        ]
        cpos = payload_start + p.pos
        while cpos < payload_end:
            child, cpos = _decode_descriptor(buf, cpos, payload_end)
            if child["tag"] == 0x05:  # DecoderSpecificInfo: mostrato inline (hex)
                hex_str = " ".join(f"{b:02x}" for b in child["raw"]) + " "
                node["fields"].append(("DecoderSpecificInfo", hex_str))
            else:
                node["children"].append(child)

    elif tag in (0x02, 0x10, 0x11):
        node["name"] = "ObjectDescriptor" if tag == 0x02 else "InitialObjectDescriptor"
        p = Reader(payload)
        b0 = p.u16()
        od_id = (b0 >> 6) & 0x3FF
        url_flag = (b0 >> 5) & 0x1
        node["fields"] = [("ObjectDescriptorID", od_id), ("URL_Flag", url_flag)]
        if url_flag:
            url_len = p.u8()
            url_string = p.read(url_len).decode("utf-8", errors="replace")
            node["fields"].append(("URLString", url_string))
        else:
            if tag in (0x10, 0x11):
                # includeInlineProfileLevelFlag(1) + reserved(3) + 4 profileLevelIndication byte
                p.read(1)
                p.read(4)
            cpos = payload_start + p.pos
            while cpos < payload_end:
                child, cpos = _decode_descriptor(buf, cpos, payload_end)
                node["children"].append(child)
    else:
        node["name"] = f"Descriptor:{tag:02x}"

    return node, payload_end


def _inspect_descriptor(node, insp):
    insp.start_atom(node["name"], 0, 0, node["header_size"], node["header_size"] + node["size"])
    for name, value in node["fields"]:
        insp.add_field(name, value)
    for child in node["children"]:
        _inspect_descriptor(child, insp)
    insp.end_atom()


def _looks_like_mdta_index_tag(box_type, buf, payload_start, payload_size):
    if box_type.isprintable() and not any(ord(c) < 0x20 for c in box_type):
        return False
    if payload_size < 16:
        return False
    if payload_start + 8 > len(buf):
        return False
    inner_size = struct.unpack_from(">I", buf, payload_start)[0]
    inner_type = buf[payload_start + 4 : payload_start + 8]
    return inner_type == b"data" and 8 <= inner_size <= payload_size


def _decode_payload(atom, buf, payload_start, payload, payload_size):
    t = atom.type
    r = Reader(payload)

    if t == "meta":
        atom.children = _parse_children(buf, payload_start, payload_size)
        return

    if t == "tref":
        children = []
        off = payload_start
        end = payload_start + payload_size
        while off < end:
            child, off = _parse_track_reference(buf, off, end)
            children.append(child)
        atom.children = children
        return

    if t in CONTAINER_TYPES:
        atom.children = _parse_children(buf, payload_start, payload_size)
        return

    if t == "ftyp" or t == "styp":
        atom.data["major_brand"] = r.fourcc()
        atom.data["minor_version"] = r.u32()
        brands = []
        while r.remaining() >= 4:
            brands.append(r.fourcc())
        atom.data["compatible_brands"] = brands
        return

    if t == "mvhd":
        if atom.version == 1:
            r.read(8)
            r.read(8)
            timescale = r.u32()
            duration = r.u64()
        else:
            r.read(4)
            r.read(4)
            timescale = r.u32()
            duration = r.u32()
        atom.data["timescale"] = timescale
        atom.data["duration"] = duration
        return

    if t == "tkhd":
        if atom.version == 1:
            r.read(8)
            r.read(8)
            track_id = r.u32()
            r.read(4)
            duration = r.u64()
        else:
            r.read(4)
            r.read(4)
            track_id = r.u32()
            r.read(4)
            duration = r.u32()

        r.read(8)
        layer = r.s16()
        alternate_group = r.s16()
        volume = r.s16()
        r.read(2)
        matrix = [r.s32() for _ in range(9)]
        width = r.u32()
        height = r.u32()
        atom.data.update(
            dict(
                track_id=track_id,
                duration=duration,
                layer=layer,
                alternate_group=alternate_group,
                volume=volume,
                matrix=matrix,
                width=width,
                height=height,
            )
        )
        return

    if t == "mdhd":
        if atom.version == 1:
            r.read(8)
            r.read(8)
            timescale = r.u32()
            duration = r.u64()
        else:
            r.read(4)
            r.read(4)
            timescale = r.u32()
            duration = r.u32()
        lang_bits = r.u16()
        chars = [((lang_bits >> 10) & 0x1F) + 0x60, ((lang_bits >> 5) & 0x1F) + 0x60, (lang_bits & 0x1F) + 0x60]
        language = bytes(chars).decode("latin-1", errors="replace")
        atom.data["timescale"] = timescale
        atom.data["duration"] = duration
        atom.data["language"] = language
        return

    if t == "hdlr":
        r.read(4)
        handler_type = r.fourcc()
        r.read(12)
        rest = payload[r.pos :]
        name = rest.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        atom.data["handler_type"] = handler_type
        atom.data["handler_name"] = name
        return

    if t == "vmhd":
        graphics_mode = r.u16()
        op_color = [r.u16() for _ in range(3)]
        atom.data["graphics_mode"] = graphics_mode
        atom.data["op_color"] = op_color
        return

    if t == "smhd":
        balance = r.s16()
        atom.data["balance"] = balance
        return

    if t == "stsd":
        entry_count = r.u32()
        atom.data["entry_count"] = entry_count
        atom.children = _parse_sample_entries(buf, payload_start + r.pos, payload_size - r.pos)
        return

    if t == "dref":
        entry_count = r.u32()
        atom.data["entry_count"] = entry_count
        atom.children = _parse_children(buf, payload_start + r.pos, payload_size - r.pos)
        return

    if t in ("url ", "urn "):
        if t == "url ":
            location = ""
            if not (atom.flags & 1) and r.remaining() > 0:
                location = r.cstring()
            atom.data["location"] = location
        else:
            name = r.cstring() if r.remaining() > 0 else ""
            location = r.cstring() if r.remaining() > 0 else ""
            atom.data["name"] = name
            atom.data["location"] = location
        return

    if t == "stts":
        entry_count = r.u32()
        entries = r.tuples(">II", min(entry_count, 200_000))
        atom.data["entry_count"] = entry_count
        atom.data["entries"] = entries
        return

    if t == "ctts":
        entry_count = r.u32()
        fmt = ">Ii" if atom.version == 1 else ">II"
        entries = r.tuples(fmt, min(entry_count, 200_000))
        atom.data["entry_count"] = entry_count
        atom.data["entries"] = entries
        return

    if t == "stsc":
        entry_count = r.u32()
        raw = r.tuples(">III", min(entry_count, 200_000))
        entries = []

        for i, (first_chunk, samples_per_chunk, sample_desc_index) in enumerate(raw):
            if i + 1 < len(raw):
                chunk_count = raw[i + 1][0] - first_chunk
            else:
                chunk_count = 0
            entries.append(
                dict(
                    first_chunk=first_chunk,
                    chunk_count=chunk_count,
                    samples_per_chunk=samples_per_chunk,
                    sample_desc_index=sample_desc_index,
                )
            )

        first_sample = 1
        for e in entries:
            e["first_sample"] = first_sample
            first_sample += e["chunk_count"] * e["samples_per_chunk"]
        atom.data["entry_count"] = entry_count
        atom.data["entries"] = entries
        return

    if t == "stsz":
        sample_size = r.u32()
        sample_count = r.u32()
        entries = []
        if sample_size == 0:
            entries = r.u32_array(min(sample_count, 500_000))
        atom.data["sample_size"] = sample_size
        atom.data["sample_count"] = sample_count
        atom.data["entries"] = entries
        return

    if t == "stco":
        entry_count = r.u32()
        entries = r.u32_array(min(entry_count, 500_000))
        atom.data["entry_count"] = entry_count
        atom.data["entries"] = entries
        return

    if t == "co64":
        entry_count = r.u32()
        entries = r.u64_array(min(entry_count, 500_000))
        atom.data["entry_count"] = entry_count
        atom.data["entries"] = entries
        return

    if t == "stss":
        entry_count = r.u32()
        atom.data["entry_count"] = entry_count
        return

    if t == "elst":
        entry_count = r.u32()
        entries = []
        for _ in range(min(entry_count, 200_000)):
            if atom.version == 1:
                seg_duration = r.u64()
                media_time = r.s32()
                r.read(4)
            else:
                seg_duration = r.u32()
                media_time = r.s32()
            media_rate = r.u16()
            r.read(2)
            entries.append((seg_duration, media_time, media_rate))
        atom.data["entry_count"] = entry_count
        atom.data["entries"] = entries
        return

    if t == "mfhd":
        atom.data["sequence_number"] = r.u32()
        return

    if t == "tfhd":
        track_id = r.u32()
        atom.data["track_id"] = track_id
        if atom.flags & 0x000001:
            atom.data["base_data_offset"] = r.u64()
        if atom.flags & 0x000002:
            atom.data["sample_description_index"] = r.u32()
        if atom.flags & 0x000008:
            atom.data["default_sample_duration"] = r.u32()
        if atom.flags & 0x000010:
            atom.data["default_sample_size"] = r.u32()
        if atom.flags & 0x000020:
            atom.data["default_sample_flags"] = r.u32()
        return

    if t == "tfdt":
        v = r.u64() if atom.version == 1 else r.u32()
        atom.data["base_media_decode_time"] = v
        return

    if t == "trun":
        sample_count = r.u32()
        if atom.flags & 0x000001:
            atom.data["data_offset"] = r.s32()
        if atom.flags & 0x000004:
            atom.data["first_sample_flags"] = r.u32()
        n = min(sample_count, 500_000)
        fields = []
        fmt = ">"
        if atom.flags & 0x000100:
            fields.append("sample_duration")
            fmt += "I"
        if atom.flags & 0x000200:
            fields.append("sample_size")
            fmt += "I"
        if atom.flags & 0x000400:
            fields.append("sample_flags")
            fmt += "I"
        if atom.flags & 0x000800:
            fields.append("sample_composition_time_offset")
            fmt += "i" if atom.version == 1 else "I"
        if fields:
            entries = [dict(zip(fields, row, strict=False)) for row in r.tuples(fmt, n)]
        else:
            entries = [{} for _ in range(n)]
        atom.data["sample_count"] = sample_count
        atom.data["entries"] = entries
        return

    if t == "sidx":
        reference_id = r.u32()
        timescale = r.u32()
        if atom.version == 0:
            earliest_pts = r.u32()
            first_offset = r.u32()
        else:
            earliest_pts = r.u64()
            first_offset = r.u64()
        r.read(2)
        ref_count = r.u16()
        entries = []
        for _ in range(min(ref_count, 200_000)):
            a = r.u32()
            reference_type = (a >> 31) & 0x1
            referenced_size = a & 0x7FFFFFFF
            subsegment_duration = r.u32()
            b = r.u32()
            starts_with_sap = (b >> 31) & 0x1
            sap_type = (b >> 28) & 0x7
            sap_delta_time = b & 0x0FFFFFFF
            entries.append(
                dict(
                    reference_type=reference_type,
                    referenced_size=referenced_size,
                    subsegment_duration=subsegment_duration,
                    starts_with_sap=starts_with_sap,
                    sap_type=sap_type,
                    sap_delta_time=sap_delta_time,
                )
            )
        atom.data.update(
            dict(
                reference_id=reference_id,
                timescale=timescale,
                earliest_pts=earliest_pts,
                first_offset=first_offset,
                entries=entries,
            )
        )
        return

    if t == "mehd":
        v = r.u64() if atom.version == 1 else r.u32()
        atom.data["duration"] = v
        return

    if t == "trex":
        atom.data["track_id"] = r.u32()
        atom.data["default_sample_description_index"] = r.u32()
        atom.data["default_sample_duration"] = r.u32()
        atom.data["default_sample_size"] = r.u32()
        atom.data["default_sample_flags"] = r.u32()
        return

    if t == "esds":
        descs = []
        pos = payload_start
        end = payload_start + payload_size
        while pos < end:
            node, pos = _decode_descriptor(buf, pos, end)
            descs.append(node)
        atom.data["descriptors"] = descs
        return

    if t == "pssh":
        system_id = r.read(16)
        kids = []
        if atom.version > 0 and r.remaining() >= 4:
            kid_count = r.u32()
            for _ in range(min(kid_count, 10_000)):
                kids.append(r.read(16))
        data_size = r.u32() if r.remaining() >= 4 else 0
        data = r.read(data_size)
        atom.data["system_id"] = system_id
        atom.data["kids"] = kids
        atom.data["data_size"] = data_size
        atom.data["data"] = data
        return

    if t == "frma":
        atom.data["original_format"] = r.fourcc()
        return

    if t == "schm":
        atom.data["scheme_type"] = r.fourcc()
        atom.data["scheme_version"] = r.u32()
        if (atom.flags & 1) and r.remaining() > 0:
            atom.data["scheme_uri"] = r.cstring()
        return

    if t == "tenc":
        r.read(1)  # reserved
        r.read(1)  # reserved (v0) / crypt_byte_block+skip_byte_block (v1)
        is_protected = r.u8()
        iv_size = r.u8()
        kid = r.read(16)
        atom.data["default_isProtected"] = is_protected
        atom.data["default_Per_Sample_IV_Size"] = iv_size
        atom.data["default_KID"] = kid
        if is_protected == 1 and iv_size == 0 and r.remaining() > 0:
            const_iv_size = r.u8()
            atom.data["default_constant_IV"] = r.read(const_iv_size)
        return

    if t == "avcC":
        atom.data["configuration_version"] = r.u8()
        atom.data["profile"] = r.u8()
        atom.data["profile_compatibility"] = r.u8()
        atom.data["level"] = r.u8()
        b = r.u8()
        atom.data["nalu_length_size"] = (b & 0x03) + 1
        num_sps = r.u8() & 0x1F
        sps_list = []
        for _ in range(num_sps):
            ln = r.u16()
            sps_list.append(r.read(ln))
        num_pps = r.u8()
        pps_list = []
        for _ in range(num_pps):
            ln = r.u16()
            pps_list.append(r.read(ln))
        atom.data["sps"] = sps_list
        atom.data["pps"] = pps_list
        if r.remaining() > 0:
            atom.data["ext_bytes"] = r.read(r.remaining())
        return

    if t == "hvcC":
        atom.data["configuration_version"] = r.u8()
        b1 = r.u8()
        atom.data["general_profile_space"] = (b1 >> 6) & 0x3
        atom.data["general_tier_flag"] = (b1 >> 5) & 0x1
        atom.data["general_profile_idc"] = b1 & 0x1F
        atom.data["general_profile_compatibility_flags"] = r.u32()
        atom.data["general_constraint_indicator_flags"] = r.read(6)
        atom.data["general_level_idc"] = r.u8()
        min_spatial_seg = r.u16() & 0x0FFF  # top 4 bit sono reserved(=1111)
        atom.data["min_spatial_segmentation_idc"] = min_spatial_seg
        atom.data["parallelism_type"] = r.u8() & 0x3
        atom.data["chroma_format"] = r.u8() & 0x3
        atom.data["bit_depth_luma_minus8"] = r.u8() & 0x7
        atom.data["bit_depth_chroma_minus8"] = r.u8() & 0x7
        atom.data["avg_frame_rate"] = r.u16()
        b2 = r.u8()
        atom.data["constant_frame_rate"] = (b2 >> 6) & 0x3
        atom.data["num_temporal_layers"] = (b2 >> 3) & 0x7
        atom.data["temporal_id_nested"] = (b2 >> 2) & 0x1
        atom.data["nalu_length_size"] = (b2 & 0x3) + 1
        num_arrays = r.u8()
        arrays = []
        for _ in range(num_arrays):
            ab = r.u8()
            nal_unit_type = ab & 0x3F
            num_nalus = r.u16()
            nalus = []
            for _ in range(num_nalus):
                ln = r.u16()
                nalus.append(r.read(ln))
            arrays.append(dict(nal_unit_type=nal_unit_type, nalus=nalus))
        atom.data["arrays"] = arrays
        return

    if t == "av1C":
        b0 = r.u8()
        atom.data["version"] = b0 & 0x7F
        b1 = r.u8()
        atom.data["seq_profile"] = (b1 >> 5) & 0x7
        atom.data["seq_level_idx_0"] = b1 & 0x1F
        b2 = r.u8()
        atom.data["seq_tier_0"] = (b2 >> 7) & 0x1
        atom.data["high_bitdepth"] = (b2 >> 6) & 0x1
        atom.data["twelve_bit"] = (b2 >> 5) & 0x1
        atom.data["monochrome"] = (b2 >> 4) & 0x1
        atom.data["chroma_subsampling_x"] = (b2 >> 3) & 0x1
        atom.data["chroma_subsampling_y"] = (b2 >> 2) & 0x1
        atom.data["chroma_sample_position"] = b2 & 0x3
        b3 = r.u8()
        atom.data["initial_presentation_delay_present"] = (b3 >> 4) & 0x1
        if r.remaining() > 0:
            atom.data["config_obus"] = r.read(r.remaining())
        return

    if t == "vpcC":
        atom.data["profile"] = r.u8()
        atom.data["level"] = r.u8()
        b = r.u8()
        atom.data["bit_depth"] = (b >> 4) & 0xF
        atom.data["chroma_subsampling"] = (b >> 1) & 0x7
        atom.data["video_full_range_flag"] = b & 0x1
        atom.data["color_primaries"] = r.u8()
        atom.data["transfer_characteristics"] = r.u8()
        atom.data["matrix_coefficients"] = r.u8()
        codec_init_size = r.u16()
        if codec_init_size:
            atom.data["codec_initialization_data"] = r.read(codec_init_size)
        return

    if t == "pasp":
        atom.data["h_spacing"] = r.u32()
        atom.data["v_spacing"] = r.u32()
        return

    if t == "clap":
        atom.data["clean_aperture_width_n"] = r.u32()
        atom.data["clean_aperture_width_d"] = r.u32()
        atom.data["clean_aperture_height_n"] = r.u32()
        atom.data["clean_aperture_height_d"] = r.u32()
        atom.data["horiz_off_n"] = r.u32()
        atom.data["horiz_off_d"] = r.u32()
        atom.data["vert_off_n"] = r.u32()
        atom.data["vert_off_d"] = r.u32()
        return

    if t == "colr":
        colour_type = r.fourcc()
        atom.data["colour_type"] = colour_type
        if colour_type in ("nclx", "nclc"):
            atom.data["colour_primaries"] = r.u16()
            atom.data["transfer_characteristics"] = r.u16()
            atom.data["matrix_coefficients"] = r.u16()
            if colour_type == "nclx" and r.remaining() > 0:
                atom.data["full_range_flag"] = (r.u8() >> 7) & 0x1
        elif colour_type in ("rICC", "prof") and r.remaining() > 0:
            atom.data["icc_profile_size"] = r.remaining()
        return

    if t == "saiz":
        if atom.flags & 1:
            atom.data["aux_info_type"] = r.fourcc()
            atom.data["aux_info_type_parameter"] = r.u32()
        default_sample_info_size = r.u8()
        sample_count = r.u32()
        atom.data["default_sample_info_size"] = default_sample_info_size
        atom.data["sample_count"] = sample_count
        if default_sample_info_size == 0:
            atom.data["sample_info_sizes"] = r.u8_array(min(sample_count, 500_000))
        else:
            atom.data["sample_info_sizes"] = []
        return

    if t == "saio":
        if atom.flags & 1:
            atom.data["aux_info_type"] = r.fourcc()
            atom.data["aux_info_type_parameter"] = r.u32()
        entry_count = r.u32()
        atom.data["entry_count"] = entry_count
        if atom.version == 1:
            atom.data["offsets"] = r.u64_array(min(entry_count, 500_000))
        else:
            atom.data["offsets"] = r.u32_array(min(entry_count, 500_000))
        return

    if t == "senc":
        sample_count = r.u32()
        atom.data["sample_count"] = sample_count
        atom.data["has_subsample_info"] = bool(atom.flags & 0x000002)
        atom.data["_raw_offset"] = payload_start + r.pos
        atom.data["_raw_size"] = r.remaining()
        atom.data["raw_entries"] = r.read(r.remaining())
        return

    if t == "nmhd":
        return

    if t == "hmhd":
        atom.data["max_pdu_size"] = r.u16()
        atom.data["avg_pdu_size"] = r.u16()
        atom.data["max_bitrate"] = r.u32()
        atom.data["avg_bitrate"] = r.u32()
        r.read(4)  # reserved
        return

    if t == "stz2":
        r.read(3)  # reserved
        field_size = r.u8()
        sample_count = r.u32()
        entries = []
        if field_size == 4:
            n = min(sample_count, 500_000)
            i = 0
            while i < n:
                b = r.u8()
                entries.append((b >> 4) & 0xF)
                i += 1
                if i < n:
                    entries.append(b & 0xF)
                    i += 1
        elif field_size == 8:
            entries = r.u8_array(min(sample_count, 500_000))
        elif field_size == 16:
            entries = r.u16_array(min(sample_count, 500_000))
        atom.data["field_size"] = field_size
        atom.data["sample_count"] = sample_count
        atom.data["entries"] = entries
        return

    if t == "ilst":
        atom.children = _parse_children(buf, payload_start, payload_size)
        return

    if t in ILST_NAME_TAGS:
        atom.children = _parse_children(buf, payload_start, payload_size)
        return

    if _looks_like_mdta_index_tag(t, buf, payload_start, payload_size):
        atom.children = _parse_children(buf, payload_start, payload_size)
        return

    if t == "data" and payload_size >= 8:
        type_indicator = r.u32()
        r.read(4)  # locale/reserved
        atom.data["type_indicator"] = type_indicator
        atom.data["value"] = r.read(r.remaining())
        return

    if t == "iods":
        descs = []
        pos = payload_start
        end = payload_start + payload_size
        while pos < end:
            node, pos = _decode_descriptor(buf, pos, end)
            descs.append(node)
        atom.data["descriptors"] = descs
        return

    if t == "leva":
        level_count = r.u8()
        levels = []
        for _ in range(level_count):
            track_id = r.u32()
            b = r.u8()
            padding_flag = (b >> 7) & 0x1
            assignment_type = b & 0x7F
            entry = dict(track_id=track_id, padding_flag=padding_flag, assignment_type=assignment_type)
            if assignment_type == 0:
                entry["grouping_type"] = r.fourcc()
            elif assignment_type == 1:
                entry["grouping_type"] = r.fourcc()
                entry["grouping_type_parameter"] = r.u32()
            elif assignment_type == 4:
                pass  # nessun campo extra
            # assignment_type 2,3 non hanno campi extra nella spec
            levels.append(entry)
        atom.data["level_count"] = level_count
        atom.data["levels"] = levels
        return

    if t == "emsg":
        if atom.version == 1:
            timescale = r.u32()
            presentation_time = r.u64()
            event_duration = r.u32()
            id_ = r.u32()
            scheme_id_uri = r.cstring()
            value = r.cstring()
        else:
            scheme_id_uri = r.cstring()
            value = r.cstring()
            timescale = r.u32()
            presentation_time = r.u32()
            event_duration = r.u32()
            id_ = r.u32()
        atom.data["timescale"] = timescale
        atom.data["presentation_time"] = presentation_time
        atom.data["event_duration"] = event_duration
        atom.data["id"] = id_
        atom.data["scheme_id_uri"] = scheme_id_uri
        atom.data["value"] = value
        atom.data["message_data"] = r.read(r.remaining())
        return

    if t == "keys":
        entry_count = r.u32()
        entries = []
        for _ in range(min(entry_count, 10_000)):
            key_size = r.u32()
            key_namespace = r.fourcc()
            value_len = key_size - 8
            key_value = r.read(value_len).decode("utf-8", errors="replace")
            entries.append(dict(key_namespace=key_namespace, key_value=key_value))
        atom.data["entry_count"] = entry_count
        atom.data["entries"] = entries
        return

    return


class _Ctx:
    TOP_LEVEL = 0
    ATOM = 1
    ARRAY = 2
    COMPACT_OBJECT = 3

    def __init__(self, type_):
        self.type = type_
        self.array_index = 0


class TextInspector:
    def __init__(self, verbosity=0):
        self.verbosity = verbosity
        self.out = io.StringIO()
        self.contexts = [_Ctx(_Ctx.TOP_LEVEL)]

    def _last(self):
        return self.contexts[-1]

    def _print_prefix(self):
        last = self._last()
        if last.type == _Ctx.COMPACT_OBJECT:
            if last.array_index:
                self.out.write(", ")
            last.array_index += 1
            return
        indent = (len(self.contexts) - 1) * 2
        self.out.write(" " * indent)
        if last.type == _Ctx.ARRAY:
            self.out.write(f"({last.array_index:>8}) ")
            last.array_index += 1

    def _print_suffix(self):
        if self._last().type != _Ctx.COMPACT_OBJECT:
            self.out.write("\n")

    def start_atom(self, name, version, flags, header_size, size):
        self._print_prefix()
        self.contexts.append(_Ctx(_Ctx.ATOM))
        extra = ""
        if header_size in (12, 20, 28):
            if version and flags:
                extra = f", version={version}, flags={flags:x}"
            elif version:
                extra = f", version={version}"
            elif flags:
                extra = f", flags={flags:x}"
        self.out.write(f"[{name}] size={header_size}+{size - header_size}{extra}")
        self._print_suffix()

    def end_atom(self):
        self.contexts.pop()

    def start_array(self, name=None):
        self._print_prefix()
        self.contexts.append(_Ctx(_Ctx.ARRAY))
        if name:
            self.out.write(f"{name}:")
        self._print_suffix()

    def end_array(self):
        self.contexts.pop()

    def start_object(self, name=None, compact=True):
        self._print_prefix()
        self.contexts.append(_Ctx(_Ctx.COMPACT_OBJECT if compact else _Ctx.ATOM))
        if name:
            self.out.write(f"{name}: ")
        self._print_suffix()

    def end_object(self):
        if self._last().type == _Ctx.COMPACT_OBJECT:
            self.out.write("\n")
        self.contexts.pop()

    def add_field(self, name, value, hex_=False):
        self._print_prefix()
        if name:
            self.out.write(f"{name} = ")
        if isinstance(value, str):
            self.out.write(value)
        else:
            self.out.write(f"{value:x}" if hex_ else f"{value}")
        self._print_suffix()

    def add_field_f(self, name, value):
        self._print_prefix()
        if name:
            self.out.write(f"{name} = ")
        self.out.write(f"{value:f}")
        self._print_suffix()


class JsonInspector:
    def __init__(self, verbosity=0):
        self.verbosity = verbosity
        self.out = io.StringIO()
        self.out.write("[\n")
        self.contexts = [{"type": "top", "field_count": 0, "children_count": 0}]

    def _prefix(self):
        return "  " * len(self.contexts)

    def _on_field_added(self):
        last = self.contexts[-1]
        if last["field_count"]:
            self.out.write(",\n")
        last["field_count"] += 1

    def _field_name(self, name):
        if name is None:
            return
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        self.out.write(f'"{escaped}": ')

    def start_atom(self, name, version, flags, header_size, size):
        self._on_field_added()
        self.contexts[-1]["children_count"] += 1
        if self.contexts[-1]["type"] == "atom" and self.contexts[-1]["children_count"] == 1:
            self.out.write(self._prefix())
            self.out.write('"children":[ \n')

        self.out.write(self._prefix())
        self.out.write("{\n")
        self.contexts.append({"type": "atom", "field_count": 0, "children_count": 0})

        self._on_field_added()
        self.out.write(self._prefix())
        self._field_name("name")
        self.out.write(f'"{name}"')

        self._on_field_added()
        self.out.write(self._prefix())
        self._field_name("header_size")
        self.out.write(str(header_size))

        self._on_field_added()
        self.out.write(self._prefix())
        self._field_name("size")
        self.out.write(str(size))

        if version:
            self._on_field_added()
            self.out.write(self._prefix())
            self._field_name("version")
            self.out.write(str(version))

        if flags:
            self._on_field_added()
            self.out.write(self._prefix())
            self._field_name("flags")
            self.out.write(str(flags))

    def end_atom(self):
        ctx = self.contexts.pop()
        if ctx["children_count"]:
            self.out.write("]")
        self.out.write("\n")
        self.out.write(self._prefix())
        self.out.write("}")

    def start_array(self, name=None):
        self._on_field_added()
        self.out.write(self._prefix())
        self._field_name(name)
        self.out.write("[\n")
        self.contexts.append({"type": "array", "field_count": 0, "children_count": 0})

    def end_array(self):
        self.contexts.pop()
        self.out.write("\n")
        self.out.write(self._prefix())
        self.out.write("]")

    def start_object(self, name=None, compact=True):
        self._on_field_added()
        self.out.write(self._prefix())
        self._field_name(name)
        self.out.write("{\n")
        self.contexts.append({"type": "object", "field_count": 0, "children_count": 0})

    def end_object(self):
        self.contexts.pop()
        self.out.write("\n")
        self.out.write(self._prefix())
        self.out.write("}")

    def add_field(self, name, value, hex_=False):
        self._on_field_added()
        self.out.write(self._prefix())
        self._field_name(name)
        if isinstance(value, str):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            self.out.write(f'"{escaped}"')
        else:
            self.out.write(str(value))

    def add_field_f(self, name, value):
        self._on_field_added()
        self.out.write(self._prefix())
        self._field_name(name)
        self.out.write(f"{value:f}")


def _inspect_children(atom, insp):
    for child in atom.children:
        _inspect_atom(child, insp)


def _inspect_fields(atom, insp):
    t = atom.type
    d = atom.data
    v = insp.verbosity

    if t in CONTAINER_TYPES or t == "meta" or t == "tref":
        _inspect_children(atom, insp)
        return

    if t in ("ftyp", "styp"):
        insp.add_field("major_brand", d["major_brand"])
        insp.add_field("minor_version", d["minor_version"], hex_=True)
        for b in d["compatible_brands"]:
            insp.add_field("compatible_brand", b)
        return

    if t == "mvhd":
        insp.add_field("timescale", d["timescale"])
        insp.add_field("duration", d["duration"])
        ms = int(d["duration"] * 1000 / d["timescale"]) if d["timescale"] else 0
        insp.add_field("duration(ms)", ms)
        return

    if t == "tkhd":
        enabled = 1 if (atom.flags & 1) else 0
        insp.add_field("enabled", enabled)
        insp.add_field("id", d["track_id"])
        insp.add_field("duration", d["duration"])
        if v > 0:
            insp.add_field("volume", d["volume"])
            insp.add_field("layer", d["layer"])
            insp.add_field("alternate_group", d["alternate_group"])
            for i in range(9):
                insp.add_field_f(f"matrix_{i}", d["matrix"][i] / 65536.0)
        insp.add_field_f("width", d["width"] / 65536.0)
        insp.add_field_f("height", d["height"] / 65536.0)
        return

    if t == "mdhd":
        insp.add_field("timescale", d["timescale"])
        insp.add_field("duration", d["duration"])
        ms = int(d["duration"] * 1000 / d["timescale"]) if d["timescale"] else 0
        insp.add_field("duration(ms)", ms)
        insp.add_field("language", d["language"])
        return

    if t == "hdlr":
        insp.add_field("handler_type", d["handler_type"])
        insp.add_field("handler_name", d["handler_name"])
        return

    if t == "vmhd":
        insp.add_field("graphics_mode", d["graphics_mode"])
        op = d["op_color"]
        insp.add_field("op_color", f"{op[0]:04x},{op[1]:04x},{op[2]:04x}")
        return

    if t == "smhd":
        insp.add_field("balance", d["balance"])
        return

    if t == "stsd":
        insp.add_field("entry_count", len(atom.children))
        _inspect_children(atom, insp)
        return

    if t == "dref":
        _inspect_children(atom, insp)
        return

    if t in ("url ", "urn "):
        if t == "url ":
            if atom.flags & 1:
                insp.add_field("location", "[local to file]")
            else:
                insp.add_field("location", d.get("location", ""))
        else:
            insp.add_field("location", d.get("location", ""))
        return

    if t == "stts":
        insp.add_field("entry_count", d["entry_count"])
        if v >= 1:
            insp.start_array("entries")
            for sample_count, sample_duration in d["entries"]:
                insp.start_object(compact=True)
                insp.add_field("sample_count", sample_count)
                insp.add_field("sample_duration", sample_duration)
                insp.end_object()
            insp.end_array()
        return

    if t == "ctts":
        insp.add_field("entry_count", d["entry_count"])
        if v >= 2:
            insp.start_array("entries")
            for count, off in d["entries"]:
                insp.start_object(compact=True)
                insp.add_field("count", count)
                insp.add_field("offset", off)
                insp.end_object()
            insp.end_array()
        return

    if t == "stsc":
        insp.add_field("entry_count", d["entry_count"])
        if v >= 1:
            insp.start_array("entries")
            for e in d["entries"]:
                insp.start_object(compact=True)
                insp.add_field("first_chunk", e["first_chunk"])
                insp.add_field("first_sample", e["first_sample"])
                insp.add_field("chunk_count", e["chunk_count"])
                insp.add_field("samples_per_chunk", e["samples_per_chunk"])
                insp.add_field("sample_desc_index", e["sample_desc_index"])
                insp.end_object()
            insp.end_array()
        return

    if t == "stsz":
        insp.add_field("sample_size", d["sample_size"])
        insp.add_field("sample_count", d["sample_count"])
        if v >= 2:
            insp.start_array("entries")
            for size_ in d["entries"]:
                insp.add_field(None, size_)
            insp.end_array()
        return

    if t == "stco":
        insp.add_field("entry_count", d["entry_count"])
        if v >= 1:
            insp.start_array("entries")
            for off in d["entries"]:
                insp.add_field(None, off)
            insp.end_array()
        return

    if t == "co64":
        insp.add_field("entry_count", d["entry_count"])
        if v >= 1:
            insp.start_array("entries")
            for off in d["entries"]:
                insp.add_field(None, off)
            insp.end_array()
        return

    if t == "stss":
        insp.add_field("entry_count", d["entry_count"])
        return

    if t == "elst":
        insp.add_field("entry_count", d["entry_count"])
        for seg_duration, media_time, media_rate in d["entries"]:
            insp.add_field("entry/segment duration", seg_duration)
            insp.add_field("entry/media time", media_time)
            insp.add_field("entry/media rate", media_rate)
        return

    if t == "mfhd":
        insp.add_field("sequence number", d["sequence_number"])
        return

    if t == "tfhd":
        insp.add_field("track ID", d["track_id"])
        if "base_data_offset" in d:
            insp.add_field("base data offset", d["base_data_offset"])
        if "sample_description_index" in d:
            insp.add_field("sample description index", d["sample_description_index"])
        if "default_sample_duration" in d:
            insp.add_field("default sample duration", d["default_sample_duration"])
        if "default_sample_size" in d:
            insp.add_field("default sample size", d["default_sample_size"])
        if "default_sample_flags" in d:
            insp.add_field("default sample flags", d["default_sample_flags"], hex_=True)
        return

    if t == "tfdt":
        insp.add_field("base media decode time", d["base_media_decode_time"])
        return

    if t == "trun":
        insp.add_field("sample count", len(d["entries"]))
        if "data_offset" in d:
            insp.add_field("data offset", d["data_offset"])
        if "first_sample_flags" in d:
            insp.add_field("first sample flags", d["first_sample_flags"], hex_=True)
        if v > 0:
            insp.start_array("entries")
            for e in d["entries"]:
                insp.start_object(compact=True)
                if "sample_duration" in e:
                    insp.add_field("sample_duration" if v >= 2 else "d", e["sample_duration"])
                if "sample_size" in e:
                    insp.add_field("sample_size" if v >= 2 else "s", e["sample_size"])
                if "sample_flags" in e:
                    insp.add_field("sample_flags" if v >= 2 else "f", e["sample_flags"])
                if "sample_composition_time_offset" in e:
                    insp.add_field(
                        "sample_composition_time_offset" if v >= 2 else "c", e["sample_composition_time_offset"]
                    )
                insp.end_object()
            insp.end_array()
        return

    if t == "sidx":
        insp.add_field("reference_ID", d["reference_id"])
        insp.add_field("timescale", d["timescale"])
        insp.add_field("earliest_presentation_time", d["earliest_pts"])
        insp.add_field("first_offset", d["first_offset"])
        if v >= 1:
            insp.start_array("entries")
            for e in d["entries"]:
                insp.start_object(compact=True)
                insp.add_field("reference_type", e["reference_type"])
                insp.add_field("referenced_size", e["referenced_size"])
                insp.add_field("subsegment_duration", e["subsegment_duration"])
                insp.add_field("starts_with_SAP", e["starts_with_sap"])
                insp.add_field("SAP_type", e["sap_type"])
                insp.add_field("SAP_delta_time", e["sap_delta_time"])
                insp.end_object()
            insp.end_array()
        return

    if t == "mehd":
        insp.add_field("duration", d["duration"])
        return

    if t == "trex":
        insp.add_field("track id", d["track_id"])
        insp.add_field("default sample description index", d["default_sample_description_index"])
        insp.add_field("default sample duration", d["default_sample_duration"])
        insp.add_field("default sample size", d["default_sample_size"])
        insp.add_field("default sample flags", d["default_sample_flags"], hex_=True)
        return

    if t == "esds":
        for node in d["descriptors"]:
            _inspect_descriptor(node, insp)
        return

    if t == "pssh":
        insp.add_field("system_id", _hex_brackets(d["system_id"]))
        for kid in d.get("kids", []):
            insp.add_field("kid", _hex_brackets(kid))
        insp.add_field("data_size", d["data_size"])
        return

    if t == "frma":
        insp.add_field("original_format", d["original_format"])
        return

    if t == "schm":
        insp.add_field("scheme_type", d["scheme_type"])
        insp.add_field("scheme_version", d["scheme_version"])
        if "scheme_uri" in d:
            insp.add_field("scheme_uri", d["scheme_uri"])
        return

    if t == "tenc":
        insp.add_field("default_isProtected", d["default_isProtected"])
        insp.add_field("default_Per_Sample_IV_Size", d["default_Per_Sample_IV_Size"])
        insp.add_field("default_KID", _hex_brackets(d["default_KID"]))
        if "default_constant_IV" in d:
            insp.add_field("default_constant_IV", _hex_brackets(d["default_constant_IV"]))
        return

    if t in VIDEO_SAMPLE_ENTRY_TYPES or t in AUDIO_SAMPLE_ENTRY_TYPES:
        insp.add_field("data_reference_index", d.get("data_reference_index", 0))
        if d.get("_kind") == "video":
            insp.add_field("width", d["width"])
            insp.add_field("height", d["height"])
            insp.add_field("compressor", d["compressor"])
        elif d.get("_kind") == "audio":
            insp.add_field("channel_count", d["channel_count"])
            insp.add_field("sample_size", d["sample_size"])
            insp.add_field("sample_rate", d["sample_rate"])
            if v > 0 and d.get("version"):
                insp.add_field("qt_version", d["version"])
                if d["version"] == 1:
                    insp.add_field("samples_per_packet", d["samples_per_packet"])
                    insp.add_field("bytes_per_packet", d["bytes_per_packet"])
                    insp.add_field("bytes_per_frame", d["bytes_per_frame"])
                    insp.add_field("bytes_per_sample", d["bytes_per_sample"])
                elif d["version"] == 2:
                    insp.add_field_f("sample_rate_64", d["sample_rate_64"])
                    insp.add_field("const_bits_per_channel", d["const_bits_per_channel"])
                    insp.add_field("format_specific_flags", d["format_specific_flags"], hex_=True)
                    insp.add_field("const_bytes_per_audio_packet", d["const_bytes_per_audio_packet"])
                    insp.add_field("const_lpcm_frames_per_audio_packet", d["const_lpcm_frames_per_audio_packet"])
        _inspect_children(atom, insp)
        return

    if t == "avcC":
        insp.add_field("configuration_version", d["configuration_version"])
        insp.add_field("profile", d["profile"], hex_=True)
        insp.add_field("profile_compatibility", d["profile_compatibility"], hex_=True)
        insp.add_field("level", d["level"])
        insp.add_field("nalu_length_size", d["nalu_length_size"])
        if v > 0:
            for sps in d["sps"]:
                insp.add_field("sps", _hex_brackets(sps))
            for pps in d["pps"]:
                insp.add_field("pps", _hex_brackets(pps))
        return

    if t == "hvcC":
        insp.add_field("configuration_version", d["configuration_version"])
        insp.add_field("general_profile_space", d["general_profile_space"])
        insp.add_field("general_tier_flag", d["general_tier_flag"])
        insp.add_field("general_profile_idc", d["general_profile_idc"])
        insp.add_field("general_profile_compatibility_flags", f"{d['general_profile_compatibility_flags']:08x}")
        insp.add_field("general_constraint_indicator_flags", _hex_brackets(d["general_constraint_indicator_flags"]))
        insp.add_field("general_level_idc", d["general_level_idc"])
        insp.add_field("min_spatial_segmentation_idc", d["min_spatial_segmentation_idc"])
        insp.add_field("parallelism_type", d["parallelism_type"])
        insp.add_field("chroma_format", d["chroma_format"])
        insp.add_field("bit_depth_luma_minus8", d["bit_depth_luma_minus8"])
        insp.add_field("bit_depth_chroma_minus8", d["bit_depth_chroma_minus8"])
        insp.add_field("avg_frame_rate", d["avg_frame_rate"])
        insp.add_field("constant_frame_rate", d["constant_frame_rate"])
        insp.add_field("num_temporal_layers", d["num_temporal_layers"])
        insp.add_field("temporal_id_nested", d["temporal_id_nested"])
        insp.add_field("nalu_length_size", d["nalu_length_size"])
        if v > 0:
            for arr in d["arrays"]:
                insp.add_field("nal_unit_type", arr["nal_unit_type"])
                for nalu in arr["nalus"]:
                    insp.add_field("nalu", _hex_brackets(nalu))
        return

    if t == "av1C":
        insp.add_field("version", d["version"])
        insp.add_field("seq_profile", d["seq_profile"])
        insp.add_field("seq_level_idx_0", d["seq_level_idx_0"])
        insp.add_field("seq_tier_0", d["seq_tier_0"])
        insp.add_field("high_bitdepth", d["high_bitdepth"])
        insp.add_field("twelve_bit", d["twelve_bit"])
        insp.add_field("monochrome", d["monochrome"])
        insp.add_field("chroma_subsampling_x", d["chroma_subsampling_x"])
        insp.add_field("chroma_subsampling_y", d["chroma_subsampling_y"])
        insp.add_field("chroma_sample_position", d["chroma_sample_position"])
        insp.add_field("initial_presentation_delay_present", d["initial_presentation_delay_present"])
        return

    if t == "vpcC":
        insp.add_field("profile", d["profile"])
        insp.add_field("level", d["level"])
        insp.add_field("bit_depth", d["bit_depth"])
        insp.add_field("chroma_subsampling", d["chroma_subsampling"])
        insp.add_field("video_full_range_flag", d["video_full_range_flag"])
        insp.add_field("color_primaries", d["color_primaries"])
        insp.add_field("transfer_characteristics", d["transfer_characteristics"])
        insp.add_field("matrix_coefficients", d["matrix_coefficients"])
        return

    if t == "pasp":
        insp.add_field("h_spacing", d["h_spacing"])
        insp.add_field("v_spacing", d["v_spacing"])
        return

    if t == "clap":
        insp.add_field("clean_aperture_width_n", d["clean_aperture_width_n"])
        insp.add_field("clean_aperture_width_d", d["clean_aperture_width_d"])
        insp.add_field("clean_aperture_height_n", d["clean_aperture_height_n"])
        insp.add_field("clean_aperture_height_d", d["clean_aperture_height_d"])
        insp.add_field("horiz_off_n", d["horiz_off_n"])
        insp.add_field("horiz_off_d", d["horiz_off_d"])
        insp.add_field("vert_off_n", d["vert_off_n"])
        insp.add_field("vert_off_d", d["vert_off_d"])
        return

    if t == "colr":
        insp.add_field("colour_type", d["colour_type"])
        if "colour_primaries" in d:
            insp.add_field("colour_primaries", d["colour_primaries"])
            insp.add_field("transfer_characteristics", d["transfer_characteristics"])
            insp.add_field("matrix_coefficients", d["matrix_coefficients"])
        if "full_range_flag" in d:
            insp.add_field("full_range_flag", d["full_range_flag"])
        if "icc_profile_size" in d:
            insp.add_field("icc_profile_size", d["icc_profile_size"])
        return

    if t == "saiz":
        if "aux_info_type" in d:
            insp.add_field("aux_info_type", d["aux_info_type"])
            insp.add_field("aux_info_type_parameter", d["aux_info_type_parameter"])
        insp.add_field("default_sample_info_size", d["default_sample_info_size"])
        insp.add_field("sample_count", d["sample_count"])
        if v > 0 and d["default_sample_info_size"] == 0:
            insp.start_array("sample_info_sizes")
            for s in d["sample_info_sizes"]:
                insp.add_field(None, s)
            insp.end_array()
        return

    if t == "saio":
        if "aux_info_type" in d:
            insp.add_field("aux_info_type", d["aux_info_type"])
            insp.add_field("aux_info_type_parameter", d["aux_info_type_parameter"])
        insp.add_field("entry_count", d["entry_count"])
        if v > 0:
            insp.start_array("offsets")
            for off in d["offsets"]:
                insp.add_field(None, off)
            insp.end_array()
        return

    if t == "senc":
        insp.add_field("sample_count", d["sample_count"])
        if "constant_iv" in d:
            insp.add_field("constant_IV", _hex_brackets(d["constant_iv"]))
        elif "entries" in d and v > 0:
            insp.add_field("resolved_iv_size", d["resolved_iv_size"])
            insp.start_array("entries")
            for e in d["entries"]:
                insp.start_object(compact=True)
                insp.add_field("InitializationVector", _hex_brackets(e["iv"]))
                if "subsamples" in e:
                    subs = ",".join(f"({c}/{en})" for c, en in e["subsamples"])
                    insp.add_field("subsamples", subs)
                insp.end_object()
            insp.end_array()
        return

    if t == "iods":
        for node in d["descriptors"]:
            _inspect_descriptor(node, insp)
        return

    if t == "leva":
        insp.add_field("level_count", d["level_count"])
        insp.start_array("levels")
        for lvl in d["levels"]:
            insp.start_object(compact=True)
            insp.add_field("track_id", lvl["track_id"])
            insp.add_field("assignment_type", lvl["assignment_type"])
            if "grouping_type" in lvl:
                insp.add_field("grouping_type", lvl["grouping_type"])
            if "grouping_type_parameter" in lvl:
                insp.add_field("grouping_type_parameter", lvl["grouping_type_parameter"])
            insp.end_object()
        insp.end_array()
        return

    if t == "emsg":
        insp.add_field("timescale", d["timescale"])
        insp.add_field("presentation_time", d["presentation_time"])
        insp.add_field("event_duration", d["event_duration"])
        insp.add_field("id", d["id"])
        insp.add_field("scheme_id_uri", d["scheme_id_uri"])
        insp.add_field("value", d["value"])
        return

    if t == "keys":
        insp.add_field("entry_count", d["entry_count"])
        if v > 0:
            insp.start_array("entries")
            for e in d["entries"]:
                insp.start_object(compact=True)
                insp.add_field("key_namespace", e["key_namespace"])
                insp.add_field("key_value", e["key_value"])
                insp.end_object()
            insp.end_array()
        return

    if t in TRACK_REFERENCE_TYPES:
        insp.start_array("track_ids")
        for tid in d.get("track_ids", []):
            insp.add_field(None, tid)
        insp.end_array()
        return

    if t == "nmhd":
        return

    if t == "hmhd":
        insp.add_field("max_pdu_size", d["max_pdu_size"])
        insp.add_field("avg_pdu_size", d["avg_pdu_size"])
        insp.add_field("max_bitrate", d["max_bitrate"])
        insp.add_field("avg_bitrate", d["avg_bitrate"])
        return

    if t == "stz2":
        insp.add_field("field_size", d["field_size"])
        insp.add_field("sample_count", d["sample_count"])
        if v >= 2:
            insp.start_array("entries")
            for size_ in d["entries"]:
                insp.add_field(None, size_)
            insp.end_array()
        return

    if t == "ilst":
        _inspect_children(atom, insp)
        return

    if t in ILST_NAME_TAGS or "_resolved_key_name" in d:
        _inspect_children(atom, insp)
        return

    if t == "data":
        insp.add_field("type_indicator", d.get("type_indicator", 0))
        value = d.get("value", b"")
        if d.get("type_indicator") == 1:
            insp.add_field("value", value.decode("utf-8", errors="replace"))
        else:
            insp.add_field("value", _hex_brackets(value))
        return

    return


def _inspect_atom(atom, insp):
    display_name = atom.data.get("_resolved_key_name", atom.type)
    insp.start_atom(display_name, atom.version, atom.flags, atom.header_size, atom.total_size)
    _inspect_fields(atom, insp)
    insp.end_atom()


def _find_children(atom, type_):
    return [c for c in atom.children if c.type == type_]


def _find_first(atom, type_):
    for c in atom.children:
        if c.type == type_:
            return c
    return None


def _find_descendant_path(atom, path):
    cur = atom
    for t in path:
        cur = _find_first(cur, t)
        if cur is None:
            return None
    return cur


def _collect_tenc_by_track(moov):
    result = {}
    if moov is None:
        return result
    for trak in _find_children(moov, "trak"):
        tkhd = _find_descendant_path(trak, [])
        tkhd = _find_first(trak, "tkhd")
        if tkhd is None:
            continue
        track_id = tkhd.data.get("track_id")
        stbl = _find_descendant_path(trak, ["mdia", "minf", "stbl"])
        if stbl is None:
            continue
        stsd = _find_first(stbl, "stsd")
        if stsd is None:
            continue
        for sample_entry in stsd.children:
            sinf = _find_first(sample_entry, "sinf")
            if sinf is None:
                continue
            schi = _find_first(sinf, "schi")
            if schi is None:
                continue
            tenc = _find_first(schi, "tenc")
            if tenc is None:
                continue
            iv_size = tenc.data.get("default_Per_Sample_IV_Size", 0)
            const_iv = tenc.data.get("default_constant_IV")
            if track_id is not None:
                result[track_id] = (iv_size, const_iv)
    return result


def _redecode_senc(senc_atom, buf, iv_size):
    d = senc_atom.data
    if iv_size == 0:
        return

    raw = d.get("raw_entries", b"")
    pos = 0
    n = len(raw)
    entries = []
    has_subsample = d.get("has_subsample_info", False)
    sample_count = d.get("sample_count", 0)
    for _ in range(sample_count):
        if pos + iv_size > n:
            break
        iv = raw[pos : pos + iv_size]
        pos += iv_size
        entry = {"iv": iv}
        if has_subsample:
            if pos + 2 > n:
                break
            subsample_count = struct.unpack_from(">H", raw, pos)[0]
            pos += 2
            take = min(subsample_count, (n - pos) // 6)
            if take:
                subsamples = list(struct.iter_unpack(">HI", raw[pos : pos + 6 * take]))
                pos += 6 * take
            else:
                subsamples = []
            entry["subsamples"] = subsamples
        entries.append(entry)
    d["entries"] = entries
    d["resolved_iv_size"] = iv_size


def _resolve_senc_boxes(top_atoms, decode_entries=True):
    moov = None
    for a in top_atoms:
        if a.type == "moov":
            moov = a
            break
    tenc_by_track = _collect_tenc_by_track(moov)
    if not tenc_by_track:
        return

    def walk(atom):
        if atom.type == "moof":
            for traf in _find_children(atom, "traf"):
                tfhd = _find_first(traf, "tfhd")
                senc = _find_first(traf, "senc")
                if tfhd is None or senc is None:
                    continue
                track_id = tfhd.data.get("track_id")
                if track_id in tenc_by_track:
                    iv_size, const_iv = tenc_by_track[track_id]
                    if iv_size:
                        if decode_entries:
                            _redecode_senc(senc, None, iv_size)
                    elif const_iv:
                        senc.data["constant_iv"] = const_iv
        for child in atom.children:
            walk(child)

    for a in top_atoms:
        walk(a)


def _resolve_mdta_tags(top_atoms):
    def walk(atom):
        if atom.type == "meta":
            keys_box = _find_first(atom, "keys")
            ilst_box = _find_first(atom, "ilst")
            if keys_box is not None and ilst_box is not None:
                entries = keys_box.data.get("entries", [])
                for tag in ilst_box.children:
                    if tag.type in ILST_NAME_TAGS:
                        continue  # tag classico iTunes, non un indice mdta
                    raw_index = struct.unpack(">I", tag.type.encode("latin-1"))[0]
                    if 1 <= raw_index <= len(entries):
                        e = entries[raw_index - 1]
                        tag.data["_resolved_key_name"] = f"{e['key_namespace']}.{e['key_value']}"
        for child in atom.children:
            walk(child)

    for a in top_atoms:
        walk(a)


def _open_buffer(filename: str):
    f = open(filename, "rb")
    try:
        size = f.seek(0, io.SEEK_END)
        if size == 0:
            f.close()
            return b"", None
        f.seek(0)
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)

        def closer():
            mm.close()
            f.close()

        return mm, closer
    except (ValueError, OSError):
        f.seek(0)
        data = f.read()
        f.close()
        return data, None


def parse_file(filename: str, decode_senc_entries: bool = True):
    data, closer = _open_buffer(filename)
    try:
        atoms = []
        offset = 0
        limit = len(data)
        while offset < limit:
            try:
                atom, next_offset = parse_box(data, offset, limit)
            except Exception:
                break
            if next_offset <= offset:
                break
            atoms.append(atom)
            offset = next_offset
        _resolve_senc_boxes(atoms, decode_entries=decode_senc_entries)
        _resolve_mdta_tags(atoms)
        return atoms
    finally:
        if closer is not None:
            closer()


def dump_to_string(filename: str, format: str = "text", verbosity: int = 0) -> str:
    atoms = parse_file(filename, decode_senc_entries=verbosity > 0)
    if format == "json":
        insp = JsonInspector(verbosity=verbosity)
        for a in atoms:
            _inspect_atom(a, insp)
        insp.out.write("\n]\n")
        return insp.out.getvalue()
    else:
        insp = TextInspector(verbosity=verbosity)
        for a in atoms:
            _inspect_atom(a, insp)
        return insp.out.getvalue()


def mp4dump(filename: str, output=None, format: str = "text", verbosity: int = 0):
    if output is None:
        output = sys.stdout
    text = dump_to_string(filename, format=format, verbosity=verbosity)
    output.write(text)
    if not text.endswith("\n"):
        output.write("\n")
