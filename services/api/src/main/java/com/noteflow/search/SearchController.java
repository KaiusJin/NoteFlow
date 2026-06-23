package com.noteflow.search;

import java.util.UUID;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SearchController {
    private final SearchService search;

    public SearchController(SearchService search) {
        this.search = search;
    }

    @PostMapping("/documents/{documentId}/embeddings")
    public GenerateEmbeddingsResponse generateEmbeddings(@PathVariable UUID documentId) {
        return search.generateEmbeddings(documentId);
    }

    @PostMapping("/documents/{documentId}/search")
    public SearchResponse searchDocument(@PathVariable UUID documentId, @RequestBody SearchRequest request) {
        return search.searchDocument(documentId, request);
    }

    @PostMapping("/search")
    public SearchResponse search(@RequestBody SearchRequest request) {
        return search.search(request);
    }
}
