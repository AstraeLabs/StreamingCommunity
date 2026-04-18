# 01.04.26

from typing import Optional

try:
    from Cryptodome.Cipher import AES as _AES
    from Cryptodome.Util.Padding import unpad as _unpad
    _HAS_AES = True
except ImportError:
    _HAS_AES = False


def decrypt_aes128(data: bytes, key_data: bytes, iv_hex: Optional[str], seg_num: int) -> bytes:
    """
    Decrypt one AES-128-CBC HLS segment in-process.

    *iv_hex* should be a 32-character lowercase hex string (the IV from the
    ``#EXT-X-KEY`` tag).  When ``None`` the segment sequence number is used
    as the IV per the HLS specification (RFC 8216 §5.2).

    Raises ``RuntimeError`` when PyCryptodome is not installed.
    """
    if not _HAS_AES:
        raise RuntimeError("PyCryptodome required for AES-128 decryption.\nInstall:  pip install pycryptodome")
    
    iv_bytes = bytes.fromhex(iv_hex) if iv_hex else seg_num.to_bytes(16, "big")
    cipher = _AES.new(key_data, _AES.MODE_CBC, iv_bytes)
    try:
        return _unpad(cipher.decrypt(data), _AES.block_size)
    except Exception:
        # Return raw decrypted bytes if un-padding fails (e.g. no padding in stream)
        return cipher.decrypt(data)