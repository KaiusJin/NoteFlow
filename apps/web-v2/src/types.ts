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
