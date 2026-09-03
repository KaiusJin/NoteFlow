export type DocumentStatus = "UPLOADED" | "PARSING" | "READY" | "FAILED";

export interface DocumentSummary {
  id: string;
  title: string;
  originalFilename: string;
  fileType: string;
  fileSize: number;
  pageCount: number | null;
  documentType: string;
  contentSourceType: string;
  status: DocumentStatus;
  aiNoteStatus: string | null;
  embeddingStatus: string | null;
  createdAt: string;
}

export interface TaskSummary {
  id: string;
  documentId: string;
  taskType: string;
  status: "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED";
  currentStep: string;
  progress: number;
  errorMessage: string | null;
  retryCount: number;
  priority: number;
  createdAt: string;
}

export interface SearchResult {
  sourceDomain: string;
  sourceObjectType: string;
  sourceObjectId: string;
  documentId: string;
  pageStart: number | null;
  pageEnd: number | null;
  title: string;
  snippet: string;
  score: number;
  metadataJson: string | null;
}

export interface SearchResponse {
  query: string;
  mode: "PDF" | "AI_NOTE" | "MIXED" | "CUSTOM";
  results: SearchResult[];
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at?: string;
  updated_at?: string;
}

export interface ConversationMessage {
  id: string;
  role: "USER" | "ASSISTANT" | "TOOL";
  content_markdown: string;
  status?: string;
  created_at?: string;
}

export interface AiNote {
  id: string;
  documentId: string;
  noteVersion: number;
  status: string;
  title: string;
  markdown: string | null;
  summary: string | null;
  createdAt: string;
}

export interface EditableNote {
  id: string;
  documentId: string;
  title: string;
  markdown: string;
  sourceKind: "BLANK" | "RAW" | "AI_NOTE";
  createdAt: string;
  updatedAt: string;
}

export interface StudySet {
  id: string;
  title: string;
  status: string;
  version: number;
  created_at: string;
}

export interface Flashcard {
  id: string;
  card_type: string;
  front: string;
  back: string;
  cloze_text: string | null;
  difficulty: string;
  topic: string | null;
  hint: string | null;
  source_pages_json: string | null;
  review_status?: string;
  due_at?: string | null;
}

export interface QuizQuestion {
  id: string;
  question_type: string;
  difficulty: string;
  topic: string | null;
  stem: string;
  options_json: string | null;
  points: number;
  source_pages_json: string | null;
}

export interface QuizAttemptResult {
  attempt: {
    id: string;
    status: "IN_PROGRESS" | "GRADING" | "COMPLETED";
    score: number | null;
    max_score: number | null;
  };
  answers: Array<{
    question_id: string;
    stem: string;
    user_response: string;
    is_correct: boolean | null;
    awarded_points: number | null;
    feedback: string | null;
    correct_answer: string | null;
    explanation: string | null;
    points: number;
  }>;
}
