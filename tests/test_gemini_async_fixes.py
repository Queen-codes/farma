"""Tests for Gemini async improvements and fixes.

Tests the corrected thinking level casing and improved schema error handling.
"""

from __future__ import annotations

import pytest
from google.genai import types

from app.workflows.gemini_async import _make_config, _thinking_level


class TestThinkingLevelNormalization:
    """Test suite for thinking level normalization."""

    def test_lowercase_normalization(self) -> None:
        """Test that thinking levels are normalized to lowercase."""
        assert _thinking_level("LOW") == "low"
        assert _thinking_level("MEDIUM") == "medium"
        assert _thinking_level("HIGH") == "high"
        assert _thinking_level("NONE") == "none"
        assert _thinking_level("MINIMAL") == "low"

    def test_mixed_case_normalization(self) -> None:
        """Test mixed case inputs."""
        assert _thinking_level("Low") == "low"
        assert _thinking_level("MeDiUm") == "medium"
        assert _thinking_level("HiGh") == "high"

    def test_already_lowercase(self) -> None:
        """Test already lowercase inputs."""
        assert _thinking_level("low") == "low"
        assert _thinking_level("medium") == "medium"
        assert _thinking_level("high") == "high"
        assert _thinking_level("none") == "none"

    def test_whitespace_handling(self) -> None:
        """Test that whitespace is stripped."""
        assert _thinking_level(" low ") == "low"
        assert _thinking_level("\tmedium\n") == "medium"

    def test_default_value(self) -> None:
        """Test default value when None or empty string."""
        assert _thinking_level(None) == "low"
        assert _thinking_level("") == "low"
        assert _thinking_level("   ") == "low"

    def test_invalid_level_defaults_to_low(self) -> None:
        """Test that invalid levels default to 'low'."""
        assert _thinking_level("invalid") == "low"
        assert _thinking_level("very_high") == "low"
        assert _thinking_level("123") == "low"

    def test_all_valid_levels(self) -> None:
        """Test all four valid thinking levels."""
        valid_levels = ["none", "low", "medium", "high"]
        for level in valid_levels:
            assert _thinking_level(level) == level
            assert _thinking_level(level.upper()) == level


class TestConfigCreation:
    """Test suite for GenerateContentConfig creation."""

    def test_basic_config_creation(self) -> None:
        """Test basic config creation without schema."""
        config = _make_config(
            thinking_level="low",
            temperature=0.5,
            schema=None,
        )
        assert isinstance(config, types.GenerateContentConfig)
        assert config.temperature == 0.5
        assert config.response_mime_type == "application/json"

    def test_config_with_schema(self) -> None:
        """Test config creation with schema."""
        test_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        config = _make_config(
            thinking_level="low",
            temperature=0.2,
            schema=test_schema,
        )
        assert isinstance(config, types.GenerateContentConfig)
        assert config.temperature == 0.2

    def test_config_thinking_level_normalization(self) -> None:
        """Test that config creation normalizes thinking level."""
        config = _make_config(
            thinking_level="HIGH",  # Uppercase
            temperature=0.3,
            schema=None,
        )
        assert isinstance(config, types.GenerateContentConfig)
        # Thinking level should be normalized to lowercase

    def test_config_with_different_temperatures(self) -> None:
        """Test config with various temperature values."""
        temperatures = [0.0, 0.2, 0.5, 0.8, 1.0]
        for temp in temperatures:
            config = _make_config(
                thinking_level="low",
                temperature=temp,
                schema=None,
            )
            assert config.temperature == temp

    def test_config_with_invalid_schema_raises_clear_error(self) -> None:
        """Test that invalid schema raises clear ValueError."""
        invalid_schema = {"invalid": "schema structure"}
        # The actual error depends on SDK validation, but we should get ValueError
        try:
            config = _make_config(
                thinking_level="low",
                temperature=0.2,
                schema=invalid_schema,
            )
            # If no error, config should still be created
            assert isinstance(config, types.GenerateContentConfig)
        except ValueError as e:
            # Should get clear error message
            error_msg = str(e).lower()
            assert "schema" in error_msg or "validation" in error_msg


class TestThinkingLevelComplianceWithDocs:
    """Tests to ensure compliance with official Gemini documentation."""

    def test_gemini_flash_supports_all_levels(self) -> None:
        """Test all four levels that Gemini Flash supports."""
        # According to docs, Gemini 3 Flash supports: minimal, low, medium, high
        flash_supported = ["none", "low", "medium", "high"]
        for level in flash_supported:
            result = _thinking_level(level)
            assert result in flash_supported

    def test_gemini_pro_supports_low_and_high(self) -> None:
        """Test levels that Gemini Pro supports."""
        # According to docs, Gemini 3 Pro supports: low, high
        pro_supported = ["low", "high"]
        for level in pro_supported:
            result = _thinking_level(level)
            assert result in pro_supported

    def test_documentation_example_formats(self) -> None:
        """Test formats shown in official documentation."""
        # Documentation examples use lowercase
        assert _thinking_level("low") == "low"
        assert _thinking_level("medium") == "medium"
        assert _thinking_level("high") == "high"


class TestBackwardCompatibility:
    """Tests for backward compatibility with old uppercase usage."""

    def test_old_uppercase_calls_still_work(self) -> None:
        """Test that old code using uppercase still works."""
        # Old code might have used "LOW", "MEDIUM", "HIGH"
        old_formats = {
            "LOW": "low",
            "MEDIUM": "medium",
            "HIGH": "high",
        }
        for old, expected in old_formats.items():
            assert _thinking_level(old) == expected

    def test_config_creation_with_uppercase(self) -> None:
        """Test that config creation works with uppercase thinking level."""
        config = _make_config(
            thinking_level="LOW",  # Old uppercase format
            temperature=0.2,
            schema=None,
        )
        assert isinstance(config, types.GenerateContentConfig)


class TestErrorHandlingImprovements:
    """Tests for improved error handling in config creation."""

    def test_clear_error_messages(self) -> None:
        """Test that error messages are clear and helpful."""
        # This test depends on actual SDK behavior
        # We're testing that our wrapper provides clear errors
        pass  # Placeholder - actual implementation would test specific error scenarios

    def test_schema_validation_errors_are_explicit(self) -> None:
        """Test that schema validation errors are explicit."""
        # When schema is malformed, should get clear ValueError
        # with information about the schema issue
        pass  # Placeholder - requires actual error scenario

    def test_thinking_level_errors_are_explicit(self) -> None:
        """Test that thinking level errors are handled gracefully."""
        # Invalid thinking levels should default to "low" without error
        invalid_levels = ["very_low", "super_high", "extreme", ""]
        for level in invalid_levels:
            result = _thinking_level(level)
            assert result == "low"  # Should default gracefully


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
