export function StatusPill({ status }: { status: string | null | undefined }) {
  const value = status || "UNKNOWN";
  return <span className={`status-pill status-${value.toLowerCase()}`}>{value.replaceAll("_", " ")}</span>;
}
