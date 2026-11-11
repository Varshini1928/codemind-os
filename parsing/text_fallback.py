"""Safe text-based fallback. Everything produced here is labelled heuristic / partially parsed."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from parsing.entity_extractor import Entity, ParseResult, Relation, content_hash, make_entity_id
from parsing.languages import CODE_LANGS, detect_language

_METHOD = "regex-heuristic"

_DEFS: dict[str, list[tuple[str, re.Pattern]]] = {
    "python": [("Function", re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>\w+)")),
               ("Class", re.compile(r"^\s*class\s+(?P<name>\w+)"))],
    "javascript": [("Function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[\w$]+)")),
                   ("Function", re.compile(r"^\s*(?:export\s+)?const\s+(?P<name>[\w$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>")),
                   ("Class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>[\w$]+)"))],
    "java": [("Class", re.compile(r"^\s*(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+|static\s+)*(?:class|interface|enum|record)\s+(?P<name>\w+)")),
             ("Method", re.compile(r"^\s*(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],.? ]+\s+(?P<name>\w+)\s*\([^;]*\)\s*(?:throws [\w., ]+)?\s*\{?\s*$"))],
    "go": [("Function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)\s*\(")),
           ("Class", re.compile(r"^type\s+(?P<name>\w+)\s+(?:struct|interface)\b"))],
    "rust": [("Function", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>\w+)")),
             ("Class", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(?P<name>\w+)"))],
    "c": [("Function", re.compile(r"^[A-Za-z_][\w\s\*]*?\s+\**(?P<name>[A-Za-z_]\w*)\s*\([^;{]*\)\s*\{?\s*$"))],
    "csharp": [("Class", re.compile(r"^\s*(?:public|internal|private|protected)?\s*(?:static\s+|abstract\s+|sealed\s+|partial\s+)*(?:class|interface|struct|enum)\s+(?P<name>\w+)")),
               ("Method", re.compile(r"^\s*(?:public|private|protected|internal)\s+(?:static\s+|async\s+|override\s+|virtual\s+)*[\w<>\[\],.?]+\s+(?P<name>\w+)\s*\([^;]*\)\s*\{?\s*$"))],
    "ruby": [("Function", re.compile(r"^\s*def\s+(?:self\.)?(?P<name>\w+[?!]?)")), ("Class", re.compile(r"^\s*(?:class|module)\s+(?P<name>\w+)"))],
    "php": [("Function", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+(?P<name>\w+)")),
            ("Class", re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+(?P<name>\w+)"))],
}
_DEFS["typescript"] = _DEFS["javascript"] + [("Class", re.compile(r"^\s*(?:export\s+)?interface\s+(?P<name>[\w$]+)"))]
_DEFS["cpp"] = _DEFS["c"] + [("Class", re.compile(r"^\s*(?:class|struct)\s+(?P<name>\w+)"))]
_DEFS["kotlin"] = [("Function", re.compile(r"^\s*(?:\w+\s+)*fun\s+(?:<[^>]+>\s*)?(?:[\w.]+\.)?(?P<name>\w+)\s*\(")),
                   ("Class", re.compile(r"^\s*(?:\w+\s+)*(?:class|interface|object)\s+(?P<name>\w+)"))]
_DEFS["swift"] = [("Function", re.compile(r"^\s*(?:\w+\s+)*func\s+(?P<name>\w+)")),
                  ("Class", re.compile(r"^\s*(?:\w+\s+)*(?:class|struct|protocol|enum)\s+(?P<name>\w+)"))]

_IMPORTS: dict[str, list[re.Pattern]] = {
    "python": [re.compile(r"^\s*import\s+(?P<mod>[\w.]+)"), re.compile(r"^\s*from\s+(?P<mod>[\w.]+)\s+import\b")],
    "javascript": [re.compile(r"^\s*import\s+(?:.+?\s+from\s+)?['\"](?P<mod>[^'\"]+)['\"]"),
                   re.compile(r"require\(\s*['\"](?P<mod>[^'\"]+)['\"]\s*\)")],
    "java": [re.compile(r"^\s*import\s+(?:static\s+)?(?P<mod>[\w.]+)\s*;")],
    "go": [re.compile(r"^\s*import\s+(?:\w+\s+)?\"(?P<mod>[^\"]+)\""), re.compile(r"^\s+(?:\w+\s+)?\"(?P<mod>[\w./\-]+)\"\s*$")],
    "rust": [re.compile(r"^\s*use\s+(?P<mod>[\w:]+)")],
    "c": [re.compile(r"^\s*#\s*include\s+[\"<](?P<mod>[^\">]+)[\">]")],
    "csharp": [re.compile(r"^\s*using\s+(?P<mod>[\w.]+)\s*;")],
    "ruby": [re.compile(r"^\s*require(?:_relative)?\s+['\"](?P<mod>[^'\"]+)['\"]")],
    "php": [re.compile(r"^\s*(?:use|require(?:_once)?|include(?:_once)?)\s+['\"]?(?P<mod>[\w\\/.]+)")],
}
_IMPORTS["typescript"] = _IMPORTS["javascript"]
_IMPORTS["cpp"] = _IMPORTS["c"]
_IMPORTS["kotlin"] = _IMPORTS["java"]


def _estimate_end(lines: list[str], i: int, language: str, next_def: int | None) -> int:
    """Best-effort end line (1-based). Brace counting for brace languages, indentation for the rest."""
    n = len(lines)
    if language in {"python", "ruby"}:
        base = len(lines[i]) - len(lines[i].lstrip())
        j = i + 1
        last = i
        while j < n:
            ln = lines[j]
            if ln.strip():
                if len(ln) - len(ln.lstrip()) <= base and not ln.lstrip().startswith((")", "]", "}", "end")):
                    break
                last = j
            j += 1
        return last + 1
    depth, seen = 0, False
    for j in range(i, min(n, i + 800)):
        depth += lines[j].count("{") - lines[j].count("}")
        seen = seen or "{" in lines[j]
        if seen and depth <= 0:
            return j + 1
        if not seen and lines[j].rstrip().endswith(";"):
            return j + 1
    return (next_def - 1) if next_def else min(n, i + 1)


def parse_generic(path: str, text: str, language: str | None = None) -> ParseResult:
    language = language or detect_language(path)
    lines = text.splitlines()
    n = len(lines)
    mod_qn = PurePosixPath(path).with_suffix("").as_posix().replace("/", ".")
    mod = Entity(make_entity_id(path, mod_qn, "Module"), "Module", PurePosixPath(path).stem, mod_qn, path, 1,
                 max(n, 1), language, content_hash(text), "file-level", "exact", None, {})
    res = ParseResult(path, language, "text-only", "text_only", mod_qn, [mod], [], {}, [], n)
    if language in CODE_LANGS and language in _DEFS:
        found: list[tuple[int, str, str]] = []
        for i, ln in enumerate(lines):
            if len(ln) > 400:
                continue
            for etype, rx in _DEFS[language]:
                m = rx.match(ln)
                if m and m.group("name") not in {"if", "for", "while", "switch", "return", "catch"}:
                    found.append((i, etype, m.group("name")))
                    break
        for idx, (i, etype, name) in enumerate(found):
            nxt = found[idx + 1][0] + 1 if idx + 1 < len(found) else None
            end = max(i + 1, _estimate_end(lines, i, language, nxt))
            qn = f"{mod_qn}.{name}"
            e = Entity(make_entity_id(path, f"{qn}@{i + 1}", etype), etype, name, qn, path, i + 1, end, language,
                       content_hash("\n".join(lines[i:end])), _METHOD, "heuristic", mod.id, {})
            res.entities.append(e)
        for i, ln in enumerate(lines, 1):
            for rx in _IMPORTS.get(language, []):
                m = rx.search(ln)
                if m:
                    res.relations.append(Relation(mod.id, "IMPORTS", m.group("mod"), "module", _METHOD, path, i,
                                                  "heuristic", {"names": [], "relative": m.group("mod").startswith(".")}))
                    break
        res.parser, res.status = _METHOD, "partial"
    else:
        parse_config(res, path, text, language)
    return res


_ENV_LINE = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=")
_FILE_TOKEN = re.compile(r"[\w./\-]+\.(?:py|js|ts|sh|jar|go|rb|php)\b")


def parse_config(res: ParseResult, path: str, text: str, language: str) -> None:
    """Config keys from .env-style files and deployment artifacts (Dockerfile, compose, workflows)."""
    name = PurePosixPath(path).name.lower()
    lines = text.splitlines()
    mod = res.entities[0]
    if language == "dotenv":  # only *.example/*.sample/*.template files get here (real secrets files are excluded)
        for i, ln in enumerate(lines, 1):
            m = _ENV_LINE.match(ln)
            if m:
                key = m.group("key")
                res.entities.append(Entity(make_entity_id(path, f"config:{key}", "ConfigKey"), "ConfigKey", key,
                                           f"config:{key}", path, i, i, language, content_hash(ln.split("=")[0]),
                                           "env-file-parse", "exact", mod.id, {"value_stored": False}))
        res.parser = "env-file-parse"
    deploy = (language == "dockerfile" or name.startswith("docker-compose") or name == "procfile"
              or name.endswith(".service") or ".github/workflows/" in path
              or (language == "yaml" and re.search(r"^kind:\s*(Deployment|StatefulSet|Service|CronJob)\b", text, re.M)))
    if deploy:
        art = Entity(make_entity_id(path, f"deploy:{path}", "DeploymentArtifact"), "DeploymentArtifact", name,
                     f"deploy:{path}", path, 1, max(len(lines), 1), language, content_hash(text), "filename-pattern",
                     "heuristic", mod.id, {})
        res.entities.append(art)
        seen = set()
        for i, ln in enumerate(lines, 1):
            for tok in _FILE_TOKEN.findall(ln):
                if tok not in seen:
                    seen.add(tok)
                    res.relations.append(Relation(art.id, "DEPLOYS", tok.lstrip("./"), "path", "text-match", path, i,
                                                  "heuristic"))
        res.parser = "deployment-pattern"
