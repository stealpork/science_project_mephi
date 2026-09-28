from decimal import ROUND_HALF_UP, Decimal
from typing import TypedDict


class Component(TypedDict):
    criterion: str
    base_weight: float
    effective_weight: float
    match: float
    contribution: float


WEIGHTS = {
    "interests": 0.4,
    "skills": 0.3,
    "specialty": 0.1,
    "experience": 0.1,
    "requirements": 0.1,
}
ROLES = {"leader", "researcher", "analyst", "developer", "designer", "author", "editor"}


def recommendation(project: dict, profile: dict) -> dict:
    skill_ids = {s["id"] for s in profile.get("skills", [])}
    interest_ids = {s["id"] for s in profile.get("interests", [])}
    matches: dict[str, float] = {}
    required_skills = set(project.get("required_skill_ids") or [])
    interests = set(project.get("interest_ids") or [])
    specialties = set(project.get("preferred_specialty_ids") or [])
    if interests:
        matches["interests"] = len(interests & interest_ids) / len(interests)
    if required_skills:
        matches["skills"] = len(required_skills & skill_ids) / len(required_skills)
    if specialties:
        matches["specialty"] = float(profile.get("specialty_id") in specialties)
    minimum = project.get("min_completed_projects")
    if minimum and minimum > 0:
        experience = profile.get("experience", [])
        completed = len(
            {
                e.get("project_id") or e.get("id") or e.get("title")
                for e in experience
                if e.get("completed")
            }
        )
        matches["experience"] = min(1.0, completed / minimum)
    courses = project.get("eligible_courses") or []
    if courses:
        matches["requirements"] = float(profile.get("course") in courses)
    denominator = sum(WEIGHTS[key] for key in matches)
    components: list[Component] = (
        [
            {
                "criterion": key,
                "base_weight": WEIGHTS[key],
                "effective_weight": WEIGHTS[key] / denominator,
                "match": match,
                "contribution": 100 * WEIGHTS[key] / denominator * match,
            }
            for key, match in matches.items()
        ]
        if denominator
        else []
    )
    score = (
        int(
            Decimal(str(sum(c["contribution"] for c in components))).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if components
        else None
    )
    reasons = []
    if matches.get("interests", 0) > 0:
        reasons.append("Совпадают научные интересы")
    if required_skills:
        reasons.append(
            f"Есть {len(required_skills & skill_ids)} из {len(required_skills)} требуемых навыков"
        )
    if matches.get("experience", 0) > 0:
        reasons.append("Подходит указанный исследовательский опыт")
    return {
        "score": score,
        "components": components,
        "matched_skill_ids": sorted(required_skills & skill_ids),
        "missing_skill_ids": sorted(required_skills - skill_ids),
        "reasons": reasons,
        "score_unavailable_reason": None if components else "no_project_criteria",
        "algorithm_version": "weighted-normalized-v1",
    }
