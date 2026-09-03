import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { EmptyState } from "../components/EmptyState";
import { PageHeading } from "../components/PageHeading";
import { apiRequest } from "../lib/api";
import type { ConversationMessage, ConversationSummary } from "../types";

interface SendResponse { conversationId: string; assistantMessageId: string; taskId: string; status: string }

export default function AgentPage() {
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const conversations = useQuery({ queryKey: ["conversations"], queryFn: () => apiRequest<ConversationSummary[]>("/conversations") });
  useEffect(() => {
    if (!activeId && conversations.data?.[0]) setActiveId(conversations.data[0].id);
  }, [activeId, conversations.data]);

  const messages = useQuery({
    queryKey: ["conversation", activeId, "messages"],
    queryFn: () => apiRequest<ConversationMessage[]>(`/conversations/${activeId}/messages`),
    enabled: Boolean(activeId),
    refetchInterval: (query) => query.state.data?.some((item) => item.status === "GENERATING") ? 1_500 : false
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.data]);

  const create = useMutation({
    mutationFn: () => apiRequest<ConversationSummary>("/conversations", { method: "POST", body: JSON.stringify({ title: "New conversation" }) }),
    onSuccess: async (conversation) => {
      setActiveId(conversation.id);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    }
  });

  const send = useMutation({
    mutationFn: async (content: string) => {
      let conversationId = activeId;
      if (!conversationId) {
        const conversation = await apiRequest<ConversationSummary>("/conversations", { method: "POST", body: JSON.stringify({ title: "New conversation" }) });
        conversationId = conversation.id;
        setActiveId(conversationId);
      }
      return apiRequest<SendResponse>(`/conversations/${conversationId}/messages`, {
        method: "POST",
        body: JSON.stringify({ content, pdfDocumentIds: [], aiNoteDocumentIds: [], allowAgentWrites: false, allowAgentDeletes: false })
      });
    },
    onSuccess: async (response) => {
      setMessage("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", response.conversationId, "messages"] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] })
      ]);
    }
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const content = message.trim();
    if (content) send.mutate(content);
  }

  const conversationMessages = messages.data ?? [];

  return (
    <div className="page agent-page">
      <PageHeading
        eyebrow="Grounded assistant"
        title="AI Agent"
        description="Ask across your sources. Every factual answer should lead back to evidence."
        actions={<button className="secondary-button" type="button" disabled={create.isPending} onClick={() => create.mutate()}>＋ New conversation</button>}
      />
      <div className="agent-layout">
        <aside className="conversation-list" aria-label="Conversations">
          {(conversations.data ?? []).map((conversation) => (
            <button key={conversation.id} type="button" className={conversation.id === activeId ? "conversation-button active" : "conversation-button"} onClick={() => setActiveId(conversation.id)}>
              <span>✦</span><strong>{conversation.title}</strong>
            </button>
          ))}
        </aside>
        <section className="chat-panel">
          <div className="messages" aria-live="polite">
            {!activeId || (!messages.isLoading && conversationMessages.length === 0) ? (
              <EmptyState icon="✦" title="Ask from your own material">Try “Explain the central argument and cite the exact pages.”</EmptyState>
            ) : null}
            {conversationMessages.map((item) => (
              <article key={item.id} className={`message message-${item.role.toLowerCase()}`}>
                <span>{item.role === "USER" ? "You" : "NoteFlow"}</span>
                <div>{item.content_markdown || (item.status === "GENERATING" ? "Reading your sources…" : "")}</div>
              </article>
            ))}
            <div ref={bottomRef} />
          </div>
          <form className="composer" onSubmit={submit}>
            <label htmlFor="agent-message" className="sr-only">Ask NoteFlow</label>
            <textarea id="agent-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Ask a grounded question…" rows={2} />
            <button className="send-button" type="submit" disabled={!message.trim() || send.isPending} aria-label="Send message">{send.isPending ? "…" : "↑"}</button>
          </form>
          {send.isError ? <p className="composer-error" role="alert">{send.error.message}</p> : null}
        </section>
      </div>
    </div>
  );
}
