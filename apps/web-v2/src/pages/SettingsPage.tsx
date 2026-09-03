import { useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { PageHeading } from "../components/PageHeading";

export default function SettingsPage() {
  const auth = useAuth();
  const [offlineDrafts, setOfflineDrafts] = useState(true);
  const [taskNotices, setTaskNotices] = useState(true);

  return (
    <div className="page settings-page">
      <PageHeading eyebrow="Workspace controls" title="Settings" description="Account, offline behavior and notification preferences." />
      <div className="settings-grid">
        <section className="panel settings-section"><p className="eyebrow">Account</p><h2>Personal workspace</h2><dl className="settings-list"><div><dt>Email</dt><dd>{auth.user?.email}</dd></div><div><dt>User ID</dt><dd className="mono">{auth.user?.id}</dd></div><div><dt>Session</dt><dd><span className="status-pill status-ready">Authenticated</span></dd></div></dl><button className="secondary-button" type="button" onClick={() => void auth.signOut()}>Sign out</button></section>
        <section className="panel settings-section"><p className="eyebrow">Offline & notifications</p><h2>Keep work resilient</h2><label className="toggle-row"><span><strong>Offline drafts</strong><small>Save edits to this device before server sync.</small></span><input type="checkbox" checked={offlineDrafts} onChange={(event) => setOfflineDrafts(event.target.checked)} /></label><label className="toggle-row"><span><strong>Task completion</strong><small>Notify when parsing or generation finishes.</small></span><input type="checkbox" checked={taskNotices} onChange={(event) => setTaskNotices(event.target.checked)} /></label><p className="field-note">Preferences will move to the server profile once the workspace migration is active.</p></section>
        <section className="panel settings-section span-two"><p className="eyebrow">AI usage</p><h2>Platform-managed models</h2><p>NoteFlow uses server-managed model keys. Usage, cost limits and retries are recorded per task; provider secrets are never sent to the browser.</p><div className="budget-row"><span>Per-document budget</span><strong>Configured by server policy</strong></div></section>
      </div>
    </div>
  );
}
