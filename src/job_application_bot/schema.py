"""Pydantic models for structured Gemini responses."""

from pydantic import BaseModel, Field


class JobAnalysis(BaseModel):
    """Structured extraction of a job posting, including a CV match score."""

    is_job_posting: bool = Field(
        description=(
            "True only if the supplied text is a genuine job posting / job "
            "description. False for homepages, articles, product pages, login "
            "walls, or any other non-posting content."
        )
    )
    company: str = Field(description="Name of the hiring company.")
    role: str = Field(description="Job title or role name.")
    technologies: list[str] = Field(
        description="Technologies, frameworks, and tools mentioned in the posting."
    )
    years_required: int = Field(
        description="Minimum years of experience required. Use 0 if not specified."
    )
    match_score: int = Field(
        description=(
            "Integer from 1 to 100 indicating how well the candidate's CV matches "
            "this job. 100 = perfect fit, 1 = no overlap."
        )
    )
    rationale: str = Field(
        description=(
            "A short side-by-side comparison explaining the score, written as three "
            "labelled parts on separate lines: a 'Fits:' part listing exact matches "
            "the CV has for this JD; a 'Bridges:' part listing transferable mappings "
            "where the CV has a different but adjacent skill (format each entry as "
            "'CV skill → value it provides (JD asks: JD skill)', e.g. "
            "'FastAPI → backend REST APIs (JD: Node.js)'); and a 'Missing:' part "
            "listing requirements the CV has no match or bridge for. Write 'none' "
            "for any part that would be empty."
        )
    )
