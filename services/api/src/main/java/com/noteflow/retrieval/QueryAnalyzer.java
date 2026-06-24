package com.noteflow.retrieval;

import java.text.Normalizer;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;

@Component
class QueryAnalyzer {
    private static final Set<String> STOP_WORDS = Set.of(
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
        "explain", "for", "from", "how", "in", "is", "it", "of", "on", "or",
        "the", "to", "what", "when", "where", "which", "why", "with"
    );
    private static final Pattern LEXICAL_TOKEN = Pattern.compile("[\\p{L}\\p{N}_]{2,}");
    private static final Pattern QUOTED_PHRASE = Pattern.compile("[\"“”]([^\"“”]{2,120})[\"“”]");
    private static final Pattern NUMBERED_LABEL = Pattern.compile("\\b\\d+(?:\\.\\d+){1,4}\\b");
    private static final Pattern CODE_IDENTIFIER = Pattern.compile(
        "\\b(?:[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|[A-Za-z]{2,}\\d+[A-Za-z0-9_]*)\\b"
    );
    private static final Pattern BRACKET_EXPRESSION = Pattern.compile(
        "\\b[A-Za-z]+\\[[^\\]\\n]{1,80}\\](?:\\^\\d+)?"
    );
    private static final Pattern COMPLEXITY_EXPRESSION = Pattern.compile(
        "\\b[OΘΩ]\\([^\\)\\n]{1,80}\\)"
    );
    private static final Pattern LATEX_COMMAND = Pattern.compile("\\\\[A-Za-z]{2,}(?:\\{[^\\}\\n]{1,80}\\})?");

    QueryAnalysis analyze(String query) {
        String normalized = Normalizer.normalize(query, Normalizer.Form.NFKC).strip();
        Set<String> signals = new LinkedHashSet<>();
        collectGroup(QUOTED_PHRASE, normalized, signals, 1);
        collectGroup(NUMBERED_LABEL, normalized, signals, 0);
        collectGroup(CODE_IDENTIFIER, normalized, signals, 0);
        collectGroup(BRACKET_EXPRESSION, normalized, signals, 0);
        collectGroup(COMPLEXITY_EXPRESSION, normalized, signals, 0);
        collectGroup(LATEX_COMMAND, normalized, signals, 0);
        return new QueryAnalysis(normalized, buildLexicalQuery(normalized), new ArrayList<>(signals));
    }

    private void collectGroup(Pattern pattern, String query, Set<String> signals, int group) {
        Matcher matcher = pattern.matcher(query);
        while (matcher.find()) {
            String signal = matcher.group(group).strip();
            if (signal.length() >= 2) {
                signals.add(signal);
            }
        }
    }

    private String buildLexicalQuery(String query) {
        Set<String> terms = new LinkedHashSet<>();
        Matcher matcher = LEXICAL_TOKEN.matcher(query.toLowerCase(Locale.ROOT));
        while (matcher.find()) {
            String term = matcher.group();
            if (!STOP_WORDS.contains(term)) {
                terms.add(term);
            }
        }
        return String.join(" OR ", terms);
    }
}
