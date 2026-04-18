# 01.04.26

import json
import logging
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from VibraVid.setup import get_velora_path
from VibraVid.core.source.download_utils import (normalize_path_key, format_size, format_speed, estimate_total_size)


logger = logging.getLogger("velora_bridge")
_QUEUE_SENTINEL = object()


def _format_header_keys(headers: Optional[Dict[str, Any]]) -> str:
    """Return a comma-separated, sorted list of non-sensitive header key names."""
    if not headers:
        return ""
    keys = [str(k) for k in headers.keys() if str(k).strip()]
    if not keys:
        return ""

    filtered = [k for k in keys if k.lower() not in {"authorization", "cookie"}]
    display_keys = filtered or keys
    return ",".join(sorted(display_keys))


def _format_bridge_event(event: Dict[str, Any]) -> str:
    """Format a Velora event dict into a human-readable string for logging."""
    event_name = (event.get("event") or "").lower()
    label = event.get("display_label") or event.get("label") or event.get("task_key") or "download"
    url = event.get("url") or ""
    path = event.get("path") or ""
    headers = _format_header_keys(event.get("headers") if isinstance(event.get("headers"), dict) else None)
    elapsed_seconds = event.get("elapsed_seconds")

    if event_name == "start":
        return (f"START {label} | tasks={event.get('task_count', '?')} | concurrency={event.get('concurrency', '?')}")

    if event_name == "summary":
        elapsed_display = (f"{float(elapsed_seconds):.1f}s" if isinstance(elapsed_seconds, (int, float)) else "?")
        return (f"SUMMARY {label} | completed={event.get('completed', '?')}/{event.get('total', '?')} | bytes={format_size(int(event.get('bytes') or 0))} | elapsed={elapsed_display}")

    if event_name == "completed":
        parts = [f"GET {url}" if url else "GET", f"PATH: {path}" if path else None]
        if headers:
            parts.append(f"HEADERS: {headers}")

        parts.extend(
            [
                f"segments={event.get('segments', '?')}",
                f"size={event.get('size', '?')}",
                f"speed={event.get('speed', '?')}",
                f"in={float(elapsed_seconds):.1f}s"
                if isinstance(elapsed_seconds, (int, float))
                else None,
                f"skipped={bool(event.get('skipped', False))}",
            ]
        )
        return f"DONE {label} | " + " | ".join(p for p in parts if p)

    if event_name == "retry":
        parts = [f"GET {url}" if url else "GET", f"PATH: {path}" if path else None]
        if headers:
            parts.append(f"HEADERS: {headers}")
        
        parts.extend(
            [
                f"RETRY={event.get('attempt', '?')}/{event.get('retry_count', '?')}",
                f"ERROR: {event.get('message', event.get('error', ''))}",
                f"in={float(elapsed_seconds):.1f}s"
                if isinstance(elapsed_seconds, (int, float))
                else None,
            ]
        )
        return f"RETRY {label} | " + " | ".join(p for p in parts if p)

    if event_name == "error":
        parts = [f"GET {url}" if url else "GET", f"PATH: {path}" if path else None]
        if headers:
            parts.append(f"HEADERS: {headers}")
        
        parts.extend(
            [
                f"ERROR: {event.get('message', '')}",
                f"RETRY={event.get('attempt', '?')}/{event.get('retry_count', '?')}"
                if event.get("attempt") is not None
                else None,
                f"in={float(elapsed_seconds):.1f}s"
                if isinstance(elapsed_seconds, (int, float))
                else None,
            ]
        )
        return f"ERROR {label} | " + " | ".join(p for p in parts if p)

    if event_name == "cancelled":
        return f"CANCELLED {label} | {event.get('message', 'Cancellation requested')}"

    return f"{event_name.upper() or 'EVENT'} {label} | {event}"


def _normalize_event_task_key(event: Dict[str, Any]) -> Dict[str, Any]:
    if "_task_key" in event and "task_key" not in event:
        event = dict(event)
        event["task_key"] = event.pop("_task_key")
    
    return event

def run_download_plan(plan: Dict[str, Any], progress_cb: Optional[Callable[[int, int, int, float], None]] = None, event_cb: Optional[Callable[[Dict[str, Any]], None]] = None, stop_check: Optional[Callable[[], bool]] = None) -> List[Dict[str, Any]]:
    """
    Launch the Velora binary for *plan* and stream its events back to the caller.

        - plan: A fully-populated Velora download-plan dict (will be serialised to a temporary JSON file on disk).
        - progress_cb: Called with ``(done_count, total, total_bytes, speed_bps)`` after each completed segment.
        - event_cb: Called with the raw (normalised) event dict for every ``completed``, ``retry``, ``error`` and ``cancelled`` event.
        - stop_check: Zero-argument callable; when it returns ``True`` the Velora process is terminated and the function returns immediately.

    Returns: List of ``{"path", "bytes", "task_key", "label", "display_label", "skipped"}`` dicts — one per successfully completed segment.
    """
    binary_path = get_velora_path()
    if not binary_path:
        raise FileNotFoundError("Velora binary not found")

    tasks = plan.get("tasks") or []
    total = len(tasks)
    if total == 0:
        return []

    task_lookup_by_path: Dict[str, Dict[str, Any]] = {
        normalize_path_key(task.get("path", "")): task
        for task in tasks
        if task.get("path")
    }

    plan_path: Optional[str] = None
    process: Optional[subprocess.Popen[str]] = None
    stop_thread: Optional[threading.Thread] = None
    results: List[Dict[str, Any]] = []
    done_count = 0
    total_bytes = 0
    started_at = time.monotonic()

    try:
        # Write plan to a temp file.
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as tmp:
            plan_path = tmp.name
            json.dump(plan, tmp, ensure_ascii=False)

        command = (["dotnet", binary_path, plan_path] if binary_path.lower().endswith(".dll") else [binary_path, plan_path])
        logger.info(f"Launching Velora with command: {command}")

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        # Read stdout in a dedicated thread → queue so the main loop cannot block forever if the process crashes silently.
        line_queue: "queue.Queue[object]" = queue.Queue()

        def _stdout_reader() -> None:
            assert process is not None and process.stdout is not None
            try:
                for raw_line in process.stdout:
                    line_queue.put(raw_line)
            finally:
                line_queue.put(_QUEUE_SENTINEL)

        reader_thread = threading.Thread(target=_stdout_reader, daemon=True)
        reader_thread.start()

        if stop_check:
            def _watch_stop() -> None:
                assert process is not None
                logger.debug("Stop-watcher thread started")
                while process.poll() is None:
                    if stop_check():
                        logger.warning("Stop requested, terminating velora...")
                        try:
                            process.terminate()
                        except Exception as e:
                            logger.error(f"Failed to terminate: {e}")
                        return
                    
                    time.sleep(0.25)
                logger.debug("Stop-watcher thread exiting (process already dead)")

            stop_thread = threading.Thread(target=_watch_stop, daemon=True)
            stop_thread.start()

        while True:
            try:
                item = line_queue.get(timeout=0.5)
            except queue.Empty:
                # Check if process died and queue is drained.
                if process.poll() is not None and line_queue.empty():
                    logger.debug(f"Process exited with code {process.returncode}, queue empty, exiting main loop")
                    break
                continue

            if item is _QUEUE_SENTINEL:
                break

            if stop_check and stop_check():
                try:
                    process.terminate()
                except Exception:
                    pass
                break

            raw_line = item
            line = str(raw_line).strip()
            if not line:
                continue

            try:
                event: Dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                logger.info("Failed to decode JSON from Velora: %s", line)
                continue

            # Enrich event with url/headers from the plan when missing.
            path_key = normalize_path_key(str(event.get("path") or ""))
            if path_key and path_key in task_lookup_by_path:
                task = task_lookup_by_path[path_key]
                event.setdefault("url", task.get("url"))
                task_headers = task.get("headers") if isinstance(task.get("headers"), dict) else {}
                if task_headers:
                    event.setdefault("headers", task_headers)

            event = _normalize_event_task_key(event)
            event_name = (event.get("event") or "").lower()

            if event_name in {"start", "summary"}:
                logger.info(_format_bridge_event(event))
                continue

            if event_name in {"retry", "error", "cancelled"}:
                logger.info(_format_bridge_event(event))
                if event_cb:
                    normalized_event = dict(event)
                    normalized_event.setdefault("task_key", plan.get("task_key", "download"))
                    normalized_event.setdefault("label", plan.get("label", ""))
                    normalized_event.setdefault("display_label", plan.get("display_label", ""))
                    normalized_event.setdefault("segments", "0/1")
                    normalized_event.setdefault("speed", "ERR")
                    event_cb(normalized_event)
                continue

            if event_name == "completed" or "path" in event:
                logger.debug(_format_bridge_event(event))
                done_count += 1
                bytes_written = int(event.get("bytes") or 0)
                total_bytes += bytes_written

                elapsed = max(time.monotonic() - started_at, 0.001)
                speed = total_bytes / elapsed
                if progress_cb:
                    progress_cb(done_count, total, total_bytes, speed)

                progress_event = dict(event)
                progress_event.setdefault("task_key", plan.get("task_key", "download"))
                progress_event.setdefault("label", plan.get("label", ""))
                progress_event.setdefault("display_label", plan.get("display_label", ""))
                progress_event.setdefault("pct", int((done_count / total) * 100) if total else 100)
                progress_event.setdefault("segments", f"{done_count}/{total}")
                estimated_total = estimate_total_size(total_bytes, done_count, total)
                progress_event.setdefault(
                    "size",
                    f"{format_size(total_bytes)}/{format_size(estimated_total)}"
                    if estimated_total
                    else format_size(total_bytes),
                )
                progress_event.setdefault("final_size", format_size(bytes_written))
                progress_event.setdefault("speed", format_speed(speed))

                if event_cb:
                    event_cb(progress_event)

                results.append(
                    {
                        "path": event.get("path"),
                        "bytes": bytes_written,
                        "task_key": progress_event.get("task_key"),
                        "label": progress_event.get("label"),
                        "display_label": progress_event.get("display_label"),
                        "skipped": bool(event.get("skipped", False)),
                    }
                )
                continue

            if event_cb:
                event_cb(event)

        if process is not None:
            return_code = process.wait()
            if return_code not in (0, None):
                logger.warning("Velora exited with code %s", return_code)

        return results

    finally:
        # Clean up temp plan file
        if plan_path:
            try:
                Path(plan_path).unlink(missing_ok=True)
                logger.debug(f"Deleted temp plan: {plan_path}")
            except Exception as e:
                logger.warning(f"Failed to delete temp plan: {e}")

        # Terminate velora process with robust cleanup
        if process:
            try:
                if process.poll() is None:
                    logging.info("Terminating velora process (SIGTERM)...")
                    process.terminate()
                    
                    # Wait up to 5 seconds for graceful termination
                    try:
                        process.wait(timeout=5.0)
                        logger.debug("Velora terminated gracefully")
                    except subprocess.TimeoutExpired:
                        logger.warning("Velora didn't terminate in 5s, using SIGKILL...")
                        process.kill()
                        process.wait(timeout=2.0)
                        logger.warning("✓Velora force-killed")
            except Exception as e:
                logger.error(f"Error terminating velora: {e}")

        # Join reader thread with longer timeout
        if "reader_thread" in dir() and reader_thread and reader_thread.is_alive():
            logger.debug("Waiting for reader thread...")
            reader_thread.join(timeout=5.0)
            if reader_thread.is_alive():
                logger.warning("Reader thread didn't finish in 5s")
        
        # Join stop-watcher thread with longer timeout
        if stop_thread and stop_thread.is_alive():
            logger.debug("Waiting for stop-watcher thread...")
            stop_thread.join(timeout=5.0)
            if stop_thread.is_alive():
                logger.warning("Stop-watcher thread didn't finish in 5s")
            if stop_thread.is_alive():
                logger.warning("Stop-watcher thread did not finish within timeout — may be dangling")