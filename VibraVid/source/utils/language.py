# 28.02.26

LANGUAGE_MAP = {

    # --- ISO 639-2/T (3 char) ---
    "ita": "it-IT",
    "eng": "en-US",
    "jpn": "ja-JP",
    "ger": "de-DE",
    "fre": "fr-FR",
    "spa": "es-419",
    "por": "pt-BR",
    "rus": "ru-RU",
    "ara": "ar-SA",
    "chi": "zh-CN",
    "kor": "ko-KR",
    "hin": "hi-IN",
    "tur": "tr-TR",
    "pol": "pl-PL",
    "dut": "nl-NL",
    "swe": "sv-SE",
    "fin": "fi-FI",
    "nor": "nb-NO",
    "dan": "da-DK",
    "cat": "ca-ES",
    "rum": "ro-RO",
    "cze": "cs-CZ",
    "hun": "hu-HU",
    "gre": "el-GR",
    "heb": "he-IL",
    "tha": "th-TH",
    "vie": "vi-VN",
    "ind": "id-ID",
    "may": "ms-MY",
    "ukr": "uk-UA",

    # --- ISO 639-1 (2 char) ---
    "it": "it-IT",
    "en": "en-US",
    "ja": "ja-JP",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-419",
    "pt": "pt-BR",
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

    # --- lowercase ---
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

    # --- extra ---
    "us": "en-US",
    "br": "pt-BR",
    "jp": "ja-JP",
    "cn": "zh-CN",
    "kr": "ko-KR",
}


def resolve_locale(lang: str) -> str:
    """Convert a language code or name to a locale string (e.g. "it-IT")."""
    if not lang or not isinstance(lang, str):
        return None

    lang = lang.strip()
    if "-" in lang:
        return lang

    return LANGUAGE_MAP.get(lang.lower(), None)