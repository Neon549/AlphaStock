"""Versioned Skill Registry with permission-gated, LLM-assisted selection."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Iterable


SKILLS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    version: str
    trigger: dict[str, Any]
    permissions: tuple[str, ...]
    entrypoint: str | None
    root: Path
    content_hash: str

    @property
    def version_id(self) -> str:
        return f"{self.name}@{self.version}+{self.content_hash[:10]}"


class SkillRegistry:
    """Discovers skills from ``skills/*/skill.json`` and executes only authorised ones."""

    def __init__(self, skills_dir: Path = SKILLS_DIR):
        self.skills_dir = skills_dir
        self._skills: dict[str, SkillManifest] = {}
        self.reload()

    def reload(self) -> None:
        skills: dict[str, SkillManifest] = {}
        for manifest_path in self.skills_dir.glob("*/skill.json"):
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            required = {"name", "description", "version", "trigger", "permissions"}
            missing = required - raw.keys()
            if missing:
                raise ValueError(f"Invalid skill manifest {manifest_path}: missing {sorted(missing)}")
            root = manifest_path.parent
            fingerprint = hashlib.sha256()
            fingerprint.update(manifest_path.read_bytes())
            # ``version_files`` may include executable handlers in addition to prompts.
            # Older manifests can keep using prompt_files as their complete version surface.
            version_files = raw.get("version_files", raw.get("prompt_files", []))
            for relative in version_files:
                file_path = root / relative
                if not file_path.exists():
                    raise ValueError(f"Skill prompt file does not exist: {file_path}")
                fingerprint.update(file_path.read_bytes())
            skill = SkillManifest(
                name=raw["name"],
                description=raw["description"],
                version=raw["version"],
                trigger=raw["trigger"],
                permissions=tuple(raw["permissions"]),
                entrypoint=raw.get("entrypoint"),
                root=root,
                content_hash=fingerprint.hexdigest(),
            )
            if skill.name in skills:
                raise ValueError(f"Duplicate skill name: {skill.name}")
            skills[skill.name] = skill
        self._skills = skills

    def get(self, name: str) -> SkillManifest:
        return self._skills[name]

    def list_public(self) -> list[dict[str, Any]]:
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "version": skill.version,
                "version_id": skill.version_id,
                "trigger": skill.trigger,
                "permissions": list(skill.permissions),
                "entrypoint": skill.entrypoint,
            }
            for skill in self._skills.values()
        ]

    def _allowed(self, skill: SkillManifest, granted: Iterable[str]) -> bool:
        return set(skill.permissions).issubset(set(granted))

    def _triggered(self, skill: SkillManifest, query: str, context: dict[str, Any]) -> bool:
        trigger = skill.trigger
        if trigger.get("requires_session_document") and not context.get("has_session_document"):
            return False
        keywords = [keyword.lower() for keyword in trigger.get("keywords", [])]
        return not keywords or any(keyword in query.lower() for keyword in keywords)

    def select(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        granted_permissions: Iterable[str] = (),
        llm: Any | None = None,
    ) -> list[SkillManifest]:
        """Return authorised skills; let an LLM choose among triggered candidates when available.

        The LLM can only select names supplied by the registry. Invalid output falls back to
        deterministic triggers, so it never bypasses permission or registry checks.
        """
        context = context or {}
        candidates = [
            skill for skill in self._skills.values()
            if self._allowed(skill, granted_permissions) and self._triggered(skill, query, context)
        ]
        if not candidates or llm is None:
            return candidates

        catalog = "\n".join(f"- {skill.name}: {skill.description}" for skill in candidates)
        prompt = (
            "Choose the skills needed for this request. Return JSON only: "
            '{"skills":["skill-name"]}. Choose only from this catalog; use [] if none.\n'
            f"Catalog:\n{catalog}\nRequest: {query}"
        )
        try:
            raw = llm.invoke(prompt).content.strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            selected_names = json.loads(raw).get("skills", [])
            selected = [skill for skill in candidates if skill.name in selected_names]
            return selected
        except Exception as exc:
            print(f"[SkillRegistry] LLM selection failed; deterministic fallback: {exc}")
            return candidates

    def execute(self, name: str, *, granted_permissions: Iterable[str], **kwargs: Any) -> Any:
        skill = self.get(name)
        if not self._allowed(skill, granted_permissions):
            raise PermissionError(f"Missing permission for skill {name}: {skill.permissions}")
        if not skill.entrypoint:
            raise ValueError(f"Skill {name} has no executable entrypoint")
        module_name, function_name = skill.entrypoint.split(":", 1)
        try:
            module = import_module(module_name)
        except ModuleNotFoundError:
            # Compatibility for manifests written before the runtime was moved
            # under ``agent_runtime``.  This keeps an already deployed registry
            # from breaking while the manifest is refreshed.
            if module_name.startswith("skills."):
                module = import_module(f"agent_runtime.{module_name}")
            else:
                raise
        handler: Callable[..., Any] = getattr(module, function_name)
        return handler(**kwargs)


skill_registry = SkillRegistry()
