// Pure-function tests (Markdown parser, graph layout): node web/tests/pure.mjs
import { parseMarkdown, parseInline } from "../js/md.js";
import { forceLayout } from "../js/graphlayout.js";
import assert from "node:assert";
const md = "# Title\n\nSome `code_here` and **bold** and _note_ plus snake_case_name.\n\n- one\n- two `x`\n\n1. a\n2. b\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```py\nprint(1)\n```\n> quoted\n";
const b = parseMarkdown(md);
assert.deepStrictEqual(b.map(x => x.t), ["h","p","ul","ol","table","code","quote"]);
assert.deepStrictEqual(parseInline("a `b` **c** _d_ snake_case_name").map(t=>t.t), ["text","code","text","b","text","i","text"]);
assert.strictEqual(b[4].rows[0][1], "2"); assert.strictEqual(b[5].text, "print(1)");
assert.strictEqual(parseInline("<img src=x onerror=alert(1)>")[0].v, "<img src=x onerror=alert(1)>"); // stays text
const nodes = Array.from({length: 40}, (_, i) => ({id: "n"+i})); const edges = nodes.slice(1).map((n,i)=>({source:"n0",target:n.id}));
const p1 = forceLayout(nodes, edges, {pinned:"n0"}), p2 = forceLayout(nodes, edges, {pinned:"n0"});
for (const [id,p] of p1) { assert(Number.isFinite(p.x) && p.x>=40 && p.x<=860 && p.y>=30 && p.y<=570); assert.deepStrictEqual(p, p2.get(id)); }
assert.deepStrictEqual(p1.get("n0"), {x:450,y:300});
console.log("js pure-function tests ok");
