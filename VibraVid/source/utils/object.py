# 04.01.25


class KeysManager:
    def __init__(self, keys=None):
        self._keys = []
        if keys:
            self.add_keys(keys)
    
    def add_keys(self, keys):
        if isinstance(keys, str):
            for k in keys.split('|'):
                if ':' in k:
                    kid, key = k.split(':', 1)
                    self._keys.append((kid.strip(), key.strip()))

        elif isinstance(keys, list):
            for k in keys:
                if isinstance(k, str):
                    if ':' in k:
                        kid, key = k.split(':', 1)
                        self._keys.append((kid.strip(), key.strip()))

                elif isinstance(k, dict):
                    kid = k.get('kid', '')
                    key = k.get('key', '')
                    if kid and key:
                        self._keys.append((kid.strip(), key.strip()))
    
    def get_keys_list(self):
        return [f"{kid}:{key}" for kid, key in self._keys]
    
    def get_keys_dict(self):
        return {kid: key for kid, key in self._keys}
    
    def find_key_by_kid(self, kid):
        kid = kid.lower().replace('-', '')
        for k, v in self._keys:
            if k.lower().replace('-', '') == kid:
                return f"{k}:{v}"
        return None
    
    def __len__(self):
        return len(self._keys)
    
    def __iter__(self):
        return iter(self._keys)
    
    def __getitem__(self, index):
        return self._keys[index]
    
    def __bool__(self):
        return len(self._keys) > 0