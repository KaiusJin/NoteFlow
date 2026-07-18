from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# ---- Flashcards ----------------------------------------------------------

FLASHCARD_TYPES = {"DEFINITION", "CONCEPT_QA", "FORMULA", "THEOREM", "CLOZE"}
DIFFICULTIES = {"EASY", "MEDIUM", "HARD"}

REVIEW_GRADES = {"AGAIN", "HARD", "GOOD", "EASY"}
REVIEW_STATUSES = {"NEW", "LEARNING", "REVIEW", "SUSPENDED"}

DECK_STATUS_GENERATING = "GENERATING"
DECK_STATUS_READY = "READY"
DECK_STATUS_FAILED = "FAILED"


@dataclass(frozen=True)
class FlashcardCandidate:
    """A validated flashcard produced by generation, before persistence."""

    card_type: str
    front: str
    back: str
    cloze_text: str
    difficulty: str
    topic: str
    hint: str
    tags: list[str]
    source_chunk_indexes: list[int]
    confidence: float
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Flashcard:
    id: str
    deck_id: str
    document_id: str
    card_index: int
    card_type: str
    front: str
    back: str
    cloze_text: str
    difficulty: str
    topic: str
    hint: str
    tags: list[str]
    source_group_index: int
    item_index: int
    source_chunk_ids: list[str]
    source_pages: list[int]
    dedupe_hash: str
    confidence: float
    warnings_json: str
    metadata_json: str


@dataclass(frozen=True)
class ReviewState:
    """SM-2 review state, keyed per (user, flashcard)."""

    user_id: str
    flashcard_id: str
    status: str
    ease_factor: float
    interval_days: int
    repetitions: int
    due_at: datetime | None
    last_reviewed_at: datetime | None
    last_grade: str | None


# ---- Quiz ----------------------------------------------------------------

QUESTION_TYPES = {
    "CONCEPTUAL",
    "CALCULATION",
    "PROOF",
    "MULTIPLE_CHOICE",
    "SHORT_ANSWER",
    "TRUE_FALSE",
}
OBJECTIVE_QUESTION_TYPES = {"MULTIPLE_CHOICE", "TRUE_FALSE"}
FREE_TEXT_QUESTION_TYPES = {"CONCEPTUAL", "CALCULATION", "PROOF", "SHORT_ANSWER"}

QUIZ_STATUS_GENERATING = "GENERATING"
QUIZ_STATUS_READY = "READY"
QUIZ_STATUS_FAILED = "FAILED"

ATTEMPT_STATUS_IN_PROGRESS = "IN_PROGRESS"
ATTEMPT_STATUS_GRADING = "GRADING"
ATTEMPT_STATUS_COMPLETED = "COMPLETED"

GRADED_BY_AUTO = "AUTO"
GRADED_BY_LLM = "LLM"


@dataclass(frozen=True)
class RubricPoint:
    point: str
    weight: float


@dataclass(frozen=True)
class QuizQuestionCandidate:
    question_type: str
    difficulty: str
    topic: str
    stem: str
    options: list[str]
    correct_answer: str
    answer_key: str
    rubric: list[RubricPoint]
    explanation: str
    related_formula: str
    common_mistake: str
    distractor_rationale: list[str]
    points: float
    source_chunk_indexes: list[int]
    confidence: float
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QuizQuestion:
    id: str
    quiz_set_id: str
    document_id: str
    question_index: int
    question_type: str
    difficulty: str
    topic: str
    stem: str
    options_json: str
    correct_answer: str
    answer_key: str
    rubric_json: str
    explanation: str
    related_formula: str
    common_mistake: str
    distractor_rationale_json: str
    points: float
    source_group_index: int
    item_index: int
    source_chunk_ids: list[str]
    source_pages: list[int]
    dedupe_hash: str
    confidence: float
    warnings_json: str


@dataclass(frozen=True)
class QuizAnswerToGrade:
    answer_id: str
    question: QuizQuestion
    user_response: str


@dataclass(frozen=True)
class GradeResult:
    is_correct: bool
    awarded_points: float
    feedback: str
    key_points_hit: list[bool]
    graded_by: str

