# 01.04.25

import gzip
import logging
import queue
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

from rich.markup import escape

from VibraVid.core.decryptor import Decryptor
from VibraVid.core.manifest.stream import track_label
from VibraVid.core.muxing.helper.video import binary_merge_segments
from VibraVid.core.ui.bar_manager import DownloadBarManager, console
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

from ..decryptor._segment_crypto import decrypt_aes128
from .util._stream_helpers import collect_failed_segments, describe_key_for_log, detect_seg_ext, merged_segment_ext
from .util._subtitle_segments import merge_vtt_files
from .util._verify import verify_decrypted_media
from .util.formatting import (
    estimate_total_size as _estimate_total_size,
)
from .util.formatting import (
    fmt_dur as _fmt_dur,
)
from .util.formatting import (
    format_size as _fmt_size,
)
from .util.formatting import (
    format_speed as _fmt_speed,
)
from .util.formatting import (
    normalize_path_key,
)

logger = logging.getLogger("manual")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
MAX_TOKEN_REFRESH_ROUNDS = config_manager.config.get_int("DOWNLOAD", "max_token_refresh_rounds")
TOKEN_REFRESH_BACKOFF_SECONDS = config_manager.config.get_float(
    "DOWNLOAD", "token_refresh_backoff_seconds", default=2.0
)
TOKEN_REFRESH_STALL_ROUNDS = max(1, config_manager.config.get_int("DOWNLOAD", "token_refresh_stall_rounds", default=3))
DECRYPT_WORKER_COUNT = max(1, config_manager.config.get_int("DOWNLOAD", "decrypt_worker_count"))
SKIP_POST_DECRYPT = config_manager.config.get_bool("DOWNLOAD", "skip_post_decrypt", default=False)


def _reads_as_self_initializing_mp4(path: Path) -> bool:
    """True if *path* starts with its own `ftyp` box — a complete, independently decryptable MP4 document — rather than a bare `moof`/`mdat` fragment meant to share a separate init segment."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    return len(head) == 8 and head[4:8] == b"ftyp"


class DecryptPipelineMixin:
    @staticmethod
    def _decrypt_track_label(stream) -> str:
        """Short human label for a track, used in decrypt-failure reporting."""
        return track_label(stream)

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in small increments so a stop request lands without waiting out the full backoff."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop_check():
                return
            time.sleep(min(0.5, deadline - time.monotonic()))

    def _verify_track_decrypted(self, out_path: "Path", stream) -> None:
        """Verify a per-track merged+decrypted MP4/M4A carries no residual CENC boxes."""
        try:
            drm = getattr(stream, "drm", None)
            if not (self.key and drm is not None and drm.is_encrypted()):
                return

            if getattr(stream, "type", "") == "subtitle":
                return

            if not out_path.exists() or out_path.stat().st_size <= 0:
                return

            label = self._decrypt_track_label(stream)
            logger.info(f"Verify starting -> {out_path.name}")
            _verify_t0 = time.monotonic()
            ok, message, still_encrypted = verify_decrypted_media(out_path)
            _verify_elapsed = time.monotonic() - _verify_t0
            if ok:
                logger.info(f"Track decrypt verified OK [{label}] {out_path.name}: {message} (verify took {_verify_elapsed:.1f}s)")
                return

            if still_encrypted:
                logger.error(f"Track still ENCRYPTED after decrypt [{label}] {out_path.name}: {message} (verify took {_verify_elapsed:.1f}s)")
                short = message.split(";", 1)[0].strip()
                console.print(escape(f"Decryption FAILED for {label}: {short};"))
                with self._decrypt_failures_lock:
                    self.decrypt_failures.append({"label": label, "track": out_path.name, "message": message})
            else:
                logger.warning(f"Track decrypt verification inconclusive [{label}] {out_path.name}: {message} (verify took {_verify_elapsed:.1f}s)")
        except Exception as exc:
            logger.warning(f"Track decrypt verification skipped for {getattr(stream, 'type', '?')}: {exc}")

    def _download_stream_generic(
        self,
        dl_segs: list[dict],
        stream,
        protocol: str,
        default_ext: str,
        bar_manager: DownloadBarManager,
        live_decryption: bool = False,
        seg_url_refresh_fn=None,
    ) -> None:
        task_key = self._stream_task_key(stream)
        total = len(dl_segs)
        stream_dir = self._make_stream_dir(stream, protocol)
        all_headers = self._build_headers()
        protocol_lower = protocol.lower()

        key_cache: dict[str, bytes] = {}
        segment_meta_by_path = {}
        for seg in dl_segs:
            seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
            if seg_ext == "m4s":
                seg_ext = "mp4"
            seg_path = stream_dir / f"seg_{seg['number']:05d}.{seg_ext}"
            segment_meta_by_path[normalize_path_key(str(seg_path))] = seg

        # Range-split streams (DASH/HLS byte-range single file, ISM byte ranges):
        # every media segment is a byte range of ONE remote file, so only the
        # header chunk (seg_00000, or a dedicated 'init' segment) carries the
        # ftyp+moov(+pssh). Detect it here so we can probe just that chunk.
        _media_dl_segs = [s for s in dl_segs if s.get("seg_type") == "media"]
        _first_media_number = min((s.get("number", 0) for s in _media_dl_segs), default=0)
        is_range_split = (
            len(_media_dl_segs) > 1
            and len({s.get("url") for s in _media_dl_segs}) == 1
            and all("Range" in (s.get("headers") or {}) for s in _media_dl_segs)
        )

        _total_duration: float = sum(s.get("duration", 0.0) for s in dl_segs if s.get("seg_type") != "init")
        _media_segs_only: list[dict] = [s for s in dl_segs if s.get("seg_type") != "init"]
        _seg_dur_cumulative: list[float] = []
        _acc = 0.0
        for _s in _media_segs_only:
            _acc += _s.get("duration", 0.0)
            _seg_dur_cumulative.append(_acc)

        decrypt_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        decrypt_errors: list[str] = []      # per-segment decryption error messages (diagnostics)
        seg_errors: list[str] = []          # per-segment HTTP/transport error messages (diagnostics)
        decrypt_threads: list[threading.Thread] = []
        probe_lock = threading.Lock()

        # ISM fragments are raw moof+mdat with no ftyp/moov of their own — DRM info
        # was already probed on the manifest-synthesized init (_probe_ism_init)
        # before this ran, so skip the (always-empty) per-segment probe here.
        probe_done = protocol_lower == "ism"
        key_cache_lock = threading.Lock()
        dash_init_box: list[Path | None] = [None]
        dash_init_lock = threading.Lock()

        def _probe_once(target_path: Path | None, reason: str) -> None:
            nonlocal probe_done
            if probe_done or not target_path:
                return

            if not target_path.exists() or target_path.stat().st_size <= 0:
                return

            with probe_lock:
                if probe_done:
                    return

                if not target_path.exists() or target_path.stat().st_size <= 0:
                    return
                probe_done = True

            logger.debug(f"{protocol.upper()} probe starting -> {target_path.name} ({reason})")
            threading.Thread(target=self._probe_media_file, args=(target_path,), daemon=True).start()

        def _replace_segment_file(source_path: Path, target_path: Path, reason: str) -> None:
            last_exc: Exception | None = None
            for attempt in range(1, 9):
                try:
                    if target_path.exists():
                        try:
                            target_path.unlink()
                        except Exception:
                            pass

                    source_path.replace(target_path)
                    return
                except OSError as exc:
                    last_exc = exc
                    if attempt >= 8:
                        raise

                    if getattr(exc, "winerror", None) not in (5, 32) and not isinstance(exc, PermissionError):
                        raise

                    logger.debug(f"{reason} replace retry {attempt}/8 for {source_path.name} -> {target_path.name}: {exc}")
                    time.sleep(0.05 * attempt)

            if last_exc:
                raise last_exc

        def _progress(
            done: int, total_: int, total_bytes: int, speed_bps: float, speed_label: str | None = None
        ) -> None:
            pct = int((done / total_) * 100) if total_ else 0
            estimated_total = _estimate_total_size(total_bytes, done, total_) if done > 0 else total_bytes
            size_display = (
                f"{_fmt_size(total_bytes)}/{_fmt_size(estimated_total)}"
                if done < total_
                else f"{_fmt_size(total_bytes)}/{_fmt_size(total_bytes)}"
            )
            duration_display = ""

            if _total_duration > 0:
                media_done = max(0, done - (1 if any(s.get("seg_type") == "init" for s in dl_segs) else 0))
                elapsed_dur = (
                    _seg_dur_cumulative[media_done - 1]
                    if media_done > 0 and media_done <= len(_seg_dur_cumulative)
                    else 0.0
                )
                duration_display = f"{_fmt_dur(elapsed_dur)}/{_fmt_dur(_total_duration)}"

            bar_manager.handle_progress_line(
                {
                    "task_key": task_key,
                    "pct": pct,
                    "segments": f"{done}/{total_}",
                    "size": size_display,
                    "speed": speed_label if speed_label is not None else _fmt_speed(speed_bps),
                    "duration": duration_display,
                }
            )

        def _decrypt_hls_segment(fp: Path, seg: dict[str, Any]) -> None:
            enc = seg.get("enc") or {}
            method = str(enc.get("method") or "NONE").upper()
            if method != "AES-128":
                return

            key_data = enc.get("key_bytes")
            if key_data is None:
                key_url = enc.get("key_url")
                if not key_url:
                    raise RuntimeError(f"Missing AES-128 key URL for {fp.name}")

                key_data = key_cache.get(key_url)
                if key_data is None:
                    with key_cache_lock:
                        key_data = key_cache.get(key_url)
                        if key_data is None:
                            with create_client(
                                headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True
                            ) as c:
                                r = c.get(key_url)
                                r.raise_for_status()
                                key_data = r.content

                            if len(key_data) != 16:
                                logger.warning(f"HLS AES-128 key length is {len(key_data)} bytes for {key_url}")

                            key_cache[key_url] = key_data
                            logger.info(f"HLS AES-128 key fetched: iv={enc.get('iv')} key={key_data.hex()}")

            logger.debug(f"AES-128 LIVE decrypt path={fp} with key={describe_key_for_log(key_data)}")
            decrypted = decrypt_aes128(fp.read_bytes(), key_data, enc.get("iv"), int(seg.get("number", 0) or 0))
            tmp_path = fp.with_suffix(fp.suffix + ".dec")
            tmp_path.write_bytes(decrypted)
            _replace_segment_file(tmp_path, fp, "HLS AES-128")

            logger.debug(f"HLS AES-128 decrypted -> {fp.name}")
            if int(seg.get("number", -1) or -1) == _first_media_number:
                _probe_once(fp, "hls-first-decrypted-segment")

        def _decrypt_dash_segment(
            fp: Path, seg: dict[str, Any], dash_decryptor: Decryptor, init_path: Path | None
        ) -> None:
            if seg.get("seg_type") == "init":
                logger.info(f"DASH init segment ready -> {fp.name}")
                return

            dec_tmp = fp.with_suffix(fp.suffix + ".dec")
            init_path_str = str(init_path) if init_path and init_path.exists() else None
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"CENC LIVE decrypt path={fp} init={init_path_str or 'None'} with key={describe_key_for_log(self.key)}")

            ok, message, _data = dash_decryptor.decrypt_segment_live(
                encrypted_path=str(fp),
                decrypted_path=str(dec_tmp),
                raw_keys=self.key,
                init_path=init_path_str,
            )

            if not ok:
                raise RuntimeError(f"DASH live decrypt failed for {fp.name}: {message}")

            if not dec_tmp.exists():
                raise RuntimeError(f"DASH live decrypt produced no output for {fp.name}")

            _replace_segment_file(dec_tmp, fp, "DASH live")
            logger.debug(f"DASH live decrypted -> {fp.name}")
            if int(seg.get("number", -1) or -1) == _first_media_number:
                _probe_once(fp, "dash-first-decrypted-segment")

        dash_decryptor = Decryptor() if protocol_lower == "dash" and live_decryption and self.key else None
        dash_pending: list[tuple] = []  # (fp, seg) media segments seen before the init segment

        def _decrypt_worker() -> None:
            while True:
                item = decrypt_queue.get()
                if item is None:
                    break
                try:
                    if item.get("skipped"):
                        continue
                    path_value = item.get("path")
                    if not path_value:
                        continue
                    fp = Path(path_value)
                    if not fp.exists() or fp.stat().st_size <= 0:
                        continue
                    seg = segment_meta_by_path.get(normalize_path_key(str(fp)))
                    if not seg:
                        logger.debug(f"Segment completion without metadata match: {fp}")
                        continue

                    if protocol_lower == "hls":
                        _decrypt_hls_segment(fp, seg)
                        continue

                    if protocol_lower == "dash" and live_decryption and self.key:
                        if seg.get("seg_type") == "init":
                            flush: list[tuple] = []
                            cached_now = False
                            with dash_init_lock:
                                if dash_init_box[0] is None:
                                    dash_init_box[0] = fp
                                    cached_now = True
                                    logger.debug(f"DASH init segment cached -> {fp.name}")
                                    flush = dash_pending[:]
                                    dash_pending.clear()

                            if cached_now:
                                _probe_once(fp, "dash-init-segment")
                            for pending_fp, pending_seg in flush:
                                _decrypt_dash_segment(pending_fp, pending_seg, dash_decryptor, dash_init_box[0])
                        else:
                            with dash_init_lock:
                                init_path = dash_init_box[0]
                                if init_path is None:
                                    dash_pending.append((fp, seg))
                            if init_path is not None:
                                _decrypt_dash_segment(fp, seg, dash_decryptor, init_path)

                except Exception as exc:
                    decrypt_errors.append(str(exc))
                    logger.error(f"Segment decrypt error ({protocol_lower}/{task_key}): {exc}")
                    decrypt_queue.task_done()

        needs_hls_decrypt = protocol_lower == "hls" and any(
            str((seg.get("enc") or {}).get("method") or "NONE").upper() == "AES-128" for seg in dl_segs
        )
        _stream_is_encrypted = stream.drm is not None and stream.drm.is_encrypted()
        needs_dash_live = protocol_lower == "dash" and live_decryption and bool(self.key) and _stream_is_encrypted

        if needs_hls_decrypt or needs_dash_live:
            worker_count = DECRYPT_WORKER_COUNT if needs_dash_live else 1
            logger.debug(f"{protocol.upper()} decrypt worker pool started ({worker_count}x, {'AES-128' if needs_hls_decrypt else 'live DASH'})")
            for _ in range(worker_count):
                t = threading.Thread(target=_decrypt_worker, daemon=True)
                t.start()
                decrypt_threads.append(t)

        def _handle_download_event(event: dict[str, Any]) -> None:
            event_name = (event.get("event") or "").lower()
            if event_name == "error":
                msg = event.get("message") or event.get("error")
                if msg:
                    seg_errors.append(str(msg))
                return

            if event_name in {"start", "summary", "retry", "cancelled", "progress"}:
                return

            path_value = event.get("path")
            if not path_value:
                return

            if event.get("skipped"):
                return

            seg = segment_meta_by_path.get(normalize_path_key(str(path_value)))

            if is_range_split:
                # Only the header chunk (init segment, or first media chunk)
                if seg and (
                    seg.get("seg_type") == "init"
                    or (seg.get("seg_type") == "media" and seg.get("number") == _first_media_number)
                ):
                    _probe_once(Path(path_value), f"{protocol.upper()}-range-split-header")

            else:
                should_probe_now = (
                    seg
                    and not decrypt_threads
                    and (
                        seg.get("seg_type") == "init"
                        or (seg.get("seg_type") == "media" and seg.get("number") == _first_media_number)
                    )
                )

                if should_probe_now:
                    _probe_once(Path(path_value), f"{protocol.upper()}-first-media-segment")

            if decrypt_threads:
                decrypt_queue.put(dict(event))

        # Segments whose bytes travel inside the manifest itself (e.g. a base64 init segment) have no URL to request
        inline_paths: list[Path] = []
        net_segs = dl_segs
        if any(seg.get("inline_data") for seg in dl_segs):
            net_segs = []
            for seg in dl_segs:
                data = seg.get("inline_data")
                if not data:
                    net_segs.append(seg)
                    continue

                seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
                if seg_ext == "m4s":
                    seg_ext = "mp4"

                inline_path = stream_dir / f"seg_{seg['number']:05d}.{seg_ext}"
                inline_path.write_bytes(data)
                inline_paths.append(inline_path)
                logger.debug(f"Inline segment written from manifest -> {inline_path.name} ({len(data)} B)")
                if seg.get("seg_type") == "init":
                    _probe_once(inline_path, "inline-init-segment")

            logger.info(f"{protocol.upper()}: {len(inline_paths)} inline segment(s) from manifest, {len(net_segs)} to download")

        paths = list(inline_paths)
        if net_segs:
            paths += self._run_dl(
                net_segs,
                stream_dir,
                all_headers,
                _progress,
                stream=stream,
                event_cb=_handle_download_event,
                default_ext=default_ext,
            )

        # Token-refresh retry: when segments fail (e.g. the CDN manifest token expired mid-download -> HTTP 403, or a transient CDN-side 503 that clears up after a short wait).
        if seg_url_refresh_fn and not self._stop_check():
            seg_by_number = {s["number"]: s for s in dl_segs}
            failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)
            rounds = 0
            stall_rounds = 0

            while failed and rounds < MAX_TOKEN_REFRESH_ROUNDS and not self._stop_check():
                rounds += 1

                if TOKEN_REFRESH_BACKOFF_SECONDS > 0:
                    backoff = min(TOKEN_REFRESH_BACKOFF_SECONDS * rounds, 20.0)
                    logger.info(f"Token refresh round {rounds}: waiting {backoff:.1f}s before retrying (transient CDN errors often clear up on their own)")
                    self._interruptible_sleep(backoff)
                    if self._stop_check():
                        break

                failed_numbers = [n for n, _ in failed]
                fresh_map = seg_url_refresh_fn(failed_numbers)
                retry_segs = [
                    {**seg_by_number[n], "url": fresh_map[n]}
                    for n in failed_numbers
                    if n in fresh_map and n in seg_by_number
                ]
                if not retry_segs:
                    break

                logger.warning(f"Token refresh round {rounds}: retrying {len(retry_segs)} segment(s) with a fresh token")
                retry_paths = self._run_dl(
                    retry_segs,
                    stream_dir,
                    all_headers,
                    _progress,
                    stream=stream,
                    event_cb=_handle_download_event,
                    default_ext=default_ext,
                )
                paths.extend(retry_paths)
                new_failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)

                if len(new_failed) >= len(failed):  # no progress this round -> token still dead / host moved
                    stall_rounds += 1
                    failed = new_failed
                    if stall_rounds >= TOKEN_REFRESH_STALL_ROUNDS:
                        logger.warning(f"Token refresh: no progress after {stall_rounds} consecutive round(s), giving up on {len(failed)} segment(s)")
                        break
                    continue

                stall_rounds = 0
                failed = new_failed

        if decrypt_threads:
            for _ in decrypt_threads:
                decrypt_queue.put(None)
            for t in decrypt_threads:
                t.join()

        if paths is not None:
            _stream_label_rich = (
                self._video_label
                if stream.type == "video"
                else self._audio_labels.get((stream.language or "und").lower(), stream.language or "und")
                if stream.type == "audio"
                else stream.language or "und"
            )

            _plain_label = re.sub(r"\[/?[^\[\]]*\]", "", _stream_label_rich).strip() or task_key
            failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)
            if failed:
                failed_numbers = {n for n, _ in failed}
                aes_failed = sum(
                    1
                    for seg in dl_segs
                    if seg["number"] in failed_numbers
                    and str((seg.get("enc") or {}).get("method") or "NONE").upper() == "AES-128"
                )
                aes_note = (
                    f" ({aes_failed} had an AES-128 key pending — never fetched, segment missing before decrypt)"
                    if aes_failed
                    else ""
                )
                if seg_errors:
                    top = "; ".join(
                        f"{m} (x{n})" for m, n in Counter(e.strip() for e in seg_errors if e.strip()).most_common(3)
                    )
                    logger.warning(f"{_plain_label}: {len(failed)}/{total} segment(s) failed to download — most common error(s): {top}{aes_note}")
                else:
                    logger.warning(f"{_plain_label}: {len(failed)}/{total} segment(s) failed to download{aes_note}")

                with self._failed_segments_lock:
                    self._failed_segments.append((_plain_label, failed))

        if decrypt_errors:
            raise RuntimeError(decrypt_errors[0])

        if self._stop_check() or not paths:
            return

        # Derive the merged-file extension from a *media* segment
        sample_url = next(
            (s["url"] for s in dl_segs if s.get("seg_type") != "init"),
            dl_segs[0]["url"] if dl_segs else "",
        )
        ext = merged_segment_ext(sample_url, default=default_ext)
        out_path = self.output_dir / self._out_filename(stream, ext)

        # ----- ISM POST‑PROCESSING -----
        if protocol_lower == "ism" and self.key:
            success = self._ism_postproc(paths, out_path, stream, bar_manager, task_key, total)
            if not success:
                logger.error("ISM post‑processing failed")
            return

        # Standard merge for HLS/DASH
        is_plain_subtitle = (
            stream is not None
            and getattr(stream, "type", "") == "subtitle"
            and not getattr(stream, "is_wvtt_mp4", False)
        )

        merge_total_size = sum(p.stat().st_size for p in paths if p.exists())
        logger.info(f"Merge starting -> {out_path.name} ({len(paths)} segs, {_fmt_size(merge_total_size)})")
        _merge_t0 = time.monotonic()
        bar_manager.handle_progress_line(
            {
                "task_key": task_key,
                "pct": 100,
                "segments": f"{total}/{total}",
                "size": f"{_fmt_size(merge_total_size)}/{_fmt_size(merge_total_size)}",
                "speed": "Merge",
            }
        )

        def _sniff_vtt_content(raw: bytes) -> bool:
            """Prova a leggere i primi byte come testo; se sono gzip, decomprime prima."""
            try:
                if raw[:2] == b"\x1f\x8b":  # magic number gzip
                    raw = gzip.decompress(raw)[:64]
                else:
                    raw = raw[:64]
                head = raw.decode("utf-8-sig", errors="replace").lstrip("\ufeff\ufffd").lstrip()
                return head.startswith("WEBVTT")
            except Exception:
                return False

        _is_webvtt_sub = False
        _detect_reason = "no paths"

        if is_plain_subtitle and paths:
            _ext_says_vtt = out_path.suffix.lower() == ".vtt"
            _content_says_vtt = False

            for p in paths:
                try:
                    raw = p.read_bytes()[:512]
                    if _sniff_vtt_content(raw):
                        _content_says_vtt = True
                        break
                except Exception as exc:
                    logger.warning(f"[merge_detect] could not sniff {getattr(p, 'name', p)}: {exc}")
                    continue

            _is_webvtt_sub = _ext_says_vtt or _content_says_vtt
            _detect_reason = f"ext={_ext_says_vtt}, content={_content_says_vtt}"

        logger.info(f"[merge_detect] {out_path.name}: is_webvtt_sub={_is_webvtt_sub} ({_detect_reason}), {len(paths)} segment(s)")

        stream_is_encrypted = stream.drm.method is not None
        already_decrypted_per_segment = False

        # Some HLS BYTERANGE packagings (observed on Shaka Packager's
        # SAMPLE-AES-CTR multi-key output) make every media segment a
        # self-initializing MP4 document (its own ftyp+moov, not a bare
        # moof+mdat fragment sharing the EXT-X-MAP init).
        if (
            not is_plain_subtitle
            and not live_decryption
            and self.key
            and stream_is_encrypted
            and not SKIP_POST_DECRYPT
        ):
            existing = [p for p in paths if p.exists() and p.stat().st_size > 0]
            ftyp_count = sum(1 for p in existing if _reads_as_self_initializing_mp4(p))
            if len(existing) > 1 and ftyp_count > 1:
                logger.info(f"{out_path.name}: {ftyp_count}/{len(existing)} segment(s) are self-initializing MP4 documents — decrypting each individually before merge instead of once after")
                decrypted_paths: list[Path] = []
                per_segment_ok = True
                for p in existing:
                    dec_p = p.with_suffix(p.suffix + ".dec")
                    try:
                        if Decryptor().decrypt(str(p), self.key, str(dec_p), stream_type=stream.type):
                            decrypted_paths.append(dec_p)
                        else:
                            per_segment_ok = False
                            logger.warning(f"{out_path.name}: per-segment decrypt failed for {p.name}")
                            break
                    except Exception as exc:
                        per_segment_ok = False
                        logger.warning(f"{out_path.name}: per-segment decrypt error for {p.name}: {exc}")
                        break

                if per_segment_ok:
                    paths = decrypted_paths
                    already_decrypted_per_segment = True
                else:
                    for dec_p in decrypted_paths:
                        dec_p.unlink(missing_ok=True)

        if _is_webvtt_sub:
            merged = merge_vtt_files(paths, merge_logger=logger)
            n_headers = merged.count("WEBVTT")
            if n_headers != 1:
                logger.warning(f"[merge_vtt] {out_path.name}: expected 1 WEBVTT header after merge, found {n_headers}")
            out_path.write_text(merged, encoding="utf-8")
            logger.debug(f"WebVTT cue-merge completed -> {out_path.name}")
        else:
            binary_merge_segments(paths, out_path, merge_logger=logger)
            logger.debug(f"Binary merge completed -> {out_path.name}")
        logger.info(f"Merge finished -> {out_path.name} in {time.monotonic() - _merge_t0:.1f}s")

        if already_decrypted_per_segment:
            for p in paths:
                p.unlink(missing_ok=True)

        # Reset absolute fragment timestamps. Must run AFTER decryption
        def _normalize_out_path() -> None:
            if is_plain_subtitle or out_path.suffix.lower() not in (".mp4", ".m4s", ".m4a"):
                return

            from VibraVid.core.muxing.helper.video import normalize_timestamps

            norm_path = normalize_timestamps(out_path, logger)
            if norm_path is None:
                return

            try:
                out_path.unlink(missing_ok=True)
                norm_path.rename(out_path)
            except OSError as exc:
                logger.error(f"[normalize] rename-back failed, keeping un-normalized file: {exc}")
                norm_path.unlink(missing_ok=True)

        if already_decrypted_per_segment or not ((not live_decryption) and self.key and stream_is_encrypted):
            _normalize_out_path()

        decrypted_ok = already_decrypted_per_segment
        decrypt_already_reported = False
        if already_decrypted_per_segment:
            # Each segment was already decrypted individually before the merge above — the merged file is plaintext already.
            logger.info(f"Decrypt already done per-segment -> {out_path.name}")
        elif SKIP_POST_DECRYPT and stream_is_encrypted:
            logger.info(f"skip_post_decrypt: leaving {out_path.name} encrypted (raw merged track kept for testing)")
        elif (
            (not live_decryption)
            and self.key
            and stream_is_encrypted
            and out_path.exists()
            and out_path.stat().st_size > 0
            and not is_plain_subtitle
        ):
            post_merge_path = out_path.with_suffix(out_path.suffix + ".dec")

            # Continue this track's own progress bar for the decrypt phase: keep the track
            # label, just swap the status (the "@ Merge" text) for the decrypt method/backend
            def _decrypt_cb(parsed: dict[str, Any] | None) -> None:
                if not parsed:
                    return

                # Only the bar position (pct) and the status text change; segment count and
                # size stay as the merge left them — just "@ Merge" -> "@ CTR".
                bar_manager.handle_progress_line(
                    {
                        "task_key": task_key,
                        "pct": parsed.get("pct"),
                        "speed": parsed.get("status") or "Decrypt",
                    }
                )

            _decrypt_t0 = time.monotonic()
            logger.info(f"Decrypt starting -> {out_path.name}")
            try:
                decryptor = Decryptor()
                if decryptor.decrypt(
                    str(out_path), self.key, str(post_merge_path), stream_type=stream.type, progress_cb=_decrypt_cb
                ):
                    decrypted_ok = True
                    logger.info(f"Decrypt finished -> {out_path.name} in {time.monotonic() - _decrypt_t0:.1f}s")
                    try:
                        out_path.unlink(missing_ok=True)
                        post_merge_path.rename(out_path)
                        _normalize_out_path()
                    except Exception as exc:
                        logger.error(f"rename failed: {exc}")
                        if post_merge_path.exists():
                            try:
                                post_merge_path.unlink()
                            except Exception:
                                pass
                else:
                    decrypt_already_reported = True
                    kid_hint = ", ".join(stream.drm.get_all_kids()) if stream.drm else ""
                    track_label = f"{stream.type} {stream.resolution or stream.language or ''}".strip()
                    logger.warning(f"Decrypt failed -> {out_path.name} after {time.monotonic() - _decrypt_t0:.1f}s (kid={kid_hint or 'unknown'})")
                    bar_manager.handle_progress_line({"task_key": task_key, "speed": "Failed"})
                    with self._decrypt_failures_lock:
                        self.decrypt_failures.append(
                            {
                                "label": track_label,
                                "track": out_path.name,
                                "message": f"required KID(s): {kid_hint or 'unknown'}",
                            }
                        )
                    if post_merge_path.exists():
                        try:
                            post_merge_path.unlink()
                        except Exception:
                            pass

            except Exception as exc:
                logger.error(f"Decrypt error -> {out_path.name} after {time.monotonic() - _decrypt_t0:.1f}s: {exc}")

        if out_path.exists() and out_path.stat().st_size > 0:
            logger.debug(f"{protocol.upper()} merged {len(paths):>4} segs -> {out_path.name} ({out_path.stat().st_size // 1024} KB)")
            if not decrypt_already_reported:
                self._verify_track_decrypted(out_path, stream)

            if decrypted_ok:
                # Finalize the bar at 100%, keeping segment/size/status as-is.
                bar_manager.handle_progress_line({"task_key": task_key, "pct": 100})
            elif decrypt_already_reported:
                _progress(total, total, out_path.stat().st_size, 0.0, speed_label="Failed")
            else:
                _progress(total, total, out_path.stat().st_size, 0.0, speed_label="Merge")
        else:
            logger.error(f"{protocol.upper()} binary merge produced empty file: {out_path}")
