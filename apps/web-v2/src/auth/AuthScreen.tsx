import { useState, type FormEvent } from "react";
import { useAuth } from "./AuthProvider";

type AuthMode = "sign-in" | "sign-up" | "verify";

const USERNAME_PATTERN = /^[a-zA-Z0-9_]{3,24}$/;

function validatePassword(password: string): string | null {
  if (password.length < 10) return "Use at least 10 characters.";
  if (!/[A-Za-z]/.test(password) || !/[0-9]/.test(password)) {
    return "Include at least one letter and one number.";
  }
  return null;
}

export function AuthScreen() {
  const auth = useAuth();
  const [mode, setMode] = useState<AuthMode>("sign-in");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "sign-in") {
        await auth.signIn(email.trim(), password);
      } else if (mode === "sign-up") {
        const normalizedUsername = username.trim().toLowerCase();
        if (!USERNAME_PATTERN.test(normalizedUsername)) {
          throw new Error("Username must be 3–24 letters, numbers or underscores.");
        }
        const passwordError = validatePassword(password);
        if (passwordError) throw new Error(passwordError);
        if (password !== passwordConfirmation) throw new Error("Passwords do not match.");

        const result = await auth.signUp(normalizedUsername, email.trim(), password);
        if (result.requiresVerification) {
          setMode("verify");
          setPassword("");
          setPasswordConfirmation("");
          setNotice("We sent a 6-digit verification code to your email.");
        }
      } else {
        if (!/^\d{6}$/.test(verificationCode)) throw new Error("Enter the 6-digit code from your email.");
        await auth.verifySignUp(email.trim(), verificationCode);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Authentication failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function continueWithGoogle() {
    setSubmitting(true);
    setError(null);
    try {
      await auth.signInWithGoogle();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Google sign-in failed.");
      setSubmitting(false);
    }
  }

  async function resendCode() {
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      await auth.resendSignUpCode(email);
      setNotice("A new code was sent. Check your inbox and spam folder.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to resend the code.");
    } finally {
      setSubmitting(false);
    }
  }

  function switchMode(nextMode: Exclude<AuthMode, "verify">) {
    setMode(nextMode);
    setError(null);
    setNotice(null);
    setVerificationCode("");
  }

  if (!auth.configured) {
    return (
      <main className="auth-layout">
        <section className="auth-card config-card">
          <Brand />
          <p className="eyebrow">Configuration required</p>
          <h1>Connect your Supabase project</h1>
          <p>Copy <code>.env.example</code> to <code>.env.local</code>, then set the project URL, anon key and API URL.</p>
          <pre>VITE_SUPABASE_URL=https://…supabase.co{"\n"}VITE_SUPABASE_ANON_KEY=…{"\n"}VITE_API_BASE_URL=http://localhost:8080</pre>
        </section>
      </main>
    );
  }

  return (
    <main className="auth-layout">
      <section className="auth-story" aria-label="NoteFlow product introduction">
        <Brand />
        <p className="eyebrow">Grounded learning, from your own sources</p>
        <h1>Read less blindly.<br />Remember more deliberately.</h1>
        <p className="auth-lede">Turn dense technical PDFs into traceable notes, evidence-backed answers and focused review sessions.</p>
        <div className="auth-proof">
          <span>Source citations</span><span>Math-ready notes</span><span>Adaptive review</span>
        </div>
      </section>

      <section className="auth-card">
        <p className="eyebrow">
          {mode === "sign-in" ? "Welcome back" : mode === "sign-up" ? "Create your workspace" : "Verify your email"}
        </p>
        <h2>
          {mode === "sign-in" ? "Continue studying" : mode === "sign-up" ? "Start with NoteFlow" : "Enter your 6-digit code"}
        </h2>
        <form onSubmit={submit}>
          {mode === "sign-up" ? (
            <label>
              Username
              <input
                type="text"
                autoComplete="username"
                minLength={3}
                maxLength={24}
                pattern="[A-Za-z0-9_]{3,24}"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                aria-describedby="username-help"
                required
              />
              <span className="field-note" id="username-help">3–24 letters, numbers or underscores.</span>
            </label>
          ) : null}
          <label>
            Email
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              readOnly={mode === "verify"}
              required
            />
          </label>
          {mode !== "verify" ? (
            <label>
              Password
              <input
                type="password"
                autoComplete={mode === "sign-in" ? "current-password" : "new-password"}
                minLength={mode === "sign-up" ? 10 : undefined}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </label>
          ) : (
            <label>
              Verification code
              <input
                className="verification-input"
                type="text"
                autoComplete="one-time-code"
                inputMode="numeric"
                minLength={6}
                maxLength={6}
                pattern="[0-9]{6}"
                value={verificationCode}
                onChange={(event) => setVerificationCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                autoFocus
                required
              />
            </label>
          )}
          {mode === "sign-up" ? (
            <label>
              Confirm password
              <input
                type="password"
                autoComplete="new-password"
                minLength={10}
                value={passwordConfirmation}
                onChange={(event) => setPasswordConfirmation(event.target.value)}
                required
              />
            </label>
          ) : null}
          {error ? <p className="form-message error" role="alert">{error}</p> : null}
          {notice ? <p className="form-message success" role="status" aria-live="polite">{notice}</p> : null}
          <button className="primary-button" type="submit" disabled={submitting}>
            {submitting ? "Please wait…" : mode === "sign-in" ? "Sign in" : mode === "sign-up" ? "Create account" : "Verify email"}
          </button>
        </form>
        {mode !== "verify" ? (
          <>
            <div className="auth-divider"><span>or</span></div>
            <button className="google-button" type="button" onClick={continueWithGoogle} disabled={submitting}>
              <GoogleMark /> Continue with Google
            </button>
            <button className="text-button" type="button" onClick={() => switchMode(mode === "sign-in" ? "sign-up" : "sign-in")}>
              {mode === "sign-in" ? "New to NoteFlow? Create an account" : "Already have an account? Sign in"}
            </button>
          </>
        ) : (
          <div className="verification-actions">
            <button className="text-button" type="button" onClick={resendCode} disabled={submitting}>Resend code</button>
            <button className="text-button" type="button" onClick={() => switchMode("sign-up")}>Change email</button>
          </div>
        )}
      </section>
    </main>
  );
}

function Brand() {
  return <div className="brand"><span className="brand-mark">N</span><span>NoteFlow</span></div>;
}

function GoogleMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18">
      <path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.4-.18-2.06H12v3.9h5.38a4.6 4.6 0 0 1-2 3.02v2.53h3.24c1.9-1.75 2.98-4.33 2.98-7.39Z" />
      <path fill="#34A853" d="M12 22c2.7 0 4.98-.9 6.63-2.38l-3.24-2.53c-.9.6-2.05.96-3.39.96-2.61 0-4.83-1.76-5.62-4.13H3.03v2.61A10 10 0 0 0 12 22Z" />
      <path fill="#FBBC05" d="M6.38 13.92A6.02 6.02 0 0 1 6.06 12c0-.67.12-1.31.32-1.92V7.47H3.03A10 10 0 0 0 2 12c0 1.61.39 3.14 1.03 4.53l3.35-2.61Z" />
      <path fill="#EA4335" d="M12 5.95c1.47 0 2.79.5 3.82 1.5l2.88-2.88A9.65 9.65 0 0 0 12 2a10 10 0 0 0-8.97 5.47l3.35 2.61C7.17 7.71 9.39 5.95 12 5.95Z" />
    </svg>
  );
}
