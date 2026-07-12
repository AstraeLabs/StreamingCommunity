# 01.04.25

import os
import shutil
import struct
import tempfile
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from VibraVid.setup import get_bento4_decrypt_path
from VibraVid.core.decryptor import Decryptor
from VibraVid.core.muxing.helper.video import _segment_number

from .util._ism_boxes import build_ism_init_segment, ISM_TIMESCALE
from .util._verify import verify_decrypted_media


logger = logging.getLogger("manual")


class IsmPostprocMixin:
    @staticmethod
    def _get_mp4fragment_path() -> Optional[str]:
        decrypt_path = get_bento4_decrypt_path()
        if decrypt_path and os.path.isfile(decrypt_path):
            base_dir = os.path.dirname(decrypt_path)
            for name in ("mp4fragment", "mp4fragment.exe"):
                candidate = os.path.join(base_dir, name)
                if os.path.isfile(candidate):
                    print(f"Found mp4fragment at {candidate} based on Bento4 decrypt path")
                    return candidate
        print(f"mp4fragment: {shutil.which('mp4fragment')}")
        return shutil.which("mp4fragment")

    @staticmethod
    def _read_fragment_track_id(data: bytes) -> Optional[int]:
        """Return the ``track_ID`` declared in the first fragment's moof>traf>tfhd."""
        buf = memoryview(data)

        def _iter(start: int, end: int):
            off = start
            while off + 8 <= end:
                size = struct.unpack(">I", buf[off:off + 4])[0]
                typ = bytes(buf[off + 4:off + 8])
                hdr = 8
                if size == 1:
                    size = struct.unpack(">Q", buf[off + 8:off + 16])[0]
                    hdr = 16
                elif size == 0:
                    size = end - off
                if size < hdr or off + size > end:
                    return
                yield off, size, typ, hdr
                off += size

        for moof_off, moof_size, moof_typ, moof_hdr in _iter(0, len(data)):
            if moof_typ != b"moof":
                continue
            for traf_off, traf_size, traf_typ, traf_hdr in _iter(moof_off + moof_hdr, moof_off + moof_size):
                if traf_typ != b"traf":
                    continue
                for tf_off, tf_size, tf_typ, tf_hdr in _iter(traf_off + traf_hdr, traf_off + traf_size):
                    if tf_typ != b"tfhd":
                        continue
                    p = tf_off + tf_hdr  # skip box header; then version/flags (4) + track_ID (4)
                    return struct.unpack(">I", buf[p + 4:p + 8])[0]
        
        return None

    @staticmethod
    def _build_ism_init(stream, kid_hex: str, track_id: Optional[int] = None) -> bytes:
        """Build a valid ftyp + moov for the encrypted ISM stream."""
        media_segs = [s for s in stream.segments if s.seg_type == "media"]
        seg_count = max(len(media_segs), 1)
        if stream.duration and stream.duration > 0:
            duration = int(stream.duration * ISM_TIMESCALE)
        else:
            # 20s per fragment is the typical ISM segment length
            duration = 20 * ISM_TIMESCALE * seg_count

        codec = (stream.codecs or "").lower() or "hvc1"
        extra = {"track_id": track_id} if track_id else {}

        if stream.type == "video":
            return build_ism_init_segment(
                stream_type="video",
                duration=duration,
                codec=codec,
                codec_private=stream.codec_private_data or b"",
                kid_hex=kid_hex,
                width=stream.width or 0,
                height=stream.height or 0,
                **extra,
            )

        if stream.type == "audio":
            # Normalize sample rate and channels to integers (manifests may provide strings)
            sr_val = 0
            try:
                sr_src = getattr(stream, "sample_rate", None)
                if sr_src is not None:
                    sr_val = int(sr_src)
            except Exception:
                try:
                    sr_val = int(float(sr_src))
                except Exception:
                    sr_val = 0

            ch_val = 0
            try:
                ch_src = getattr(stream, "channels", None)
                if ch_src is not None and ch_src != "":
                    ch_val = int(ch_src)
            except Exception:
                try:
                    ch_val = int(float(ch_src))
                except Exception:
                    import re
                    m = re.search(r"(\d+)", str(ch_src or ""))
                    ch_val = int(m.group(1)) if m else 0

            return build_ism_init_segment(
                stream_type="audio",
                duration=duration,
                codec=codec,
                codec_private=stream.codec_private_data or b"",
                kid_hex=kid_hex,
                sample_rate=sr_val,
                channels=ch_val,
                language=getattr(stream, "language", "und") or "und",
                **extra,
            )

        raise ValueError(f"Unsupported ISM stream type: {stream.type!r}")

    def _probe_ism_init(self, stream) -> None:
        """Best-effort DRM probe on the manifest-synthesized init segment,
        run as soon as the KID is known — before any segment is downloaded."""
        try:
            init_data = self._build_ism_init(stream, stream.drm.kid)
        except Exception as exc:
            logger.debug(f"ISM early init probe skipped: {exc}")
            return

        tmp_path = Path(tempfile.gettempdir()) / f"ism_init_probe_{uuid.uuid4().hex}.mp4"
        try:
            tmp_path.write_bytes(init_data)
            self._probe_media_file(tmp_path)
        except Exception as exc:
            logger.debug(f"ISM early init probe failed: {exc}")
        finally:
            try:
                tmp_path.unlink()
            except Exception:
                pass

    @staticmethod
    def _normalize_ism_fragment_sdi(data: bytes) -> bytes:
        """Force ``tfhd.sample_description_index`` to 1 in every fragment."""
        buf = bytearray(data)

        def _iter(start: int, end: int):
            off = start
            while off + 8 <= end:
                size = struct.unpack(">I", buf[off:off + 4])[0]
                typ = bytes(buf[off + 4:off + 8])
                hdr = 8
                if size == 1:
                    size = struct.unpack(">Q", buf[off + 8:off + 16])[0]
                    hdr = 16
                elif size == 0:
                    size = end - off
                if size < hdr or off + size > end:
                    return
                yield off, size, typ, hdr
                off += size

        for moof_off, moof_size, moof_typ, moof_hdr in _iter(0, len(buf)):
            if moof_typ != b"moof":
                continue
            for traf_off, traf_size, traf_typ, traf_hdr in _iter(moof_off + moof_hdr, moof_off + moof_size):
                if traf_typ != b"traf":
                    continue
                for tf_off, tf_size, tf_typ, tf_hdr in _iter(traf_off + traf_hdr, traf_off + traf_size):
                    if tf_typ != b"tfhd":
                        continue
                    p = tf_off + tf_hdr
                    flags = struct.unpack(">I", buf[p:p + 4])[0] & 0xFFFFFF
                    q = p + 4 + 4  # skip version/flags + track_ID
                    if flags & 0x000001:  # base-data-offset-present
                        q += 8
                    if flags & 0x000002:  # sample-description-index-present
                        if struct.unpack(">I", buf[q:q + 4])[0] != 1:
                            struct.pack_into(">I", buf, q, 1)

        return bytes(buf)

    def _ism_postproc(self, seg_paths: List[Path], out_path: Path, stream, bar_manager, task_key: str, total: int) -> bool:
        audio_codec = (getattr(stream, "codecs", "") or "").lower()
        codec_private_optional = audio_codec in ("ec-3", "eac3", "ac-3", "ac3")
        if not stream.codec_private_data and not codec_private_optional:
            logger.error("CodecPrivateData mancante per lo stream ISM")
            return False

        kid_hex = stream.drm.kid
        if not kid_hex:
            logger.error("KID mancante nello stream ISM")
            return False

        # 1. Concatenate init + media segments into a single file (in the correct order, skipping invalid segments)
        encrypted_temp = out_path.with_suffix(".enc.mp4")

        try:
            valid_segs = []
            for p in seg_paths:
                try:
                    if p.stat().st_size > 0:
                        valid_segs.append((p, _segment_number(p)))
                except OSError:
                    continue

            valid_segs.sort(key=lambda item: item[1])
            if not valid_segs:
                logger.error("No valid ISM segments found for concatenation")
                return False

            # 2. Generate init (ftyp+moov) using the SAME track_ID the fragments carry (read from the first fragment) so ffmpeg can match tfhd -> trak/trex.
            frag_track_id: Optional[int] = None
            try:
                frag_track_id = self._read_fragment_track_id(valid_segs[0][0].read_bytes())
            except Exception as exc:
                logger.debug(f"ISM: could not read fragment track_id ({exc}) — using default")
            logger.info(f"ISM init track_id={frag_track_id if frag_track_id else 1} (from fragments)")
            init_data = self._build_ism_init(stream, kid_hex, track_id=frag_track_id)

            # 3. Write the init + normalized fragments to the temporary encrypted file
            with open(encrypted_temp, "wb") as out:
                out.write(init_data)
                for seg_path, _ in valid_segs:
                    out.write(self._normalize_ism_fragment_sdi(seg_path.read_bytes()))
            
        except Exception as exc:
            logger.error(f"ISM unified file creation failed: {exc}")
            return False

        logger.info(f"ISM file: {encrypted_temp} ({encrypted_temp.stat().st_size} bytes)")

        # Continue the track's own bar for the decrypt phase (status "@ Merge" -> "@ CTR",
        # bar restarts) instead of spawning a separate "Dec ..." bar.
        def _decrypt_cb(parsed: Optional[Dict[str, Any]]) -> None:
            if not parsed:
                return

            bar_manager.handle_progress_line({
                "task_key": task_key,
                "pct": parsed.get("pct"),
                "speed": parsed.get("status") or "Decrypt",
            })

        decryptor = Decryptor()
        ok = decryptor.decrypt(
            str(encrypted_temp),
            self.key,
            str(out_path),
            stream_type=stream.type,
            progress_cb=_decrypt_cb,
        )

        try:
            encrypted_temp.unlink()
        except Exception:
            pass

        if not (ok and out_path.exists() and out_path.stat().st_size > 0):
            logger.error("ISM post-processing decryption failed")
            return False

        verify_ok, verify_msg = verify_decrypted_media(out_path)
        if not verify_ok:
            logger.error(f"Post-mux verification failed for {out_path.name}: {verify_msg}")
            return False
        logger.info(f"Check post-mux OK for{out_path.name}: {verify_msg}")

        # Finalize at 100%, keeping the decrypt status/segment/size (don't revert to Merge).
        bar_manager.handle_progress_line({"task_key": task_key, "pct": 100})
        return True
