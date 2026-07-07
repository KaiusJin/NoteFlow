package com.noteflow.conversations;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/conversations")
public class ConversationController {
    private final ConversationService conversations;

    public ConversationController(ConversationService conversations) { this.conversations = conversations; }

    @PostMapping
    public Map<String, Object> create(@RequestBody(required = false) CreateRequest request) {
        return conversations.create(request == null ? null : request.title());
    }

    @GetMapping
    public List<Map<String, Object>> list() { return conversations.list(); }

    @GetMapping("/{conversationId}/messages")
    public List<Map<String, Object>> messages(@PathVariable UUID conversationId) {
        return conversations.messages(conversationId);
    }

    @PostMapping("/{conversationId}/messages")
    public Map<String, Object> send(@PathVariable UUID conversationId, @RequestBody SendRequest request) {
        return conversations.send(conversationId, request.content(), request.pdfDocumentIds(), request.aiNoteDocumentIds());
    }

    @GetMapping("/messages/{messageId}")
    public Map<String, Object> message(@PathVariable UUID messageId) { return conversations.message(messageId); }

    public record CreateRequest(String title) {}
    public record SendRequest(String content, List<UUID> pdfDocumentIds, List<UUID> aiNoteDocumentIds) {}
}
