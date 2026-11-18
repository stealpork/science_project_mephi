import importlib
import json

import pytest
from pydantic import ValidationError

from libs.platform import api
from libs.platform.config import Settings

pytestmark = pytest.mark.unit

FIELDS = {
    "people": (
        "ProfilePatch",
        ["first_name", "last_name", "bio", "contacts", "experience", "interest_ids", "skill_ids"],
    ),
    "projects": (
        "ProjectPatch",
        [
            "title",
            "summary",
            "description",
            "research_goal",
            "interest_ids",
            "required_skill_ids",
            "required_roles",
            "preferred_specialty_ids",
            "eligible_courses",
            "team_size",
            "access_type",
            "results",
        ],
    ),
    "publications": ("PaperPatch", ["title", "abstract", "keywords", "external_links"]),
}


@pytest.mark.parametrize(
    "service,field",
    [(service, field) for service, (_, fields) in FIELDS.items() for field in fields],
)
def test_patch_null_is_not_a_database_constraint_error(monkeypatch, service, field):
    monkeypatch.setattr(api, "get_settings", lambda: Settings(service_name=service))
    model = getattr(importlib.import_module(f"services.{service}.main"), FIELDS[service][0])
    assert model(version=1).version == 1
    schema = model.model_json_schema()
    assert '"type": "null"' not in json.dumps(schema["properties"][field])
    assert field not in schema.get("required", [])
    with pytest.raises(ValidationError, match="cannot be null"):
        model.model_validate({"version": 1, field: None})


@pytest.mark.parametrize(
    "service,field",
    [("people", "university_id"), ("projects", "deadline"), ("publications", "project_id")],
)
def test_nullable_relationships_can_be_explicitly_cleared(monkeypatch, service, field):
    monkeypatch.setattr(api, "get_settings", lambda: Settings(service_name=service))
    model = getattr(importlib.import_module(f"services.{service}.main"), FIELDS[service][0])
    value = model.model_validate({"version": 1, field: None})
    assert value.model_dump(exclude_unset=True)[field] is None
