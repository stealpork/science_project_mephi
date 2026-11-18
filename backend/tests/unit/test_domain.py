import io
from itertools import combinations

import pytest
from PIL import Image
from pypdf import PdfWriter

from services.media.validate import validate_image, validate_pdf
from services.projects.domain import WEIGHTS, recommendation

pytestmark = pytest.mark.unit


def test_no_criteria_is_not_zero_match():
    assert recommendation({}, {})["score"] is None


@pytest.mark.parametrize("skill_count", range(5))
def test_skills_matching(skill_count):
    result = recommendation(
        {"required_skill_ids": list("abcd")},
        {"skills": [{"id": value} for value in "abcd"[:skill_count]]},
    )
    assert result["score"] == skill_count * 25
    assert result["components"][0]["effective_weight"] == 1


@pytest.mark.parametrize(
    "criteria", [list(group) for size in range(1, 6) for group in combinations(WEIGHTS, size)]
)
def test_all_criterion_subsets_normalize_to_100(criteria):
    values = {
        "interests": ("interest_ids", ["i"]),
        "skills": ("required_skill_ids", ["s"]),
        "specialty": ("preferred_specialty_ids", ["sp"]),
        "experience": ("min_completed_projects", 1),
        "requirements": ("eligible_courses", [2]),
    }
    project = dict(values[name] for name in criteria)
    profile = {
        "interests": [{"id": "i"}],
        "skills": [{"id": "s"}],
        "specialty_id": "sp",
        "course": 2,
        "experience": [{"id": "exp", "completed": True}],
    }
    result = recommendation(project, profile)
    assert result["score"] == 100
    assert sum(item["effective_weight"] for item in result["components"]) == pytest.approx(1)
    assert recommendation(project, {})["score"] == 0


def test_experience_deduplicates_and_excludes_incomplete():
    result = recommendation(
        {"min_completed_projects": 4},
        {
            "experience": [
                {"project_id": "same", "completed": True},
                {"project_id": "same", "completed": True},
                {"id": "unfinished"},
            ]
        },
    )
    assert result["score"] == 25


def test_pdf_accepts_valid_document():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    assert validate_pdf(buffer.getvalue()) == buffer.getvalue()


def test_pdf_rejects_javascript():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with pytest.warns(DeprecationWarning, match="add_js is deprecated"):
        writer.add_js("app.alert('blocked')")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ValueError, match="UNSAFE_PDF"):
        validate_pdf(buffer.getvalue())


@pytest.mark.parametrize("payload", [b"", b"<html>not a PDF</html>", b"MZexecutable"])
def test_pdf_rejects_fake_type(payload):
    with pytest.raises(ValueError, match="INVALID_PDF"):
        validate_pdf(payload)


def test_avatar_reencodes_and_reduces_size():
    source = io.BytesIO()
    Image.new("RGB", (1800, 1400)).save(source, "JPEG", comment=b"private metadata")
    result = validate_image(source.getvalue())
    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "PNG"
        assert max(image.size) <= 1200
        assert "comment" not in image.info
