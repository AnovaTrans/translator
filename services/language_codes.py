"""
Language code normalization for memoQ and SDL Trados Studio compatibility.

memoQ uses ISO 639-2/B 3-letter codes: eng, tur, ger, fre, bul, spa, etc.
SDL Trados uses BCP-47: en-US, tr-TR, de-DE, fr-FR, bg-BG, etc.
TMX files may contain EITHER format in xml:lang attributes.

This module provides a single normalize_lang_code() function that converts
ANY language code variant to a canonical 2-letter ISO 639-1 code.
"""

# ISO 639-2/B (3-letter) -> ISO 639-1 (2-letter) mapping
# Covers ALL languages supported by memoQ (393 variants)
# Source: https://docs.memoq.com/current/en/Concepts/concepts-supported-languages.html
ISO_639_2B_TO_1 = {
    "afr": "af",   # Afrikaans
    "aka": "ak",   # Akan
    "alb": "sq",   # Albanian
    "amh": "am",   # Amharic
    "ara": "ar",   # Arabic
    "arg": "an",   # Aragonese
    "hye": "hy",   # Armenian
    "asm": "as",   # Assamese
    "ast": "ast",  # Asturian (no 2-letter code, keep 3)
    "aze": "az",   # Azeri (Latin)
    "azf": "az",   # Azeri (Cyrillic) -- same base
    "baq": "eu",   # Basque
    "bel": "be",   # Belarussian
    "ben": "bn",   # Bengali
    "bis": "bi",   # Bislama
    "bos": "bs",   # Bosnian (Latin)
    "boc": "bs",   # Bosnian (Cyrillic)
    "bre": "br",   # Breton
    "bul": "bg",   # Bulgarian
    "mya": "my",   # Burmese
    "cat": "ca",   # Catalan
    "ceb": "ceb",  # Cebuano (no 2-letter)
    "chr": "chr",  # Cherokee (no 2-letter)
    "zho": "zh",   # Chinese
    "hrv": "hr",   # Croatian
    "cze": "cs",   # Czech
    "dan": "da",   # Danish
    "prs": "prs",  # Dari
    "dut": "nl",   # Dutch
    "eng": "en",   # English
    "epo": "eo",   # Esperanto
    "est": "et",   # Estonian
    "fas": "fa",   # Farsi/Persian
    "fij": "fj",   # Fijian
    "fil": "fil",  # Filipino
    "fin": "fi",   # Finnish
    "fre": "fr",   # French
    "fry": "fy",   # Frisian
    "ful": "ff",   # Fulah
    "gla": "gd",   # Gaelic (Scotland)
    "glg": "gl",   # Galician
    "kat": "ka",   # Georgian
    "ger": "de",   # German
    "gre": "el",   # Greek
    "kal": "kl",   # Greenlandic
    "grn": "gn",   # Guarani
    "guj": "gu",   # Gujarati
    "hat": "ht",   # Haitian Creole
    "hau": "ha",   # Hausa
    "haw": "haw",  # Hawaiian (no 2-letter)
    "heb": "he",   # Hebrew
    "hin": "hi",   # Hindi
    "hun": "hu",   # Hungarian
    "ice": "is",   # Icelandic
    "ibo": "ig",   # Igbo
    "ind": "id",   # Indonesian
    "gle": "ga",   # Irish
    "ita": "it",   # Italian
    "jpn": "ja",   # Japanese
    "jav": "jv",   # Javanese
    "kan": "kn",   # Kannada
    "kas": "ks",   # Kashmiri
    "kaz": "kk",   # Kazakh
    "khm": "km",   # Khmer
    "kor": "ko",   # Korean
    "kur": "ku",   # Kurdish (Latin)
    "ckb": "ku",   # Kurdish (Arabic) -- same base
    "kir": "ky",   # Kyrgyz
    "lao": "lo",   # Lao
    "lat": "la",   # Latin
    "lav": "lv",   # Latvian
    "lin": "ln",   # Lingala
    "lit": "lt",   # Lithuanian
    "ltz": "lb",   # Luxembourgish
    "mac": "mk",   # Macedonian
    "mlg": "mg",   # Malagasy
    "msa": "ms",   # Malay
    "mal": "ml",   # Malayalam
    "mlt": "mt",   # Maltese
    "mri": "mi",   # Maori
    "mar": "mr",   # Marathi
    "khk": "mn",   # Mongolian
    "mol": "mo",   # Moldavian
    "nep": "ne",   # Nepali
    "nor": "no",   # Norwegian
    "nnb": "nb",   # Norwegian Bokmal
    "nno": "nn",   # Norwegian Nynorsk
    "oci": "oc",   # Occitan
    "ori": "or",   # Oriya
    "orm": "om",   # Oromo
    "pbu": "ps",   # Pashto
    "pol": "pl",   # Polish
    "por": "pt",   # Portuguese
    "pan": "pa",   # Punjabi
    "rum": "ro",   # Romanian
    "rus": "ru",   # Russian
    "kin": "rw",   # Rwanda
    "smo": "sm",   # Samoan
    "san": "sa",   # Sanskrit
    "scc": "sr",   # Serbian (Cyrillic)
    "scr": "sh",   # Serbian (Latin)
    "sot": "st",   # Sesotho
    "sin": "si",   # Sinhala
    "slo": "sk",   # Slovak
    "slv": "sl",   # Slovenian
    "som": "so",   # Somali
    "spa": "es",   # Spanish
    "sun": "su",   # Sundanese
    "swa": "sw",   # Swahili
    "swe": "sv",   # Swedish
    "tgl": "tl",   # Tagalog
    "tgk": "tg",   # Tajiki
    "tam": "ta",   # Tamil
    "tat": "tt",   # Tatar
    "tel": "te",   # Telugu
    "tha": "th",   # Thai
    "tir": "ti",   # Tigrigna
    "ton": "to",   # Tongan
    "tsn": "tn",   # Tswana
    "tur": "tr",   # Turkish
    "tuk": "tk",   # Turkmen
    "ukr": "uk",   # Ukrainian
    "urd": "ur",   # Urdu
    "uzb": "uz",   # Uzbek (Latin)
    "uzn": "uz",   # Uzbek (Cyrillic)
    "vie": "vi",   # Vietnamese
    "wel": "cy",   # Welsh
    "wol": "wo",   # Wolof
    "xho": "xh",   # Xhosa
    "yid": "yi",   # Yiddish
    "yor": "yo",   # Yoruba
    "zul": "zu",   # Zulu
    # Additional codes that are same in both systems
    "ocs": "oc",   # Aranese -> Occitan
    "vls": "vls",  # Flemish (no 2-letter)
}

# Reverse mapping: ISO 639-1 (2-letter) -> ISO 639-2/B (3-letter)
# Built automatically from the forward mapping
ISO_639_1_TO_2B = {}
for _code3, _code2 in ISO_639_2B_TO_1.items():
    if _code2 not in ISO_639_1_TO_2B:  # Keep first mapping (primary)
        ISO_639_1_TO_2B[_code2] = _code3

# Comprehensive language names mapping (2-letter code -> English name)
# Used for SUPPORTED_LANGUAGES in config.py and TB column detection
LANGUAGE_NAMES = {
    "af": "Afrikaans",
    "ak": "Akan",
    "sq": "Albanian",
    "am": "Amharic",
    "ar": "Arabic",
    "hy": "Armenian",
    "as": "Assamese",
    "az": "Azerbaijani",
    "eu": "Basque",
    "be": "Belarusian",
    "bn": "Bengali",
    "bs": "Bosnian",
    "br": "Breton",
    "bg": "Bulgarian",
    "my": "Burmese",
    "ca": "Catalan",
    "zh": "Chinese",
    "hr": "Croatian",
    "cs": "Czech",
    "da": "Danish",
    "nl": "Dutch",
    "en": "English",
    "eo": "Esperanto",
    "et": "Estonian",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "gl": "Galician",
    "ka": "Georgian",
    "de": "German",
    "el": "Greek",
    "gu": "Gujarati",
    "ht": "Haitian Creole",
    "ha": "Hausa",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "is": "Icelandic",
    "ig": "Igbo",
    "id": "Indonesian",
    "ga": "Irish",
    "it": "Italian",
    "ja": "Japanese",
    "jv": "Javanese",
    "kn": "Kannada",
    "kk": "Kazakh",
    "km": "Khmer",
    "ko": "Korean",
    "ku": "Kurdish",
    "ky": "Kyrgyz",
    "lo": "Lao",
    "la": "Latin",
    "lv": "Latvian",
    "lt": "Lithuanian",
    "lb": "Luxembourgish",
    "mk": "Macedonian",
    "ms": "Malay",
    "ml": "Malayalam",
    "mt": "Maltese",
    "mi": "Maori",
    "mr": "Marathi",
    "mn": "Mongolian",
    "ne": "Nepali",
    "no": "Norwegian",
    "nb": "Norwegian Bokmal",
    "nn": "Norwegian Nynorsk",
    "or": "Oriya",
    "ps": "Pashto",
    "pl": "Polish",
    "pt": "Portuguese",
    "pa": "Punjabi",
    "ro": "Romanian",
    "ru": "Russian",
    "sr": "Serbian",
    "sh": "Serbian Latin",
    "sk": "Slovak",
    "sl": "Slovenian",
    "so": "Somali",
    "es": "Spanish",
    "sw": "Swahili",
    "sv": "Swedish",
    "tl": "Tagalog",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tr": "Turkish",
    "tk": "Turkmen",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "cy": "Welsh",
    "xh": "Xhosa",
    "yo": "Yoruba",
    "zu": "Zulu",
}


def normalize_lang_code(code: str) -> str:
    """
    Normalize ANY language code to its canonical 2-letter ISO 639-1 form.

    Handles all formats:
    - memoQ 3-letter:       "eng" -> "en", "tur" -> "tr", "ger" -> "de"
    - memoQ 3-letter+region:"eng-US" -> "en", "tur" -> "tr", "ger-DE" -> "de"
    - SDL BCP-47:           "en-US" -> "en", "tr-TR" -> "tr", "de-DE" -> "de"
    - SDL extended:         "en-US-POSIX" -> "en", "zh-Hans-HK" -> "zh"
    - SDL custom:           "en-x-bi-SDL" -> "en", "hi-x-bh-SDL" -> "hi"
    - Plain 2-letter:       "en" -> "en", "tr" -> "tr"
    - Already normalized:   "en" -> "en" (no-op)

    Args:
        code: Language code string in any format

    Returns:
        Normalized 2-letter code (lowercase), or the base part if no mapping exists
    """
    if not code:
        return ""

    code = code.strip().lower()

    # Split on hyphen to get base code
    parts = code.split('-')
    base = parts[0]

    # If base is 3 letters, try ISO 639-2/B -> ISO 639-1 mapping
    if len(base) == 3 and base in ISO_639_2B_TO_1:
        return ISO_639_2B_TO_1[base]

    # If base is 2 letters, it's already ISO 639-1
    if len(base) == 2:
        return base

    # For codes with no mapping (rare languages), return as-is
    return base


def get_language_name(code: str) -> str:
    """
    Get the English language name for a language code.
    Accepts any format (2-letter, 3-letter, BCP-47).

    Args:
        code: Language code in any format

    Returns:
        English language name, or the code itself if unknown
    """
    normalized = normalize_lang_code(code)
    return LANGUAGE_NAMES.get(normalized, normalized.upper())


def codes_match(code1: str, code2: str) -> bool:
    """
    Check if two language codes refer to the same language,
    regardless of format differences.

    Examples:
        codes_match("eng", "en") -> True
        codes_match("eng-US", "en-us") -> True
        codes_match("tur", "tr-TR") -> True
        codes_match("ger-DE", "de") -> True
        codes_match("en", "tr") -> False
    """
    return normalize_lang_code(code1) == normalize_lang_code(code2)
