<!--
================================================================================
cv.example.md — committed template for your CV
================================================================================

HOW TO USE THIS FILE
  1. Copy it to `cv.md` in the project root:
       - PowerShell:  Copy-Item cv.example.md cv.md
       - bash/macOS:  cp cv.example.md cv.md
  2. Replace every placeholder below with your real details, then delete the
     example text (the lines starting with "e.g.") so only your content remains.
  3. You do NOT need to keep these HTML comments in cv.md — they are just guidance.

WHY TWO FILES
  `cv.md` holds your personal data and is gitignored (see .gitignore), so it is
  never committed. `cv.example.md` is the safe, shareable template that lives in
  the repo so anyone cloning the project knows what `cv.md` should contain.

HOW THE PIPELINE USES IT
  `cv.md` is the single source of truth for BOTH:
    - scoring: brain.analyze() compares each job posting against this profile to
      produce the 1–100 match score, and
    - cover letters: brain.write_cover_letter() draws your strengths and voice
      from here.
  The richer and more specific this file is, the better the score and the letter.
  Keep it as plain text/markdown — no PDF, no images.
================================================================================
-->

# Contact

<!-- Your name and contact details. The name (the level-1 heading directly below)
     is what the cover letter uses in its sign-off, so make it your real full
     name — without it the model will guess and get it wrong. The rest are
     standard contact fields; fill in what applies and delete the lines that
     don't. GitHub/portfolio links double as "proof of work" the scorer credits. -->
e.g.
# Jane Doe

- Email: jane.doe@example.com
- Phone: +972 50 000 0000
- GitHub: github.com/janedoe
- LinkedIn: linkedin.com/in/janedoe
- Portfolio: janedoe.dev

# Summary

<!-- One or two sentences: your title, years of experience, and top-level focus. -->
e.g. AI engineer with 5 years building LLM-powered products, specialising in RAG
pipelines and applied NLP. Comfortable owning a feature from prototype to
production.

# About Me

<!-- Your voice and personality — this section is the primary source for the
     cover letter's TONE. Write in first person, as you would open a cover letter:
     what drives you, what you care about, the problems you love solving, and how
     you prefer to work. Two to four sentences is plenty. -->
e.g. I love turning fuzzy product ideas into reliable systems people actually use.
I care about clean interfaces, fast feedback loops, and shipping. I do my best
work in small, high-trust teams that move quickly and review each other's code.

# Skills

<!-- A grouped list of technologies, tools, frameworks, and methodologies. Be
     specific: name models, libraries, cloud providers, and DevOps tools. The
     scorer rewards exact matches to a job's requirements, and gives partial
     credit for adjacent skills (e.g. FastAPI when a job asks for another backend
     framework), so list what you genuinely know. -->
e.g.
- Languages: Python, TypeScript, SQL
- AI/ML: Gemini, OpenAI APIs, LangChain, RAG, prompt engineering, fine-tuning
- Backend: FastAPI, async Python, REST APIs, PostgreSQL, SQLAlchemy
- Infra/DevOps: GCP, Docker, Docker Compose, GitHub Actions (CI/CD)

# Projects

<!-- Two to five projects most relevant to the roles you target. For each: name,
     one-line description, key technologies, and a measurable outcome if possible.
     Add a link (GitHub/portfolio) where you can — the scorer credits proof of work. -->
e.g.
**Job-Application Bot** — automated pipeline that scores job postings against a CV
and drafts tailored cover letters. Stack: Python, LLM, Airtable, Telegram.
Cut manual screening time by ~80%. https://github.com/you/job-application-bot

**Support RAG Assistant** — retrieval-augmented chatbot over internal docs.
Stack: Python, FastAPI, pgvector, GCP. Reduced support tickets by 35%.

# Publications

<!-- Papers, blog posts, talks, or notable open-source contributions worth citing.
     Include title, venue/platform, and year. Delete this section if not applicable. -->
e.g. "Cheap and Fast RAG Evaluation" — personal blog, 2025.

# Experience

<!-- Reverse-chronological work history. For each role: company, title, dates, and
     two to four bullet points on IMPACT. Quantify where possible (team size,
     latency, revenue, adoption). This section grounds the cover letter — only
     claims that appear here will be used. -->
e.g.
**Acme Corp** — Senior AI Engineer (Jan 2023 – present)
- Built a RAG system that cut support ticket volume by 35%.
- Led three engineers shipping a real-time recommendation API at 50 ms p99.
- Set up CI/CD (GitHub Actions) and Dockerised deploys to GCP.

**Beta Labs** — Software Engineer (2020 – 2022)
- Owned the billing service (Python, PostgreSQL) handling ~$2M/month.

# Education

<!-- Degree(s), institution, graduation year. Add relevant coursework or thesis if
     it strengthens your profile. -->
e.g.
**B.Sc. Computer Science** — Tel Aviv University, 2019.
Thesis: "Efficient approximate nearest-neighbour search for dense embeddings."
