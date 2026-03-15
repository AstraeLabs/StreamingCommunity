# 18.07.25

import sys

from .checker import check_bento4, check_mp4dump, check_ffmpeg, check_n_m3u8dl_re, check_shaka_packager
from .device_install import check_device_wvd_path, check_device_prd_path


is_binary_installation = getattr(sys, 'frozen', False)
ffmpeg_path, ffprobe_path = check_ffmpeg()
bento4_decrypt_path = check_bento4()
mp4dump_path = check_mp4dump()
wvd_path = check_device_wvd_path()
prd_path = check_device_prd_path()
n_m3u8dl_re_path = check_n_m3u8dl_re()
shaka_packager = check_shaka_packager()


def get_is_binary_installation() -> bool:
    return is_binary_installation

def get_ffmpeg_path() -> str:
    return ffmpeg_path

def get_ffprobe_path() -> str:
    return ffprobe_path

def get_bento4_decrypt_path() -> str:
    return bento4_decrypt_path

def get_mp4dump_path() -> str:
    return mp4dump_path

def get_wvd_path() -> str:
    return wvd_path

def get_prd_path() -> str:
    return prd_path

def get_n_m3u8dl_re_path() -> str:
    return n_m3u8dl_re_path

def get_shaka_packager_path() -> str:
    return shaka_packager

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
    cert_chain  = device.group_certificate
    leaf_cert   = cert_chain.get(0)

    basic = leaf_cert.get_attribute(BCertObjType.BASIC)
    cert_type = BCertCertType(basic.attribute.cert_type).name if basic else "N/A"
    security_level = basic.attribute.security_level if basic else device.security_level
    #client_id   = basic.attribute.client_id.hex() if basic else "N/A"

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