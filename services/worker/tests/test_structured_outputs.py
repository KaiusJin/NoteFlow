import unittest

from noteflow_worker.notes.providers import (
    ALLOWED_SECTION_TYPES,
    openai_notes_response_schema,
    validate_notes_response,
)
from noteflow_worker.vision.providers import (
    VISION_KEYS,
    openai_vision_response_schema,
    validate_vision_response,
    vision_response_schema,
)


class NotesStructuredOutputTest(unittest.TestCase):
    def test_accepts_complete_notes_response(self):
        validate_notes_response(
            {
                "sections": [
                    {
                        "heading": "Taylor's Inequality",
                        "sectionType": "THEOREM",
                        "markdown": "## Taylor's Inequality\n\nContent",
                        "confidence": 0.9,
                        "warnings": [],
                    }
                ]
            }
        )

    def test_rejects_unknown_fields_and_invalid_types(self):
        with self.assertRaises(ValueError):
            validate_notes_response(
                {
                    "sections": [
                        {
                            "heading": "Title",
                            "sectionType": "THEOREM",
                            "markdown": "## Title",
                            "confidence": 2,
                            "warnings": [],
                            "invented": True,
                        }
                    ]
                }
            )

    def test_openai_schema_is_strict_and_enumerated(self):
        schema = openai_notes_response_schema()
        section = schema["properties"]["sections"]["items"]

        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(section["additionalProperties"])
        self.assertEqual(
            set(section["properties"]["sectionType"]["enum"]),
            ALLOWED_SECTION_TYPES,
        )


class VisionStructuredOutputTest(unittest.TestCase):
    def test_accepts_complete_visual_analysis(self):
        validate_vision_response(
            {
                "transcription": "x = 1",
                "description": "A handwritten formula.",
                "latex": "x=1",
                "code": "",
                "uncertainty": "",
                "search_text": "handwritten x equals one",
            }
        )

    def test_rejects_empty_or_missing_visual_content(self):
        with self.assertRaises(ValueError):
            validate_vision_response({key: "" for key in VISION_KEYS})
        with self.assertRaises(ValueError):
            validate_vision_response({"transcription": "text"})

    def test_provider_schemas_require_every_field(self):
        gemini_schema = vision_response_schema()
        openai_schema = openai_vision_response_schema()

        self.assertEqual(set(gemini_schema["required"]), VISION_KEYS)
        self.assertEqual(set(openai_schema["required"]), VISION_KEYS)
        self.assertFalse(openai_schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
