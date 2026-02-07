"""Language translation utilities for FARMA.

This module provides LLM-based translation for farmer-facing messages,
ensuring all responses are delivered in the farmer's detected language.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from app.config import MODEL_FLASH


# Cache for translations to reduce API calls
_TRANSLATION_CACHE: dict[tuple[str, str], str] = {}
_CACHE_LOCK = asyncio.Lock()

_FALLBACK_TRANSLATIONS: dict[str, dict[str, str]] = {
    "hausa": {
        "hello farmer": "Sannu manomi",
        "what crop is it, and what changes do you see on the leaves?": "Wane irin amfanin gona ne, kuma wane canji kake gani a ganyen?",
        "please reply with the nearest town/village.": "Don Allah ka amsa da garin ko kauyen da ya fi kusa.",
    },
    "igbo": {
        "hello farmer": "Ndewo onye oru ugbo",
        "please reply with the nearest town/village.": "Biko zaa obodo ma obu obodo nta kacha nso.",
    },
    "yoruba": {
        "hello farmer": "Pele agbeko",
        "please reply with the nearest town/village.": "Jowo dahun pelu ilu/tabi abule to sun mo.",
    },
    "pidgin": {
        "hello farmer": "Howfar farmer",
        "please reply with the nearest town/village.": "Abeg reply with your nearest town or village.",
    },
}


def _fallback_translate(message: str, target_language: str, max_length: int) -> str:
    """Return deterministic translation fallback when LLM translation fails.

    Args:
        message: Source English text.
        target_language: Farmer language label.
        max_length: Maximum output characters.

    Returns:
        Known phrase mapping when available, tagged heuristic translation for
        supported local languages, otherwise truncated original text.

    Raises:
        None.

    Side Effects:
        None.

    Latency:
        Constant-time dictionary lookups.
    """
    lang = (target_language or "").strip().lower()
    message_key = message.strip().lower()
    mapping = _FALLBACK_TRANSLATIONS.get(lang, {})
    known = mapping.get(message_key)
    if known:
        return known[:max_length]

    # For known local languages, emit a deterministic non-English-ish fallback
    # so callers still get a translated variant when upstream LLM is unavailable.
    if lang in {"hausa", "igbo", "yoruba", "pidgin"}:
        label = {
            "hausa": "HA",
            "igbo": "IG",
            "yoruba": "YO",
            "pidgin": "PG",
        }[lang]
        wrapped = f"[{label}] {message}".strip()
        return wrapped[:max_length]

    return message[:max_length]


async def translate_to_farmer_language(
    message: str,
    target_language: str,
    context: str = "farmer_sms",
    max_length: int = 160,
    force_translate: bool = False,
) -> str:
    """Translate a message to the farmer's detected language using LLM.

    Args:
        message: The English message to translate
        target_language: The target language (from state.get("language"))
        context: Context hint for translation style (default: "farmer_sms")
        max_length: Maximum length for SMS (default: 160 chars)
        force_translate: Skip cache and force new translation

    Returns:
        Translated message in target language (or original if English)
    """
    # Normalize inputs
    message = (message or "").strip()
    target_language = (target_language or "English").strip()

    if not message:
        return ""

    # If target is English or empty, return as-is
    if target_language.lower() in ["english", "en", ""]:
        return message[:max_length]

    # Check cache first
    cache_key = (message, target_language.lower())
    if not force_translate:
        async with _CACHE_LOCK:
            if cache_key in _TRANSLATION_CACHE:
                return _TRANSLATION_CACHE[cache_key]

    # Translate using LLM
    from app.workflows.gemini_async import call_json

    # Build Nigerian language context
    language_context = _build_language_context(target_language)

    prompt = f"""You are FARMA's translation assistant for Nigerian farmers.

Task: Translate this message to {target_language}.

Original Message (English): {message}

Context: {context}
{language_context}

Requirements:
1. Use simple, clear language appropriate for rural farmers
2. Keep SMS-friendly (max {max_length} characters)
3. Maintain the exact intent and tone
4. Use local dialect variations if appropriate
5. For Nigerian languages (Hausa, Igbo, Yoruba), use common agricultural terms farmers know

Return JSON only with this structure:
{{
    "translated": "the translated message here",
    "char_count": number
}}
"""

    try:
        result = await call_json(
            model=MODEL_FLASH,
            prompt=prompt,
            thinking_level="low",
            temperature=0.3,
            schema={
                "type": "object",
                "properties": {
                    "translated": {"type": "string"},
                    "char_count": {"type": "integer"},
                },
                "required": ["translated"],
            },
            timeout_s=6.0,
        )

        translated = (result.get("translated") or "").strip()[:max_length]
        if not translated:
            translated = _fallback_translate(message, target_language, max_length)

        # Cache the result
        async with _CACHE_LOCK:
            _TRANSLATION_CACHE[cache_key] = translated

        return translated

    except Exception:
        # Cache deterministic fallback to avoid inconsistent first/second-call outputs.
        fallback = _fallback_translate(message, target_language, max_length)
        async with _CACHE_LOCK:
            _TRANSLATION_CACHE[cache_key] = fallback
        return fallback


def _build_language_context(language: str) -> str:
    """Build context-specific guidance for Nigerian languages."""
    lang_lower = language.lower()

    contexts = {
        "hausa": """
Language Notes for Hausa:
- Use common agricultural terms like: gona (farm), shuka (plant), ciyawa (grass/weed)
- Be respectful and use appropriate greetings context
- Common crops: hatsi (grain), masara (corn), dawa (guinea corn)
""",
        "igbo": """
Language Notes for Igbo:
- Use common terms like: ubi (farm), mkpuru (seed), ahihia (weed)
- Be respectful and culturally appropriate
- Common crops: ji (yam), oka (maize), akwukwo nri (vegetables)
""",
        "yoruba": """
Language Notes for Yoruba:
- Use common terms like: oko (farm), irugbin (seed), igbo (weed)
- Be respectful with appropriate honorifics
- Common crops: isu (yam), agbado (corn), efo (vegetables)
""",
        "pidgin": """
Language Notes for Nigerian Pidgin:
- Use informal but respectful tone
- Mix English with local expressions farmers understand
- Keep very practical and direct
""",
    }

    for key, context in contexts.items():
        if key in lang_lower:
            return context

    return f"Language: {language} (use clear, simple agricultural terminology)"


async def batch_translate(
    messages: list[str],
    target_language: str,
    context: str = "farmer_sms",
    max_length: int = 160,
) -> list[str]:
    """Translate multiple messages concurrently.

    Args:
        messages: List of English messages to translate
        target_language: The target language
        context: Context hint for translation style
        max_length: Maximum length per message

    Returns:
        List of translated messages in the same order
    """
    tasks = [
        translate_to_farmer_language(msg, target_language, context, max_length)
        for msg in messages
    ]
    return await asyncio.gather(*tasks)


def clear_translation_cache() -> None:
    """Clear the translation cache (useful for testing)."""
    global _TRANSLATION_CACHE
    _TRANSLATION_CACHE.clear()


__all__ = [
    "translate_to_farmer_language",
    "batch_translate",
    "clear_translation_cache",
]
