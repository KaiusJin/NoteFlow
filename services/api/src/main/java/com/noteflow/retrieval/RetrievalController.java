package com.noteflow.retrieval;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class RetrievalController {
    private final RetrievalService retrieval;

    public RetrievalController(RetrievalService retrieval) {
        this.retrieval = retrieval;
    }

    @PostMapping("/retrieval")
    public RetrievalResponse retrieve(@RequestBody RetrievalRequest request) {
        return retrieval.retrieve(request);
    }
}
