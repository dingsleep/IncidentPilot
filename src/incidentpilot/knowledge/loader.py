from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, cast

import yaml

from incidentpilot.knowledge.models import (
    RunbookDocument,
    RunbookMetadata,
    RunbookSection,
)

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_SLUG_PART = re.compile(r"[^a-z0-9]+")
_STEP_FIELDS = ("Applies when", "Do not use when", "Action", "Validate", "Rollback")


def load_catalog(path: Path) -> list[RunbookDocument]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("runbook catalog must contain a runbooks list")
    catalog = cast(dict[str, Any], raw)
    entries = catalog.get("runbooks")
    if not isinstance(entries, list):
        raise ValueError("runbook catalog must contain a runbooks list")
    root = path.parent
    documents: list[RunbookDocument] = []
    seen: set[tuple[str, str]] = set()
    for entry in cast(list[Any], entries):
        if not isinstance(entry, dict):
            raise ValueError("catalog entries require only id, version, and path")
        item = cast(dict[str, Any], entry)
        if set(item) != {"id", "version", "path"}:
            raise ValueError("catalog entries require only id, version, and path")
        document = load_runbook(root / str(item["path"]))
        identity = (document.id, document.version)
        if identity != (str(item["id"]), str(item["version"])):
            raise ValueError(f"catalog metadata mismatch for {item['path']}")
        if identity in seen:
            raise ValueError(f"duplicate runbook version: {identity}")
        seen.add(identity)
        documents.append(document)
    return documents


def load_runbook(path: Path) -> RunbookDocument:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ValueError(f"{path}: missing YAML frontmatter")
    raw: Any = yaml.safe_load(match.group(1))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: frontmatter must be an object")
    metadata = RunbookMetadata.model_validate(raw)
    body = text[match.end() :]
    title, sections = _parse_sections(body, metadata, _digest(text))
    return RunbookDocument(
        **metadata.model_dump(),
        path=path.as_posix(),
        title=title,
        content=body.strip(),
        digest=_digest(text),
        sections=sections,
    )


def _parse_sections(
    body: str,
    metadata: RunbookMetadata,
    runbook_digest: str,
) -> tuple[str, list[RunbookSection]]:
    title = ""
    current: tuple[int, str, str | None, list[str]] | None = None
    sections: list[RunbookSection] = []
    parent_title: str | None = None
    parent_slug: str | None = None

    def finish() -> None:
        if current is None:
            return
        level, section_title, section_parent, lines = current
        content = "\n".join(lines).strip()
        if level == 3 and section_parent == "Procedure":
            _validate_step(section_title, content)
        local_slug = _slug(section_title)
        section_id = (
            f"{parent_slug}/{local_slug}" if level == 3 and parent_slug is not None else local_slug
        )
        checksum = _digest(f"{metadata.id}\n{metadata.version}\n{section_id}\n{content}")
        sections.append(
            RunbookSection(
                runbook_id=metadata.id,
                version=metadata.version,
                section_id=section_id,
                title=section_title,
                parent_title=section_parent,
                content=content,
                checksum=checksum,
                runbook_digest=runbook_digest,
                services=metadata.services,
                symptoms=metadata.symptoms,
            )
        )

    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading is None:
            if current is not None:
                current[3].append(line)
            continue
        level = len(heading.group(1))
        heading_title = heading.group(2)
        if level == 1:
            title = heading_title
            continue
        finish()
        if level == 2:
            parent_title = heading_title
            parent_slug = _slug(heading_title)
            current = (level, heading_title, None, [])
        else:
            current = (level, heading_title, parent_title, [])
    finish()
    if not title or not sections:
        raise ValueError("runbook requires one title and at least one section")
    return title, sections


def _validate_step(title: str, content: str) -> None:
    for field in _STEP_FIELDS:
        if re.search(rf"(?im)^-\s*{re.escape(field)}\s*:", content) is None:
            raise ValueError(f"{title}: operational step is missing {field}")


def _slug(value: str) -> str:
    slug = _SLUG_PART.sub("-", value.lower()).strip("-")
    if not slug:
        raise ValueError(f"heading cannot form a section ID: {value}")
    return slug


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
