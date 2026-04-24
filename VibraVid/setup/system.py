# 18.07.25

import sys
import threading

from .checker import check_bento4, check_mp4dump, check_ffmpeg, check_shaka_packager, check_dovi_tool, check_mkvmerge, check_velora
from .device_install import check_device_wvd_path, check_device_prd_path


is_binary_installation = getattr(sys, 'frozen', False)
_ffmpeg_path = None
_ffprobe_path = None
_bento4_decrypt_path = None
_mp4dump_path = None
_wvd_path = None
_prd_path = None
_velora_path = None
_shaka_packager_path = None
_dovi_tool_path = None
_mkvmerge_path = None
_initialized = False
_init_lock = threading.Lock()


def _initialize_paths():
    """
    Resolve and cache all binary paths exactly once.

    Uses double-checked locking so that:
    - After the first initialization, every getter returns instantly with zero locking overhead (outer ``if _initialized`` check).
    - During the first initialization, only one thread runs the checks
    """
    global _ffmpeg_path, _ffprobe_path, _bento4_decrypt_path, _mp4dump_path
    global _wvd_path, _prd_path, _velora_path, _shaka_packager_path
    global _dovi_tool_path, _mkvmerge_path
    global _initialized

    # Fast path: already initialized, return immediately.
    if _initialized:
        return

    with _init_lock:
        # A concurrent thread may have finished initialization while we were waiting for the lock.
        if _initialized:
            return

        _ffmpeg_path, _ffprobe_path = check_ffmpeg()
        _bento4_decrypt_path = check_bento4()
        _mp4dump_path = check_mp4dump()
        _wvd_path = check_device_wvd_path()
        _prd_path = check_device_prd_path()
        _velora_path = check_velora()
        _shaka_packager_path = check_shaka_packager()
        _dovi_tool_path = check_dovi_tool()
        _mkvmerge_path = check_mkvmerge()
        _initialized = True


def get_is_binary_installation() -> bool:
    return is_binary_installation

def get_ffmpeg_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _ffmpeg_path

def get_ffprobe_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _ffprobe_path

def get_bento4_decrypt_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _bento4_decrypt_path

def get_mp4dump_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _mp4dump_path

def get_wvd_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _wvd_path

def get_prd_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _prd_path

def get_velora_path() -> str:
    global _velora_path
    if not _initialized:
        _initialize_paths()
    if _velora_path is None:
        _velora_path = check_velora()
    return _velora_path

def get_shaka_packager_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _shaka_packager_path

def get_dovi_tool_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _dovi_tool_path

def get_mkvmerge_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _mkvmerge_path

def get_info_wvd(cdm_device_path):
    if cdm_device_path is None:
        return None

    from pywidevine.device import Device
    device = Device.load(cdm_device_path)

    info = {ci.name: ci.value for ci in device.client_id.client_info}
    model = info.get("model_name", "N/A")
    device_name = info.get("device_name", "").lower()
    build_info = info.get("build_info", "").lower()

    is_emulator = (
        any(x in device_name for x in ["generic", "sdk", "emulator", "x86"])
        or any(x in build_info for x in ["test-keys", "userdebug"])
    )

    if "tv" in model.lower():
        dev_type = "TV"
    elif is_emulator:
        dev_type = "Emulator"
    else:
        dev_type = "Phone"

    return (
        f"[red]Load [cyan]{dev_type} [red]{cdm_device_path}[cyan] | "
        f"[cyan]Security: [red]L{device.security_level} [cyan]| "
        f"[cyan]Model: [red]{model} [cyan]| "
        f"[cyan]SysID: [red]{device.system_id}"
    )


def get_info_prd(cdm_device_path):
    if cdm_device_path is None:
        return None

    from pyplayready.device import Device
    from pyplayready.system.bcert import BCertObjType, BCertCertType

    device = Device.load(cdm_device_path)
    cert_chain = device.group_certificate
    leaf_cert = cert_chain.get(0)

    basic = leaf_cert.get_attribute(BCertObjType.BASIC)
    cert_type = BCertCertType(basic.attribute.cert_type).name if basic else "N/A"
    security_level = basic.attribute.security_level if basic else device.security_level

    def un_pad(b: bytes) -> str:
        return b.rstrip(b'\x00').decode("utf-8", errors="ignore")

    manufacturer = model = model_number = "N/A"
    mfr = leaf_cert.get_attribute(BCertObjType.MANUFACTURER)
    if mfr:
        manufacturer = un_pad(mfr.attribute.manufacturer_name)
        model = un_pad(mfr.attribute.model_name)
        model_number = un_pad(mfr.attribute.model_number)

    return (
        f"[red]Load [cyan]{cert_type} [red]{cdm_device_path}[cyan] | "
        f"[cyan]Security: [red]SL{security_level} [cyan]| "
        f"[cyan]Model: [red]{manufacturer} {model} {model_number} [cyan]"
    )