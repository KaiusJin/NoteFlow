export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function renderRich(rawText) {
  if (rawText == null || rawText === "") return "";
  const text = String(rawText);
  const mathTokens = [];
  const protectedText = protectMath(text, mathTokens);
  let html = renderMarkdownSubset(protectedText);
  // Control-char sentinels never occur in study content and survive HTML
  // escaping intact, so placeholders cannot collide with text like "MATH239".
  html = html.replace(/\u0001(\d+)\u0001/g, (_, index) => renderMathToken(mathTokens[Number(index)]));
  return html;
}

function protectMath(text, tokens) {
  const patterns = [
    { re: /\$\$([\s\S]+?)\$\$/g, display: true },
    { re: /\\\[([\s\S]+?)\\\]/g, display: true },
    { re: /\\\(([\s\S]+?)\\\)/g, display: false },
    { re: /(?<![\\$])\$(?!\s)((?:\\.|[^$\\])+?)(?<!\s)\$(?!\d)/g, display: false },
  ];
  let output = text;
  for (const { re, display } of patterns) {
    output = output.replace(re, (_, tex) => {
      const index = tokens.push({ tex: tex.trim(), display }) - 1;
      return `\u0001${index}\u0001`;
    });
  }
  return output;
}

function renderMathToken(token) {
  if (!token) return "";
  if (typeof window.katex === "undefined") {
    return `<code class="math-fallback">${escapeHtml(token.tex)}</code>`;
  }
  try {
    return window.katex.renderToString(token.tex, {
      displayMode: token.display,
      throwOnError: false,
      output: "html",
    });
  } catch {
    return `<code class="math-fallback">${escapeHtml(token.tex)}</code>`;
  }
}

function renderMarkdownSubset(text) {
  const lines = text.split("\n");
  const blocks = [];
  let paragraph = [];
  let list = null;
  let code = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push(`<p>${paragraph.map(renderInline).join("<br/>")}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      const tag = list.ordered ? "ol" : "ul";
      blocks.push(`<${tag}>${list.items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</${tag}>`);
      list = null;
    }
  };

  for (const rawLine of lines) {
    if (code) {
      if (rawLine.trim().startsWith("```")) {
        blocks.push(`<pre class="code-block"><code>${code.lines.join("\n")}</code></pre>`);
        code = null;
      } else {
        code.lines.push(escapeHtml(rawLine));
      }
      continue;
    }
    const line = rawLine.replace(/\s+$/, "");
    const fenceMatch = line.trim().match(/^```(\w*)/);
    if (fenceMatch) {
      flushParagraph();
      flushList();
      code = { lang: fenceMatch[1], lines: [] };
      continue;
    }
    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      flushParagraph();
      flushList();
      const level = headingMatch[1].length;
      blocks.push(`<h${level} class="md-h md-h${level}">${renderInline(headingMatch[2])}</h${level}>`);
      continue;
    }
    const orderedMatch = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const bulletMatch = line.match(/^\s*[-*+]\s+(.*)$/);
    if (orderedMatch || bulletMatch) {
      flushParagraph();
      const ordered = Boolean(orderedMatch);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push((orderedMatch || bulletMatch)[1]);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  if (code) blocks.push(`<pre class="code-block"><code>${code.lines.join("\n")}</code></pre>`);
  flushParagraph();
  flushList();
  return blocks.join("");
}

function renderInline(text) {
  const codeSpans = [];
  let out = text.replace(/`([^`]+)`/g, (_, code) => {
    const index = codeSpans.push(code) - 1;
    return `\u0002${index}\u0002`;
  });
  out = escapeHtml(out);
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
  out = out.replace(/\u0002(\d+)\u0002/g, (_, index) => `<code>${escapeHtml(codeSpans[Number(index)])}</code>`);
  return out;
}

// ---------------------------------------------------------------------------
