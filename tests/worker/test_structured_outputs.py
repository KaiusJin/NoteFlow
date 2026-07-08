import unittest

from noteflow_worker.notes.providers import (
    ALLOWED_SECTION_TYPES,
    openai_notes_response_schema,
    parse_json_object,
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
                "content_kind": "handwriting",
                "importance": "high",
                "reading_order": "top to bottom",
                "language": "en",
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


class ModelJsonRepairTest(unittest.TestCase):
    def test_latex_dense_json_with_raw_newlines_is_repaired(self):
        payload = (
            '{\n "questions":[{"stem":"Coin $p=0.3$","answerKey":"'
            + r"\begin{enumerate}\item \textbf{Step} \frac{1}{2} \rho"
            + '\nSecond line"}]}'
        )
        parsed = parse_json_object(payload)
        answer_key = parsed["questions"][0]["answerKey"]
        for command in (r"\begin{enumerate}", r"\item", r"\textbf", r"\frac", r"\rho"):
            self.assertIn(command, answer_key)
        self.assertIn("\n", answer_key)

    def test_valid_unicode_escape_is_preserved(self):
        parsed = parse_json_object(r'{"s":"café and \\ and \"quote\""}')
        self.assertEqual(parsed["s"], 'café and \\ and "quote"')

    def test_strict_json_is_unchanged(self):
        self.assertEqual(parse_json_object('{"a": 1, "b": [2, 3]}'), {"a": 1, "b": [2, 3]})


if __name__ == "__main__":
    unittest.main()
