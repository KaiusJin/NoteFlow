import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthGate } from "./auth/AuthGate";
import { ProfileGate } from "./auth/ProfileGate";
import { AppShell } from "./layout/AppShell";

const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const DocumentsPage = lazy(() => import("./pages/DocumentsPage"));
const DocumentDetailPage = lazy(() => import("./pages/DocumentDetailPage"));
const SearchPage = lazy(() => import("./pages/SearchPage"));
const AgentPage = lazy(() => import("./pages/AgentPage"));
const StudyPage = lazy(() => import("./pages/StudyPage"));
const FlashcardSessionPage = lazy(() => import("./pages/FlashcardSessionPage"));
const QuizSessionPage = lazy(() => import("./pages/QuizSessionPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

function PageLoader() {
  return <div className="page-loader" role="status">Loading module…</div>;
}

export function App() {
  return (
    <AuthGate>
      <ProfileGate>
        <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<DashboardPage />} />
              <Route path="documents" element={<DocumentsPage />} />
              <Route path="documents/:documentId" element={<DocumentDetailPage />} />
              <Route path="search" element={<SearchPage />} />
              <Route path="agent" element={<AgentPage />} />
              <Route path="study" element={<StudyPage />} />
              <Route path="study/flashcards/:deckId" element={<FlashcardSessionPage />} />
              <Route path="study/quizzes/:quizId" element={<QuizSessionPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </Suspense>
      </ProfileGate>
    </AuthGate>
  );
}
