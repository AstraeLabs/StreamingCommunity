# 19.05.25

import base64
import logging


logger = logging.getLogger(__name__)


class DRMInfo:
    WIDEVINE_SYSTEM_ID = "edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"
    PLAYREADY_SYSTEM_ID = "9a04f079-9840-4286-ab92-e65be0885f95"
    FAIRPLAY_SYSTEM_ID = "94ce86fb-07ff-4f43-adb8-93d2fa968ca2"
    
    def __init__(self):
        self.pssh = None
        self.kid = None
        self.key = None
        self.system_id = None
        self.drm_type = None
        self.default_kid = None
        self.method = None
    
    def set_pssh(self, pssh_base64):
        self.pssh = pssh_base64
        try:
            pssh_data = base64.b64decode(pssh_base64)
            if len(pssh_data) >= 32:
                system_id_bytes = pssh_data[12:28]
                system_id = '-'.join([
                    system_id_bytes[0:4].hex(),
                    system_id_bytes[4:6].hex(),
                    system_id_bytes[6:8].hex(),
                    system_id_bytes[8:10].hex(),
                    system_id_bytes[10:16].hex()
                ])
                self.system_id = system_id
                
                if system_id.lower() == self.WIDEVINE_SYSTEM_ID:
                    self.drm_type = 'WV'
                elif system_id.lower() == self.PLAYREADY_SYSTEM_ID:
                    self.drm_type = 'PR'
                elif system_id.lower() == self.FAIRPLAY_SYSTEM_ID:
                    self.drm_type = 'FP'
                else:
                    self.drm_type = 'UNK'
        except Exception as e:
            logger.error(f"Failed to parse PSSH: {e}")
            self.drm_type = 'UNK'
    
    def set_kid(self, kid_hex):
        self.kid = kid_hex.lower().replace('-', '')
    
    def set_key(self, key_hex):
        self.key = key_hex.lower().replace('-', '')
    
    def set_method(self, scheme_id_uri):
        if scheme_id_uri:
            if 'cenc' in scheme_id_uri or 'mp4protection' in scheme_id_uri:
                self.method = 'cenc'
            elif 'cbcs' in scheme_id_uri:
                self.method = 'cbcs'
            else:
                self.method = scheme_id_uri.split(':')[-1] if ':' in scheme_id_uri else scheme_id_uri
    
    def is_encrypted(self):
        return self.pssh is not None or self.kid is not None or self.default_kid is not None
    
    def get_drm_display(self):
        if self.drm_type:
            return self.drm_type
        elif self.default_kid:
            return self.default_kid[:16] + "..."
        else:
            return "-"
    
    def get_key_pair(self):
        if self.kid and self.key:
            return f"{self.kid}:{self.key}"
        elif self.default_kid and self.key:
            return f"{self.default_kid}:{self.key}"
        return None
    
    def __repr__(self):
        if not self.is_encrypted():
            return "DRMInfo(not encrypted)"
        return f"DRMInfo({self.drm_type}, KID={self.kid[:16] if self.kid else 'N/A'}...)"