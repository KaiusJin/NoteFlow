import type { ReactNode } from "react";
import { AuthScreen } from "./AuthScreen";
import { useAuth } from "./AuthProvider";

export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  if (auth.loading) {
    return <main className="loading-screen"><span className="loading-orbit" /><p>Opening your workspace…</p></main>;
  }
  if (!auth.session) return <AuthScreen />;
  return children;
}
