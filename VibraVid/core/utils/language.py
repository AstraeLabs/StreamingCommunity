# 28.02.26

LANGUAGE_MAP = {

    # --- ISO 639-2/T (terminological, 3 char) ---
    "ita": "it-IT",
    "eng": "en-US",
    "jpn": "ja-JP",
    "deu": "de-DE",
    "fra": "fr-FR",
    "spa": "es-419",
    "por": "pt-BR",
    "rus": "ru-RU",
    "ara": "ar-SA",
    "zho": "zh-CN",
    "kor": "ko-KR",
    "hin": "hi-IN",
    "tur": "tr-TR",
    "pol": "pl-PL",
    "nld": "nl-NL",
    "swe": "sv-SE",
    "fin": "fi-FI",
    "nor": "nb-NO",
    "dan": "da-DK",
    "cat": "ca-ES",
    "ron": "ro-RO",
    "ces": "cs-CZ",
    "hun": "hu-HU",
    "ell": "el-GR",
    "heb": "he-IL",
    "tha": "th-TH",
    "vie": "vi-VN",
    "ind": "id-ID",
    "msa": "ms-MY",
    "ukr": "uk-UA",
    "slk": "sk-SK",
    "hrv": "hr-HR",
    "srp": "sr-RS",
    "bul": "bg-BG",
    "slv": "sl-SI",

    # --- ISO 639-2/B (bibliographic, 3 char) ---
    "ger": "de-DE",
    "fre": "fr-FR",
    "dut": "nl-NL",
    "rum": "ro-RO",
    "cze": "cs-CZ",
    "gre": "el-GR",
    "chi": "zh-CN",
    "may": "ms-MY",
    "slo": "sk-SK",
    "scr": "hr-HR",
    "alb": "sq-AL",

    # --- ISO 639-1 (2 char) ---
    "it": "it-IT",
    "en": "en-US",
    "ja": "ja-JP",
    "de": "de-DE",
    "fr": "fr-FR",
    # "es": "es-419",
    # "pt": "pt-BR",
    "ru": "ru-RU",
    "ar": "ar-SA",
    "zh": "zh-CN",
    "ko": "ko-KR",
    "hi": "hi-IN",
    "tr": "tr-TR",
    "pl": "pl-PL",
    "nl": "nl-NL",
    "sv": "sv-SE",
    "fi": "fi-FI",
    "nb": "nb-NO",
    "no": "nb-NO",
    "da": "da-DK",
    "ca": "ca-ES",
    "ro": "ro-RO",
    "cs": "cs-CZ",
    "hu": "hu-HU",
    "el": "el-GR",
    "he": "he-IL",
    "th": "th-TH",
    "vi": "vi-VN",
    "id": "id-ID",
    "ms": "ms-MY",
    "uk": "uk-UA",
    "sk": "sk-SK",
    "hr": "hr-HR",
    "sr": "sr-RS",
    "bg": "bg-BG",
    "sl": "sl-SI",
    "sq": "sq-AL",

    # --- English full-name keys (lowercase) ---
    "italian": "it-IT",
    "english": "en-US",
    "japanese": "ja-JP",
    "german": "de-DE",
    "french": "fr-FR",
    "spanish": "es-419",
    "portuguese": "pt-BR",
    "russian": "ru-RU",
    "arabic": "ar-SA",
    "chinese": "zh-CN",
    "mandarin": "zh-CN",
    "korean": "ko-KR",
    "hindi": "hi-IN",
    "turkish": "tr-TR",
    "polish": "pl-PL",
    "dutch": "nl-NL",
    "swedish": "sv-SE",
    "finnish": "fi-FI",
    "norwegian": "nb-NO",
    "danish": "da-DK",
    "catalan": "ca-ES",
    "romanian": "ro-RO",
    "czech": "cs-CZ",
    "hungarian": "hu-HU",
    "greek": "el-GR",
    "hebrew": "he-IL",
    "thai": "th-TH",
    "vietnamese": "vi-VN",
    "indonesian": "id-ID",
    "malay": "ms-MY",
    "ukrainian": "uk-UA",
    "slovak": "sk-SK",
    "croatian": "hr-HR",
    "serbian": "sr-RS",
    "bulgarian": "bg-BG",
    "slovenian": "sl-SI",
    "albanian": "sq-AL",

    # --- Common region/country shortcuts ---
    "us": "en-US",
    "gb": "en-GB",
    "au": "en-AU",
    "br": "pt-BR",
    "pt": "pt-PT",
    "jp": "ja-JP",
    "cn": "zh-CN",
    "tw": "zh-TW",
    "kr": "ko-KR",
    "mx": "es-MX",
    "es": "es-ES",

    # --- BCP 47 variants (pass-through normalization) ---
    "pt-br": "pt-BR",
    "pt-pt": "pt-PT",
    "zh-cn": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hans": "zh-CN",
    "zh-hant": "zh-TW",
    "en-gb": "en-GB",
    "en-au": "en-AU",
    "es-mx": "es-MX",
    "es-es": "es-ES",
    "es-us": "es-US",
}


_ISO639_2_FROM_2 = {
    "af": "afr",  # Afrikaans
    "sq": "sqi",  # Albanian
    "am": "amh",  # Amharic
    "ar": "ara",  # Arabic
    "hy": "hye",  # Armenian
    "az": "aze",  # Azerbaijani
    "eu": "eus",  # Basque
    "be": "bel",  # Belarusian
    "bn": "ben",  # Bengali
    "bs": "bos",  # Bosnian
    "bg": "bul",  # Bulgarian
    "ca": "cat",  # Catalan
    "ceb": "ceb", # Cebuano
    "ny": "nya",  # Chichewa
    "zh": "zho",  # Chinese
    "co": "cos",  # Corsican
    "hr": "hrv",  # Croatian
    "cs": "ces",  # Czech
    "da": "dan",  # Danish
    "nl": "nld",  # Dutch
    "en": "eng",  # English
    "eo": "epo",  # Esperanto
    "et": "est",  # Estonian
    "tl": "fil",  # Filipino
    "fi": "fin",  # Finnish
    "fr": "fra",  # French
    "fy": "fry",  # Frisian
    "gl": "glg",  # Galician
    "ka": "kat",  # Georgian
    "de": "deu",  # German
    "el": "ell",  # Greek
    "gu": "guj",  # Gujarati
    "ht": "hat",  # Haitian Creole
    "ha": "hau",  # Hausa
    "haw": "haw", # Hawaiian
    "he": "heb",  # Hebrew
    "hi": "hin",  # Hindi
    "hmn": "hmn", # Hmong
    "hu": "hun",  # Hungarian
    "is": "isl",  # Icelandic
    "ig": "ibo",  # Igbo
    "id": "ind",  # Indonesian
    "ga": "gle",  # Irish
    "it": "ita",  # Italian
    "ja": "jpn",  # Japanese
    "jv": "jav",  # Javanese
    "kn": "kan",  # Kannada
    "kk": "kaz",  # Kazakh
    "km": "khm",  # Khmer
    "rw": "kin",  # Kinyarwanda
    "ko": "kor",  # Korean
    "ku": "kur",  # Kurdish
    "ky": "kir",  # Kyrgyz
    "lo": "lao",  # Lao
    "la": "lat",  # Latin
    "lv": "lav",  # Latvian
    "lt": "lit",  # Lithuanian
    "lb": "ltz",  # Luxembourgish
    "mk": "mkd",  # Macedonian
    "mg": "mlg",  # Malagasy
    "ms": "msa",  # Malay
    "ml": "mal",  # Malayalam
    "mt": "mlt",  # Maltese
    "mi": "mri",  # Maori
    "mr": "mar",  # Marathi
    "mn": "mon",  # Mongolian
    "my": "mya",  # Myanmar (Burmese)
    "ne": "nep",  # Nepali
    "no": "nor",  # Norwegian
    "or": "ori",  # Odia (Oriya)
    "ps": "pus",  # Pashto
    "fa": "fas",  # Persian
    "pl": "pol",  # Polish
    "pt": "por",  # Portuguese
    "pa": "pan",  # Punjabi
    "ro": "ron",  # Romanian
    "ru": "rus",  # Russian
    "sm": "smo",  # Samoan
    "gd": "gla",  # Scots Gaelic
    "sr": "srp",  # Serbian
    "st": "sot",  # Sesotho
    "sn": "sna",  # Shona
    "sd": "snd",  # Sindhi
    "si": "sin",  # Sinhala
    "sk": "slk",  # Slovak
    "sl": "slv",  # Slovenian
    "so": "som",  # Somali
    "es": "spa",  # Spanish
    "su": "sun",  # Sundanese
    "sw": "swa",  # Swahili
    "sv": "swe",  # Swedish
    "tg": "tgk",  # Tajik
    "ta": "tam",  # Tamil
    "tt": "tat",  # Tatar
    "te": "tel",  # Telugu
    "th": "tha",  # Thai
    "tr": "tur",  # Turkish
    "tk": "tuk",  # Turkmen
    "uk": "ukr",  # Ukrainian
    "ur": "urd",  # Urdu
    "ug": "uig",  # Uyghur
    "uz": "uzb",  # Uzbek
    "vi": "vie",  # Vietnamese
    "cy": "cym",  # Welsh
    "xh": "xho",  # Xhosa
    "yi": "yid",  # Yiddish
    "yo": "yor",  # Yoruba
    "zu": "zul",  # Zulu
}

_ISO639_2_FROM_NAME = {
    # Afrikaans
    "afrikaans": "afr",
    # Albanian
    "albanian": "sqi",
    "shqip": "sqi",
    # Amharic
    "amharic": "amh",
    # Arabic
    "arabic": "ara",
    "arabo": "ara",
    # Armenian
    "armenian": "hye",
    # Azerbaijani
    "azerbaijani": "aze",
    # Basque
    "basque": "eus",
    "euskera": "eus",
    # Belarusian
    "belarusian": "bel",
    # Bengali
    "bengali": "ben",
    # Bosnian
    "bosnian": "bos",
    # Bulgarian
    "bulgarian": "bul",
    "bulgaro": "bul",
    # Catalan
    "catalan": "cat",
    "catala": "cat",
    # Chinese
    "chinese": "zho",
    "cinese": "zho",
    # Croatian
    "croatian": "hrv",
    # Czech
    "czech": "ces",
    "ceco": "ces",
    # Danish
    "danish": "dan",
    "danese": "dan",
    # Dutch
    "dutch": "nld",
    "olandese": "nld",
    # English
    "english": "eng",
    "inglese": "eng",
    # Esperanto
    "esperanto": "epo",
    # Estonian
    "estonian": "est",
    # Filipino
    "filipino": "fil",
    "tagalog": "fil",
    # Finnish
    "finnish": "fin",
    "finlandese": "fin",
    # French
    "french": "fra",
    "francese": "fra",
    "francais": "fra",
    # Galician
    "galician": "glg",
    "gallego": "glg",
    # Georgian
    "georgian": "kat",
    # German
    "german": "deu",
    "tedesco": "deu",
    "deutsch": "deu",
    # Greek
    "greek": "ell",
    "greco": "ell",
    # Gujarati
    "gujarati": "guj",
    # Haitian Creole
    "haitian creole": "hat",
    # Hausa
    "hausa": "hau",
    # Hebrew
    "hebrew": "heb",
    "ebraico": "heb",
    # Hindi
    "hindi": "hin",
    # Hungarian
    "hungarian": "hun",
    "ungherese": "hun",
    # Icelandic
    "icelandic": "isl",
    # Igbo
    "igbo": "ibo",
    # Indonesian
    "indonesian": "ind",
    "indonesiano": "ind",
    # Irish
    "irish": "gle",
    # Italian
    "italian": "ita",
    "italiano": "ita",
    # Japanese
    "japanese": "jpn",
    "giapponese": "jpn",
    # Javanese
    "javanese": "jav",
    # Kannada
    "kannada": "kan",
    # Kazakh
    "kazakh": "kaz",
    # Khmer
    "khmer": "khm",
    # Korean
    "korean": "kor",
    "coreano": "kor",
    # Kurdish
    "kurdish": "kur",
    # Kyrgyz
    "kyrgyz": "kir",
    # Lao
    "lao": "lao",
    # Latin
    "latin": "lat",
    "latino": "lat",
    # Latvian
    "latvian": "lav",
    # Lithuanian
    "lithuanian": "lit",
    # Macedonian
    "macedonian": "mkd",
    # Malay
    "malay": "msa",
    "malese": "msa",
    # Malayalam
    "malayalam": "mal",
    # Maltese
    "maltese": "mlt",
    # Maori
    "maori": "mri",
    # Marathi
    "marathi": "mar",
    # Mongolian
    "mongolian": "mon",
    # Myanmar
    "myanmar": "mya",
    "burmese": "mya",
    # Nepali
    "nepali": "nep",
    # Norwegian
    "norwegian": "nor",
    "norvegese": "nor",
    # Pashto
    "pashto": "pus",
    # Persian
    "persian": "fas",
    "farsi": "fas",
    # Polish
    "polish": "pol",
    "polacco": "pol",
    # Portuguese
    "portuguese": "por",
    "portoghese": "por",
    # Punjabi
    "punjabi": "pan",
    # Romanian
    "romanian": "ron",
    "rumeno": "ron",
    # Russian
    "russian": "rus",
    "russo": "rus",
    # Samoan
    "samoan": "smo",
    # Serbian
    "serbian": "srp",
    "serbo": "srp",
    # Sinhala
    "sinhala": "sin",
    "sinhalese": "sin",
    # Slovak
    "slovak": "slk",
    "slovacco": "slk",
    # Slovenian
    "slovenian": "slv",
    "sloveno": "slv",
    # Somali
    "somali": "som",
    # Spanish
    "spanish": "spa",
    "spagnolo": "spa",
    "espanol": "spa",
    # Swahili
    "swahili": "swa",
    # Swedish
    "swedish": "swe",
    "svedese": "swe",
    # Tajik
    "tajik": "tgk",
    # Tamil
    "tamil": "tam",
    # Telugu
    "telugu": "tel",
    # Thai
    "thai": "tha",
    "tailandese": "tha",
    # Turkish
    "turkish": "tur",
    "turco": "tur",
    # Ukrainian
    "ukrainian": "ukr",
    "ucraino": "ukr",
    # Urdu
    "urdu": "urd",
    # Uzbek
    "uzbek": "uzb",
    # Vietnamese
    "vietnamese": "vie",
    "vietnamita": "vie",
    # Welsh
    "welsh": "cym",
    # Xhosa
    "xhosa": "xho",
    # Yiddish
    "yiddish": "yid",
    # Yoruba
    "yoruba": "yor",
    # Zulu
    "zulu": "zul",
}





def resolve_locale(lang: str) -> str:
    """Convert a language code or name to a BCP 47 locale string (e.g. "it-IT")."""
    if not lang or not isinstance(lang, str):
        return ""

    lang = lang.strip()
    if not lang:
        return ""

    if "-" in lang:
        parts = lang.split("-", 1)
        normalised = f"{parts[0].lower()}-{parts[1].upper()}"
        return LANGUAGE_MAP.get(normalised, normalised if len(parts[1]) == 2 else lang)

    return LANGUAGE_MAP.get(lang.lower(), "")


def resolve_iso639_2(lang: str) -> str:
    """Convert a language code or name to an ISO 639-2 code (e.g. "ita")."""
    raw = (lang or "").strip().lower()
    if not raw:
        return "und"

    if len(raw) == 3 and raw.isalpha():
        return raw

    if len(raw) == 2 and raw.isalpha():
        return _ISO639_2_FROM_2.get(raw, "und")

    token = raw.split("-", 1)[0]
    token = token.split("_", 1)[0]
    if len(token) == 3 and token.isalpha():
        return token
    if len(token) == 2 and token.isalpha():
        return _ISO639_2_FROM_2.get(token, "und")

    token = "".join(ch for ch in token if ch.isalpha())
    return _ISO639_2_FROM_NAME.get(token, "und")