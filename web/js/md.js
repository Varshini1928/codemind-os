// Minimal, safe Markdown renderer: parse to a small AST (pure, unit-tested in Node), then build DOM nodes.
export function parseInline(text) {
  const out = [];
  const rx = /(`[^`]+`|\*\*[^*]+\*\*|(?<![\w])_[^_]+_(?![\w]))/g;
  let last = 0, m;
  while ((m = rx.exec(text)) !== null) {
    if (m.index > last) out.push({ t: "text", v: text.slice(last, m.index) });
    const tok = m[0];
    if (tok.startsWith("`")) out.push({ t: "code", v: tok.slice(1, -1) });
    else if (tok.startsWith("**")) out.push({ t: "b", v: tok.slice(2, -2) });
    else out.push({ t: "i", v: tok.slice(1, -1) });
    last = m.index + tok.length;
  }
  if (last < text.length) out.push({ t: "text", v: text.slice(last) });
  return out;
}

const splitRow = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export function parseMarkdown(src) {
  const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = line.match(/^\s*```(\w*)/);
    if (fence) {
      const buf = []; i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      blocks.push({ t: "code", lang: fence[1], text: buf.join("\n") });
      continue;
    }
    const hd = line.match(/^(#{1,6})\s+(.*)$/);
    if (hd) { blocks.push({ t: "h", level: hd[1].length, text: hd[2] }); i++; continue; }
    if (/^\s*---+\s*$/.test(line)) { blocks.push({ t: "hr" }); i++; continue; }
    if (line.trim().startsWith("|") && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const head = splitRow(line); i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(splitRow(lines[i++]));
      blocks.push({ t: "table", head, rows });
      continue;
    }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      blocks.push({ t: "quote", text: buf.join(" ") });
      continue;
    }
    if (/^\s*([-*])\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items = [];
      while (i < lines.length && (/^\s*([-*])\s+/.test(lines[i]) || /^\s*\d+[.)]\s+/.test(lines[i]))) {
        items.push(lines[i++].replace(/^\s*([-*]|\d+[.)])\s+/, ""));
      }
      blocks.push({ t: ordered ? "ol" : "ul", items });
      continue;
    }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*```|\s*>|\s*([-*]|\d+[.)])\s+|\s*\|)/.test(lines[i])) buf.push(lines[i++].trim());
    if (!buf.length) { buf.push(line.trim()); i++; }
    blocks.push({ t: "p", text: buf.join(" ") });
  }
  return blocks;
}

export function renderMarkdown(src, h) {
  const frag = document.createDocumentFragment();
  const inline = (text) => parseInline(text).map((tok) =>
    tok.t === "code" ? h("code", { class: "inline-code", text: tok.v })
      : tok.t === "b" ? h("strong", { text: tok.v }) : tok.t === "i" ? h("em", { text: tok.v }) : tok.v);
  for (const b of parseMarkdown(src)) {
    if (b.t === "h") frag.append(h(`h${Math.min(6, b.level + 1)}`, { class: "md-h" }, inline(b.text)));
    else if (b.t === "p") frag.append(h("p", {}, inline(b.text)));
    else if (b.t === "ul" || b.t === "ol") frag.append(h(b.t, { class: "md-list" }, b.items.map((it) => h("li", {}, inline(it)))));
    else if (b.t === "code") frag.append(h("pre", { class: "code-block" }, h("code", { text: b.text })));
    else if (b.t === "quote") frag.append(h("blockquote", {}, inline(b.text)));
    else if (b.t === "hr") frag.append(h("hr"));
    else if (b.t === "table") frag.append(h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, b.head.map((c) => h("th", {}, inline(c))))),
      h("tbody", {}, b.rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, inline(c)))))))));
  }
  return frag;
}
