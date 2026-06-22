# PDF to Markdown Quality Grading Report

This report evaluates parsed markdown outputs on heading structure, formulas, tables, code blocks, cleanliness, page metadata, and handwriting quality.

## Overall Summary Table

| Sample | Tool | Composite | Heading | Math | Table | Code | Cleanliness | Metadata | Handwriting | Warnings |
|---|---|---|---|---|---|---|---|---|---|---|
| CS116 | noteflow | **0.82** | 1.00 | 1.00 | 0.50 | 1.00 | 0.60 | 0.70 | 1.00 | expected_tables_missing, excessive_page_footer_furniture_noise, noteflow_missing_coordinate_metadata |
| MATH138L22 | noteflow | **0.74** | 1.00 | 0.40 | 0.50 | 1.00 | 0.90 | 0.70 | 1.00 | math_keywords_missing_in_math_slide, table_missing_separator_row, duplicate_text_paragraphs_detected_count_2, noteflow_missing_coordinate_metadata |
| handwritten_note | noteflow | **0.79** | 1.00 | 1.00 | 0.50 | 1.00 | 0.60 | 0.70 | 0.80 | table_missing_separator_row, excessive_page_footer_furniture_noise, noteflow_missing_coordinate_metadata |

## Evaluation Criteria Definitions

- **Heading:** Correct hierarchy (no empty levels or sequence violations). Lowered by structural confusion.
- **Math:** LaTeX conversion precision. Lowered by `formula-not-decoded` placeholders or mismatched `$$`/`$` delimiters.
- **Table:** Structured table parsing using Markdown pipes (`|`). Separator rows and aligned column structures are checked.
- **Code:** Retaining fenced code blocks (```` ``` ````) with code language identifier. Lowered by flattened layout or missing code blocks.
- **Cleanliness:** Excludes footer noise, duplicate slide headers, or page templates.
- **Metadata:** Extent of retaining page markers (`<!-- page: X -->`) and coordinates/references.
- **Handwriting:** Readability, faithfulness, and uncertainty annotation of handwritten segments (only evaluated on handwritten samples).
- **Composite:** Weighted average score based on the target document type (academic slide vs scan).