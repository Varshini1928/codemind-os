"""Reports (JSON + Markdown) stored outside the analysed repository; guarded export into repositories."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from config.settings import Settings
from verification.safety_policy import SecurityError, require_approval, resolve_inside

_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def render_markdown(result: dict, title: str = "CodeMind OS report") -> str:
    v = result.get("verification", {})
    L = [f"# {title}", "", f"- Repository: `{result.get('repository_id')}`", f"- Indexed revision: `{result.get('revision')}`",
         f"- Request: {result.get('request')}", f"- Status: **{result.get('status')}**",
         f"- Engine: {result.get('engine')}; AI used: {result.get('ai', {}).get('used')}"
         + (f" (model `{result['ai']['model']}`)" if result.get("ai", {}).get("model") else ""),
         f"- Generated: {result.get('generated_at')}", ""]
    if result.get("ai", {}).get("error"):
        L += [f"> AI interpretation unavailable: {result['ai']['error']}", ""]
    if result.get("stale_index"):
        L += ["> Warning: the repository changed after indexing; results describe the indexed revision.", ""]
    for s in result.get("sections", []):
        L += [f"## {s['task'].title()}: {s['question']}", "", s.get("summary", ""), ""]
        pl = s.get("payload", {})
        if pl.get("markdown"):
            L += [pl["markdown"], ""]
        for kind, head in (("fact", "Verified repository facts (citation-checked)"), ("interpretation", "Interpretations / unverified hypotheses"),
                           ("recommendation", "Recommendations")):
            cs = [c for c in s.get("claims", []) if c["kind"] == kind]
            if cs and not (pl.get("markdown") and kind in {"fact", "recommendation"}):
                L += [f"### {head}"]
                for c in cs:
                    L.append(f"- {c['text']} _[{c['status']}; evidence: {', '.join(c['evidence_ids']) or 'none'}]_")
                L.append("")
        if pl.get("findings"):
            L += ["### Findings", "| ID | Severity | Category | Location | Title | Uncertainty |", "|---|---|---|---|---|---|"]
            for f in pl["findings"]:
                L.append(f"| {f['id']} | {f['severity']} | {f['category']} | `{f['path']}:{f['line']}` | {f['title']} | {f['uncertainty']} |")
            L.append("")
        if s.get("notes"):
            L += ["### Notes and limitations"] + [f"- {n}" for n in dict.fromkeys(s["notes"])] + [""]
    if result.get("evidence"):
        L += ["## Evidence", "| ID | Location | Symbol | Methods |", "|---|---|---|---|"]
        for e in result["evidence"]:
            L.append(f"| {e['id']} | `{e['path']}:{e['start_line']}-{e['end_line']}` | {e.get('symbol', '')} | {', '.join(e['methods'])} |")
        L.append("")
    if v:
        L += ["## Verification", f"- Claims: {v.get('total')}; citation-valid: {v.get('citation_valid')}; unverified: {v.get('unverified')}; "
              f"removed: {v.get('removed')}; citation coverage: {v.get('citation_coverage')}", f"- {v.get('note', '')}", ""]
    if result.get("errors"):
        L += ["## Errors"] + [f"- {e}" for e in result["errors"]] + [""]
    return "\n".join(L)


class ReportService:
    def __init__(self, settings: Settings):
        self.s = settings

    def _dir(self, repo_id: str) -> Path:
        d = self.s.reports_dir / repo_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, repo_id: str, kind: str, result: dict, markdown: str | None = None) -> dict:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        rid = f"{ts}_{kind}"
        d = self._dir(repo_id)
        (d / f"{rid}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        (d / f"{rid}.md").write_text(markdown or render_markdown(result, f"CodeMind OS {kind} report"), encoding="utf-8")
        return {"report_id": rid, "repository_id": repo_id, "kind": kind, "path_json": str(d / f"{rid}.json"),
                "path_markdown": str(d / f"{rid}.md")}

    def list(self, repo_id: str | None = None) -> list[dict]:
        out = []
        base = self.s.reports_dir
        for jf in sorted(base.glob("*/*.json"), reverse=True):
            if repo_id and jf.parent.name != repo_id:
                continue
            out.append({"report_id": jf.stem, "repository_id": jf.parent.name, "kind": jf.stem.split("_", 1)[-1],
                        "created": jf.stem.split("_")[0], "size_bytes": jf.stat().st_size})
        return out

    def read(self, repo_id: str, report_id: str, fmt: str = "markdown") -> str:
        if not (_ID.match(repo_id) and _ID.match(report_id)) or fmt not in {"markdown", "json"}:
            raise SecurityError("Invalid report identifier.")
        p = self.s.reports_dir / repo_id / (report_id + (".md" if fmt == "markdown" else ".json"))
        if not p.is_file():
            raise FileNotFoundError("Report not found.")
        return p.read_text(encoding="utf-8")

    def delete(self, repo_id: str, report_id: str) -> bool:
        if not (_ID.match(repo_id) and _ID.match(report_id)):
            raise SecurityError("Invalid report identifier.")
        found = False
        for ext in (".md", ".json"):
            p = self.s.reports_dir / repo_id / (report_id + ext)
            if p.is_file():
                p.unlink()
                found = True
        return found

    def export_test_proposals(self, repo_id: str, markdown: str, data: dict) -> dict:
        """Writes to the review directory (outside the repository). Never overwrites."""
        d = self.s.review_dir / repo_id
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        md, js = d / f"test_plan_{ts}.md", d / f"test_plan_{ts}.json"
        md.write_text(markdown, encoding="utf-8")
        js.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return {"markdown": str(md), "json": str(js)}

    def write_into_repository(self, repo_root: Path, rel_path: str, content: str, approved: bool) -> str:
        require_approval("write a generated file into the repository", approved, self.s.allow_repo_writes, "CODEMIND_ALLOW_REPO_WRITES")
        target = resolve_inside(repo_root, rel_path)
        if target.exists():
            raise SecurityError("Refusing to overwrite an existing file.")
        if len(content.encode("utf-8")) > 2_000_000:
            raise SecurityError("Content too large.")
        target.parent.mkdir(parents=True, exist_ok=True)
        resolve_inside(repo_root, rel_path)  # re-check after mkdir (symlink races)
        with open(target, "x", encoding="utf-8") as fh:
            fh.write(content)
        return str(target)
