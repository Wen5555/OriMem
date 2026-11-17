"""Heading/code-fence aware spans; exact character and line provenance."""

import re, json, unicodedata
import yaml


def metadata(text):
    props = {}
    offset = 0
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            try:
                props = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                props = {}
            offset = end + 4
    return props if isinstance(props, dict) else {}, offset


def lexical(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    parts = re.findall(r"[a-z0-9_]+(?:[-.][a-z0-9_]+)*|[\u3400-\u9fff]+", text)
    result = []
    for part in parts:
        if re.match(r"[\u3400-\u9fff]", part):
            result.extend(part)
            result.extend(part[i : i + 2] for i in range(len(part) - 1))
        else:
            result.append(part)
    return " ".join(result)


def chunks(text, tokenizer, size=384, overlap=48):
    props, start = metadata(text)
    stack = []
    fence = None
    offset = 0
    segments = []
    heading = "正文"
    for line in text.splitlines(keepends=True):
        mark = re.match(r"^\s*(`{3,}|~{3,})", line)
        if mark:
            token = mark[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None and offset >= start:
            m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.rstrip())
            if m:
                if offset > start:
                    segments.append((start, offset, heading))
                level = len(m[1])
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, m[2]))
                heading = " / ".join(x[1] for x in stack)
                start = offset + len(line)
        offset += len(line)
    if start < len(text):
        segments.append((start, len(text), heading))
    output = []
    for first, last, heading in segments:
        body = text[first:last]
        if not body.strip():
            continue
        positions = tokenizer(
            body, add_special_tokens=False, return_offsets_mapping=True, verbose=False
        )["offset_mapping"]
        cursor = 0
        while cursor < len(positions):
            end = min(cursor + size, len(positions))
            a = first + positions[cursor][0]
            b = first + positions[end - 1][1]
            value = text[a:b]
            if value.strip():
                output.append(
                    dict(
                        heading=heading,
                        text=value,
                        start=a,
                        end=b,
                        line_start=text.count("\n", 0, a) + 1,
                        line_end=text.count("\n", 0, b) + 1,
                    )
                )
            if end == len(positions):
                break
            cursor = end - overlap
    return props, output


def links(text, props):
    result = set()
    # Frozen metadata, examples and code are source text, not navigational
    # graph edges. Use the same fenced-block convention as chunk headings.
    visible = []
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            visible.append(line)
    for link in re.findall(r"!?\[\[([^\]]+)\]\]", "".join(visible)):
        target = link.split("|")[0].split("#")[0].strip()
        if target:
            result.add((target, "wikilink"))
    for key in ["project", "course", "related"]:
        values = props.get(key, [])
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            for v in values:
                if isinstance(v, str):
                    result.add((v.strip("[]"), "property:" + key))
    return sorted(result)
