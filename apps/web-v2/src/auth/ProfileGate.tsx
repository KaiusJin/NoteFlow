import { useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supabase } from "../lib/supabase";
import { useAuth } from "./AuthProvider";

interface Profile {
  username: string;
  onboarding_completed: boolean;
}

const USERNAME_PATTERN = /^[a-z0-9_]{3,24}$/;

export function ProfileGate({ children }: { children: ReactNode }) {
  const { user, signOut } = useAuth();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");

  const profile = useQuery({
    queryKey: ["profile", user?.id],
    enabled: Boolean(user && supabase),
    queryFn: async () => {
      if (!user || !supabase) throw new Error("Authentication is unavailable.");
      const { data, error } = await supabase
        .from("profiles")
        .select("username,onboarding_completed")
        .eq("id", user.id)
        .single<Profile>();
      if (error) throw error;
      return data;
    },
    staleTime: 5 * 60_000,
    retry: 2
  });

  const completeOnboarding = useMutation({
    mutationFn: async (nextUsername: string) => {
      if (!user || !supabase) throw new Error("Authentication is unavailable.");
      const normalized = nextUsername.trim().toLowerCase();
      if (!USERNAME_PATTERN.test(normalized)) {
        throw new Error("Username must be 3–24 letters, numbers or underscores.");
      }
      const { data: available, error: availabilityError } = await supabase.rpc("username_available", {
        candidate: normalized
      });
      if (availabilityError) throw availabilityError;
      if (!available && normalized !== profile.data?.username) throw new Error("That username is already in use.");

      const { data, error } = await supabase
        .from("profiles")
        .update({ username: normalized, onboarding_completed: true })
        .eq("id", user.id)
        .select("username,onboarding_completed")
        .single<Profile>();
      if (error) throw error;
      return data;
    },
    onSuccess(data) {
      queryClient.setQueryData(["profile", user?.id], data);
    }
  });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await completeOnboarding.mutateAsync(username);
  }

  if (profile.isPending) {
    return <main className="loading-screen"><span className="loading-orbit" /><p>Preparing your profile…</p></main>;
  }

  if (profile.isError) {
    return (
      <main className="auth-layout">
        <section className="auth-card profile-card">
          <p className="eyebrow">Profile unavailable</p>
          <h1>We could not open your workspace</h1>
          <p className="form-message error" role="alert">{profile.error.message}</p>
          <button className="secondary-button" type="button" onClick={() => profile.refetch()}>Try again</button>
          <button className="text-button" type="button" onClick={() => signOut()}>Sign out</button>
        </section>
      </main>
    );
  }

  if (!profile.data.onboarding_completed) {
    return (
      <main className="auth-layout">
        <section className="auth-card profile-card">
          <p className="eyebrow">One last step</p>
          <h1>Choose your username</h1>
          <p>Your Google account is connected. Pick the name other workspace members will see.</p>
          <form onSubmit={submit}>
            <label>
              Username
              <input
                type="text"
                autoComplete="username"
                minLength={3}
                maxLength={24}
                pattern="[a-z0-9_]{3,24}"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
                autoFocus
              />
            </label>
            {completeOnboarding.error ? (
              <p className="form-message error" role="alert">{completeOnboarding.error.message}</p>
            ) : null}
            <button className="primary-button" type="submit" disabled={completeOnboarding.isPending}>
              {completeOnboarding.isPending ? "Saving…" : "Open my workspace"}
            </button>
          </form>
          <button className="text-button" type="button" onClick={() => signOut()}>Sign out</button>
        </section>
      </main>
    );
  }

  return children;
}
