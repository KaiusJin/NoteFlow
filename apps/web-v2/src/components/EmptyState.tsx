import type { ReactNode } from "react";

export function EmptyState({ icon, title, children, action }: {
  icon: string;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return <div className="empty-state"><span className="empty-icon">{icon}</span><h3>{title}</h3><p>{children}</p>{action}</div>;
}
