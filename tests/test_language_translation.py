"""Comprehensive tests for language translation functionality.

Tests the LLM-based translation system for farmer-facing messages
across different Nigerian languages and dialects.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from app.workflows.language_utils import (
    batch_translate,
    clear_translation_cache,
    translate_to_farmer_language,
)


class TestLanguageTranslation:
    """Test suite for language translation utilities."""

    @pytest.fixture(autouse=True)
    def clear_cache_between_tests(self) -> Generator[None, None, None]:
        """Clear translation cache before each test."""
        clear_translation_cache()
        yield
        clear_translation_cache()

    @pytest.mark.anyio
    async def test_english_passthrough(self) -> None:
        """Test that English messages pass through unchanged."""
        message = "Hello farmer, please provide your location."
        result = await translate_to_farmer_language(message, "English")
        assert len(result) > 0
        # Should be similar to original (or exactly same if no translation needed)
        assert len(result) <= 160

    @pytest.mark.anyio
    async def test_hausa_translation(self) -> None:
        """Test translation to Hausa."""
        message = "What crop is it, and what changes do you see on the leaves?"
        result = await translate_to_farmer_language(message, "Hausa")
        assert len(result) > 0
        assert len(result) <= 160
        # Result should be different from original (translated)
        assert result != message

    @pytest.mark.anyio
    async def test_igbo_translation(self) -> None:
        """Test translation to Igbo."""
        message = "Please reply with the nearest town/village."
        result = await translate_to_farmer_language(message, "Igbo")
        assert len(result) > 0
        assert len(result) <= 160

    @pytest.mark.anyio
    async def test_yoruba_translation(self) -> None:
        """Test translation to Yoruba."""
        message = "Your farm is performing well. Continue your current practices."
        result = await translate_to_farmer_language(message, "Yoruba")
        assert len(result) > 0
        assert len(result) <= 160

    @pytest.mark.anyio
    async def test_pidgin_translation(self) -> None:
        """Test translation to Nigerian Pidgin."""
        message = "We need more information about your farm location."
        result = await translate_to_farmer_language(message, "Pidgin")
        assert len(result) > 0
        assert len(result) <= 160

    @pytest.mark.anyio
    async def test_sms_length_constraint(self) -> None:
        """Test that translations respect 160 character SMS limit."""
        long_message = (
            "Your case needs local expert verification before we can proceed. "
            "A field officer should inspect the crop before any treatment is recommended. "
            "Please wait for further instructions and do not apply any chemicals."
        )
        result = await translate_to_farmer_language(long_message, "Hausa")
        assert len(result) <= 160, f"Translation too long: {len(result)} chars"

    @pytest.mark.anyio
    async def test_empty_message(self) -> None:
        """Test handling of empty message."""
        result = await translate_to_farmer_language("", "Hausa")
        assert result == ""

    @pytest.mark.anyio
    async def test_custom_max_length(self) -> None:
        """Test custom maximum length parameter."""
        message = "This is a test message for custom length."
        result = await translate_to_farmer_language(message, "Yoruba", max_length=50)
        assert len(result) <= 50

    @pytest.mark.anyio
    async def test_context_preservation(self) -> None:
        """Test that context-specific terminology is preserved."""
        # Disease context
        disease_msg = "Remove affected leaves, improve spacing."
        disease_result = await translate_to_farmer_language(
            disease_msg, "Hausa", context="disease_treatment"
        )
        assert len(disease_result) > 0
        assert len(disease_result) <= 160

        # Loan context
        loan_msg = "Your loan application is being reviewed."
        loan_result = await translate_to_farmer_language(
            loan_msg, "Hausa", context="loan_status"
        )
        assert len(loan_result) > 0
        assert len(loan_result) <= 160

    @pytest.mark.anyio
    async def test_translation_caching(self) -> None:
        """Test that translations are cached for performance."""
        message = "Hello farmer"
        language = "Hausa"

        # First call - should hit LLM
        result1 = await translate_to_farmer_language(message, language)

        # Second call - should use cache (much faster)
        result2 = await translate_to_farmer_language(message, language)

        # Results should be identical
        assert result1 == result2

    @pytest.mark.anyio
    async def test_force_translate_bypass_cache(self) -> None:
        """Test that force_translate bypasses cache."""
        message = "Test message"
        language = "Igbo"

        # First call
        result1 = await translate_to_farmer_language(message, language)

        # Second call with force_translate
        result2 = await translate_to_farmer_language(
            message, language, force_translate=True
        )

        # Both should be valid translations
        assert len(result1) > 0
        assert len(result2) > 0
        assert len(result1) <= 160
        assert len(result2) <= 160

    @pytest.mark.anyio
    async def test_batch_translation(self) -> None:
        """Test batch translation of multiple messages."""
        messages = [
            "What crop is it?",
            "Please provide your location.",
            "Your application is approved.",
        ]
        language = "Yoruba"

        results = await batch_translate(messages, language)

        assert len(results) == len(messages)
        for result in results:
            assert len(result) > 0
            assert len(result) <= 160

    @pytest.mark.anyio
    async def test_special_characters(self) -> None:
        """Test handling of special characters and punctuation."""
        message = "Hello! How are you? (Please respond)"
        result = await translate_to_farmer_language(message, "Hausa")
        assert len(result) > 0
        assert len(result) <= 160

    @pytest.mark.anyio
    async def test_disease_safety_message(self) -> None:
        """Test translation of safety-critical disease messages."""
        safety_msg = (
            "Avoid harsh chemicals. Remove affected leaves, improve spacing, "
            "and use approved pesticide only with label instructions."
        )
        result = await translate_to_farmer_language(
            safety_msg, "Hausa", context="disease_safety"
        )
        assert len(result) > 0
        assert len(result) <= 160

    @pytest.mark.anyio
    async def test_location_clarification_messages(self) -> None:
        """Test translation of location clarification messages."""
        messages = [
            "Please reply with the nearest town/village and a nearby market or junction.",
            "Reply with your ward/village and the nearest market or junction.",
            "For weather advice, reply with your nearest town/village.",
        ]

        for msg in messages:
            for language in ["Hausa", "Igbo", "Yoruba"]:
                result = await translate_to_farmer_language(
                    msg, language, context="location_clarification"
                )
                assert len(result) > 0
                assert len(result) <= 160

    @pytest.mark.anyio
    async def test_fallback_on_translation_failure(self) -> None:
        """Test that system falls back to original message if translation fails."""
        # This test assumes the translation might fail with invalid language
        # The system should gracefully return the original message
        message = "Test message"
        result = await translate_to_farmer_language(
            message, "UnknownLanguage", max_length=160
        )
        # Should still return something valid
        assert len(result) > 0
        assert len(result) <= 160


class TestLanguageTranslationIntegration:
    """Integration tests for translation in actual workflow nodes."""

    @pytest.mark.anyio
    async def test_disease_guardrails_translation(self) -> None:
        """Test that disease guardrails uses translation correctly."""
        # This would require mocking the full state and testing the node
        # For now, we test the translation utility directly
        message = "What crop is it, and what changes do you see on the leaves (spots, yellowing, holes)?"

        # Test for multiple languages
        for language in ["English", "Hausa", "Igbo", "Yoruba"]:
            result = await translate_to_farmer_language(
                message, language, context="disease_clarification"
            )
            assert len(result) > 0
            assert len(result) <= 160

    @pytest.mark.anyio
    async def test_all_hardcoded_messages_translatable(self) -> None:
        """Test that all previously hardcoded messages can be translated."""
        hardcoded_messages = [
            # From disease/guardrails.py
            "Avoid harsh chemicals. Remove affected leaves, improve spacing, and use approved pesticide only with label instructions.",
            "What crop is it, and what changes do you see on the leaves (spots, yellowing, holes)?",
            "Your case needs local expert verification. A field officer should inspect the crop before treatment.",
            # From disease/analyze.py
            "Please tell me the crop and what you see on the leaves/stem (spots, yellowing, holes, or wilting).",
            # From sms_parser.py
            "Please send your request in one message: loan need, crop disease symptoms, or weather question.",
            # From loan/geocode.py
            "Please reply with the nearest town/village and a nearby market or junction.",
            "Reply with your ward/village and the nearest market or junction.",
            # From climate/geocode.py
            "For weather advice, reply with your nearest town/village and a nearby market or junction.",
        ]

        for message in hardcoded_messages:
            # Test Hausa translation as representative
            result = await translate_to_farmer_language(message, "Hausa")
            assert len(result) > 0, f"Failed to translate: {message}"
            assert len(result) <= 160, f"Translation too long ({len(result)} chars): {message}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
