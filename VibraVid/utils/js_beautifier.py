# 10.01.26

import re
import json


def extract_setup(source):
    """Estrae e parsifica il JSON dell'oggetto jwplayer().setup({...})"""
    m = re.search(r'\.setup\((\{.*?\})\);', source, re.DOTALL)
    if not m:
        return None
    raw = m.group(1)

    def js_to_json(s):
        s = s.replace("\\'", "'")
        result = []
        i = 0
        while i < len(s):
            if s[i] == '"':
                j = i + 1
                while j < len(s) and s[j] != '"':
                    if s[j] == '\\':
                        j += 1
                    j += 1
                result.append(s[i:j+1])
                i = j + 1

            elif s[i] == "'":
                j = i + 1
                inner = []
                while j < len(s) and s[j] != "'":
                    if s[j] == '\\':
                        j += 1
                    inner.append(s[j])
                    j += 1
                result.append('"' + ''.join(inner) + '"')
                i = j + 1
            else:
                result.append(s[i])
                i += 1

        s = ''.join(result)
        s = re.sub(r'(?<=[{,\[])\s*([a-zA-Z_]\w*)\s*:', r'"\1":', s)
        s = re.sub(r',\s*([}\]])', r'\1', s)
        return s

    return json.loads(js_to_json(raw))

def unpack(source):
    match = re.search(
        r"eval\(function\(\w+,\w+,\w+,\w+(?:,\w+,\w+)?\)\{.*?\}\('(.*?)',(\d+),(\d+),'(.*?)'\.split\('\|'\)\)\)",
        source, re.DOTALL
    )
    if not match:
        return None

    p = match.group(1)
    a = int(match.group(2))
    k = match.group(4).split('|')

    def replace(m):
        word = m.group(0)
        try:
            index = int(word, a)
        except ValueError:
            return word
        return k[index] if index < len(k) and k[index] else word

    return re.sub(r'\b\w+\b', replace, p)