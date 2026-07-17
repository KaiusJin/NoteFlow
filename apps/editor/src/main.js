import { Crepe } from "@milkdown/crepe";
import { $command, $markSchema, $remark, callCommand, insert, replaceAll } from "@milkdown/kit/utils";
import { undoCommand, redoCommand } from "@milkdown/kit/plugin/history";
import {
  turnIntoTextCommand,
  wrapInHeadingCommand,
  wrapInBulletListCommand,
  wrapInOrderedListCommand,
  wrapInBlockquoteCommand,
  createCodeBlockCommand,
} from "@milkdown/kit/preset/commonmark";
import "@milkdown/crepe/theme/common/style.css";
import "@milkdown/crepe/theme/frame.css";

// ---------------------------------------------------------------------------
// Math delimiter normalization
//
// The PDF pipeline and AI notes emit a mix of $...$, $$...$$, \(...\) and
// \[...\]. Milkdown's LaTeX feature (remark-math) only understands the dollar
// forms, so the backslash forms are rewritten before the markdown reaches the
// editor. Fenced code blocks and inline code spans are protected first so
// TeX-looking text inside code is never touched.
// ---------------------------------------------------------------------------
export function normalizeMathDelimiters(markdown) {
  if (!markdown) return "";
  const protectedSpans = [];
  const protect = (text) =>
    text.replace(/```[\s\S]*?(?:```|$)|`[^`\n]*`/g, (span) => {
      const index = protectedSpans.push(span) - 1;
      return `${index}`;
    });
  let output = protect(String(markdown));
  output = output.replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => `\n$$\n${tex.trim()}\n$$\n`);
  output = output.replace(/\\\(([\s\S]+?)\\\)/g, (_, tex) => `$${tex.trim()}$`);
  output = output.replace(/(\d+)/g, (_, index) => protectedSpans[Number(index)]);
  return output;
}

// ---------------------------------------------------------------------------
// Color mark: Notion-style text/background colors
//
// Stored in markdown as `<span style="color:...;background-color:...">…</span>`
// (the same encoding Notion uses for HTML export). A remark transform folds the
// raw open/close html nodes back into a custom `colorSpan` mdast node on load,
// and a remark-stringify handler emits the span again on save, so colors
// round-trip through plain markdown files.
// ---------------------------------------------------------------------------
const SPAN_OPEN_RE = /^<span style="([^"]*)"\s*>$/;
const SPAN_CLOSE_RE = /^<\/span>$/;

function parseStyleAttr(style) {
  const out = {};
  for (const part of String(style).split(";")) {
    const [key, value] = part.split(":");
    if (key && value) out[key.trim()] = value.trim();
  }
  return out;
}

function styleOf(attrs) {
  const parts = [];
  if (attrs.color) parts.push(`color:${attrs.color}`);
  if (attrs.background) parts.push(`background-color:${attrs.background}`);
  return parts.join(";");
}

function foldColorSpans(node) {
  if (!node || !Array.isArray(node.children)) return;
  node.children.forEach(foldColorSpans);
  const folded = [];
  for (let i = 0; i < node.children.length; i += 1) {
    const child = node.children[i];
    const open = child.type === "html" ? SPAN_OPEN_RE.exec(child.value || "") : null;
    if (open) {
      let closeIndex = -1;
      for (let j = i + 1; j < node.children.length; j += 1) {
        const candidate = node.children[j];
        if (candidate.type === "html" && SPAN_CLOSE_RE.test(candidate.value || "")) {
          closeIndex = j;
          break;
        }
      }
      if (closeIndex > -1) {
        const styles = parseStyleAttr(open[1]);
        folded.push({
          type: "colorSpan",
          color: styles.color || null,
          background: styles["background-color"] || null,
          children: node.children.slice(i + 1, closeIndex),
        });
        i = closeIndex;
        continue;
      }
    }
    folded.push(child);
  }
  node.children = folded;
}

function remarkColorSpan() {
  const data = this.data();
  const extensions = data.toMarkdownExtensions || (data.toMarkdownExtensions = []);
  extensions.push({
    handlers: {
      colorSpan(node, _parent, state, info) {
        const style = styleOf(node);
        return `<span style="${style}">` + state.containerPhrasing(node, info) + `</span>`;
      },
    },
  });
  return (tree) => foldColorSpans(tree);
}

const remarkColorSpanPlugin = $remark("remarkColorSpan", () => remarkColorSpan);

const colorMark = $markSchema("colorSpan", () => ({
  attrs: {
    color: { default: null },
    background: { default: null },
  },
  parseDOM: [
    {
      tag: "span[data-color-mark]",
      getAttrs: (dom) => ({
        color: dom.style.color || null,
        background: dom.style.backgroundColor || null,
      }),
    },
  ],
  toDOM: (mark) => ["span", { "data-color-mark": "", style: styleOf(mark.attrs) }, 0],
  parseMarkdown: {
    match: (node) => node.type === "colorSpan",
    runner: (state, node, markType) => {
      state.openMark(markType, { color: node.color || null, background: node.background || null });
      state.next(node.children);
      state.closeMark(markType);
    },
  },
  toMarkdown: {
    match: (mark) => mark.type.name === "colorSpan",
    runner: (state, mark) => {
      state.withMark(mark, "colorSpan", undefined, {
        color: mark.attrs.color,
        background: mark.attrs.background,
      });
    },
  },
}));

// Applies/merges/clears the color mark. With an empty selection the whole
// current text block is colored (Notion's block-color behaviour).
const setColorCommand = $command("SetColorSpan", (ctx) => (payload = {}) => (state, dispatch) => {
  const markType = colorMark.type(ctx);
  let { from, to } = state.selection;
  if (state.selection.empty) {
    const $from = state.selection.$from;
    if (!$from.parent.isTextblock) return false;
    from = $from.start();
    to = $from.end();
  }
  if (from === to) return false;
  if (dispatch) {
    let current = { color: null, background: null };
    state.doc.nodesBetween(from, to, (node) => {
      const existing = node.marks && node.marks.find((m) => m.type === markType);
      if (existing && current.color === null && current.background === null) {
        current = { ...existing.attrs };
      }
    });
    const next = {
      color: "color" in payload ? payload.color : current.color,
      background: "background" in payload ? payload.background : current.background,
    };
    let tr = state.tr.removeMark(from, to, markType);
    if (next.color || next.background) {
      tr = tr.addMark(from, to, markType.create(next));
    }
    dispatch(tr.scrollIntoView());
  }
  return true;
});

const TURN_INTO = {
  text: () => callCommand(turnIntoTextCommand.key),
  h1: () => callCommand(wrapInHeadingCommand.key, 1),
  h2: () => callCommand(wrapInHeadingCommand.key, 2),
  h3: () => callCommand(wrapInHeadingCommand.key, 3),
  h4: () => callCommand(wrapInHeadingCommand.key, 4),
  bullet: () => callCommand(wrapInBulletListCommand.key),
  ordered: () => callCommand(wrapInOrderedListCommand.key),
  quote: () => callCommand(wrapInBlockquoteCommand.key),
  code: () => callCommand(createCodeBlockCommand.key),
};

// ---------------------------------------------------------------------------
// Editor factory
//
// Usage from the static app:
//   const { createNoteFlowEditor } = await import("./vendor/editor/noteflow-editor.js");
//   const editor = await createNoteFlowEditor({ root, defaultValue, onMarkdownChange });
//   editor.getMarkdown();
//   editor.insertMarkdown("...");   // at current cursor position
//   editor.setMarkdown("...");      // replace whole document
//   editor.destroy();
// ---------------------------------------------------------------------------
export async function createNoteFlowEditor({ root, defaultValue = "", placeholder = "Start writing, or press / for commands…", onMarkdownChange }) {
  const crepe = new Crepe({
    root,
    defaultValue: normalizeMathDelimiters(defaultValue),
    featureConfigs: {
      [Crepe.Feature.Placeholder]: {
        text: placeholder,
        mode: "block",
      },
    },
  });

  crepe.editor.use(remarkColorSpanPlugin).use(colorMark).use(setColorCommand);

  if (typeof onMarkdownChange === "function") {
    crepe.on((listener) => {
      listener.markdownUpdated((_ctx, markdown, prevMarkdown) => {
        if (markdown !== prevMarkdown) onMarkdownChange(markdown);
      });
    });
  }

  await crepe.create();

  return {
    crepe,
    getMarkdown: () => crepe.getMarkdown(),
    insertMarkdown: (markdown) => {
      crepe.editor.action(insert(normalizeMathDelimiters(markdown)));
    },
    setMarkdown: (markdown) => {
      crepe.editor.action(replaceAll(normalizeMathDelimiters(markdown)));
    },
    setReadonly: (value) => crepe.setReadonly(value),
    undo: () => crepe.editor.action(callCommand(undoCommand.key)),
    redo: () => crepe.editor.action(callCommand(redoCommand.key)),
    turnInto: (kind) => {
      const command = TURN_INTO[kind];
      if (command) crepe.editor.action(command());
    },
    // payload: { color: "#hex" | null } and/or { background: "#hex" | null }
    setColor: (payload) => crepe.editor.action(callCommand(setColorCommand.key, payload)),
    destroy: () => crepe.destroy(),
  };
}
