import type { Session, User } from "@supabase/supabase-js";
import { createContext, use, useEffect, useMemo, useState, type ReactNode } from "react";
import { hasSupabaseConfig, supabase } from "../lib/supabase";

interface AuthContextValue {
  configured: boolean;
  loading: boolean;
  session: Session | null;
  user: User | null;
  signIn(email: string, password: string): Promise<void>;
  signUp(username: string, email: string, password: string): Promise<{ requiresVerification: boolean }>;
  verifySignUp(email: string, token: string): Promise<void>;
  resendSignUpCode(email: string): Promise<void>;
  signInWithGoogle(): Promise<void>;
  signOut(): Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(hasSupabaseConfig);

  useEffect(() => {
    if (!supabase) return;
    let active = true;
    void supabase.auth.getSession().then(({ data }) => {
      if (active) {
        setSession(data.session);
        setLoading(false);
      }
    });
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setLoading(false);
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    configured: hasSupabaseConfig,
    loading,
    session,
    user: session?.user ?? null,
    async signIn(email, password) {
      if (!supabase) throw new Error("Supabase is not configured.");
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) throw error;
    },
    async signUp(username, email, password) {
      if (!supabase) throw new Error("Supabase is not configured.");
      const normalizedUsername = username.trim().toLowerCase();
      const { data: available, error: availabilityError } = await supabase.rpc("username_available", {
        candidate: normalizedUsername
      });
      if (availabilityError) throw availabilityError;
      if (!available) throw new Error("That username is already in use.");

      const { data, error } = await supabase.auth.signUp({
        email: email.trim().toLowerCase(),
        password,
        options: { data: { username: normalizedUsername } }
      });
      if (error) throw error;
      return { requiresVerification: !data.session };
    },
    async verifySignUp(email, token) {
      if (!supabase) throw new Error("Supabase is not configured.");
      const { error } = await supabase.auth.verifyOtp({
        email: email.trim().toLowerCase(),
        token,
        type: "signup"
      });
      if (error) throw error;
    },
    async resendSignUpCode(email) {
      if (!supabase) throw new Error("Supabase is not configured.");
      const { error } = await supabase.auth.resend({
        type: "signup",
        email: email.trim().toLowerCase()
      });
      if (error) throw error;
    },
    async signInWithGoogle() {
      if (!supabase) throw new Error("Supabase is not configured.");
      const { error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo: window.location.origin }
      });
      if (error) throw error;
    },
    async signOut() {
      if (!supabase) return;
      const { error } = await supabase.auth.signOut();
      if (error) throw error;
    }
  }), [loading, session]);

  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthContextValue {
  const context = use(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
