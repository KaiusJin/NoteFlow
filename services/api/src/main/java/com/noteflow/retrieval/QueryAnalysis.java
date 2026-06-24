package com.noteflow.retrieval;

import java.util.List;

record QueryAnalysis(String originalQuery, String lexicalQuery, List<String> exactSignals) {
    boolean hasExactSignals() {
        return !exactSignals.isEmpty();
    }

    boolean hasLexicalQuery() {
        return lexicalQuery != null && !lexicalQuery.isBlank();
    }
}
