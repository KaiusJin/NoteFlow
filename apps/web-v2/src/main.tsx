import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { registerSW } from "virtual:pwa-register";
import { App } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import "./styles.css";

registerSW({ immediate: true });

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: (attempt, error) => attempt < 2 && !(error instanceof Error && error.message.includes("401"))
    }
  }
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
);
