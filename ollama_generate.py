# -*- coding: utf-8 -*-
"""Generate a tailored resume data/<company>.json using a LOCAL Ollama model
(no Claude/Anthropic calls) and then render it into a PDF via
templates/pdf_template.py.

Generation is split into small, focused calls (overview, then one call per
company) instead of one giant JSON blob — small local models lose track of
a long schema partway through, so this keeps each call short enough to
finish reliably.

Usage:
    python ollama_generate.py <jd_text_file> <Company Name> <Profile Name> [model]
"""

import sys
import os
import re
import json
import requests

# Windows' console defaults to a codepage (e.g. cp1252) that can't encode
# non-ASCII characters (e.g. Cyrillic) - without this, print() raises
# UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen3:1.7b"

# Rendered .docx resumes are saved here rather than inside the project
# folder, so they're easy to find/attach to applications directly.
CVS_DIR = r"C:\Users\webNcodes\Desktop\CVs"




def call_ollama(prompt: str, model: str) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "keep_alive": "10m",
            # Thinking-capable models (e.g. qwen3.5) otherwise spend the whole
            # call writing to the separate "thinking" field and leave
            # "response" empty, which looks like a JSON-parse failure here.
            "think": False,
            "options": {"temperature": 0.3},
        },
        # Ollama's server has occasionally wedged mid-request (accepts the
        # call but never generates - 0% CPU, no response) rather than
        # actually failing fast. A successful call normally finishes in
        # well under a minute even on CPU, so 300s is enough headroom for
        # real slow inference under load while still catching a hang and
        # moving to the next of the 4 retry attempts much sooner than the
        # old 600s did (which could waste up to ~40 minutes total on one
        # stuck piece before giving up).
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("Could not parse JSON from model output:\n" + raw[:1500])


def _normalize_skills(skills):
    """Ollama sometimes flattens [[label, value], ...] into a plain
    [label, value, label, value, ...] list. Detect and reshape that case;
    raise if the structure is something else entirely (triggers a retry)."""
    if all(isinstance(item, (list, tuple)) for item in skills):
        normalized = []
        for item in skills:
            if len(item) >= 2:
                normalized.append([str(item[0]), ", ".join(str(x) for x in item[1:])])
            elif len(item) == 1:
                normalized.append([str(item[0]), ""])
        return normalized
    if all(isinstance(item, str) for item in skills) and len(skills) % 2 == 0:
        return [[skills[i], skills[i + 1]] for i in range(0, len(skills), 2)]
    raise ValueError(f"unrecognized skills structure: {skills!r}")


def generate_piece(prompt: str, model: str, required_keys: list, attempts: int = 4) -> dict:
    prompt_out = prompt.replace("\n", "\n[FILE_ONLY]")
    print(f"[FILE_ONLY]--- PROMPT TO OLLAMA ---\n[FILE_ONLY]{prompt_out}\n[FILE_ONLY]------------------------")
    last_err = None
    for i in range(1, attempts + 1):
        try:
            raw = call_ollama(prompt, model)
            raw_out = raw.replace("\n", "\n[FILE_ONLY]")
            print(f"[FILE_ONLY]--- RESPONSE FROM OLLAMA (Attempt {i}) ---\n[FILE_ONLY]{raw_out}\n[FILE_ONLY]----------------------------------")
            data = extract_json(raw)
            missing = [k for k in required_keys if k not in data]
            if missing:
                raise ValueError(f"missing keys {missing}")
            if "skills" in data:
                data["skills"] = _normalize_skills(data["skills"])
            if "keywords" in data and isinstance(data["keywords"], list):
                data["keywords"] = ", ".join(str(k) for k in data["keywords"])
            return data
        except Exception as e:
            last_err = e
            print(f"    attempt {i} failed: {e}")
    raise RuntimeError(f"Gave up after {attempts} attempts: {last_err}")


def build_summary_prompt(jd_text: str, profile_name: str, experience_summary: str) -> str:
    return f"""You are a resume-tailoring engine. Output ONLY valid JSON, no markdown fences, no commentary.

Analyze the job description below and produce these 2 fields for an {experience_summary} experienced candidate named {profile_name}:

- "subtitle": "<Job Title Matching The JD> | <3-4 key skills from the JD separated by the bullet char •>"
- "summary": a professional summary highlighting skills/tools/value proposition from the JD, professional and easy to read. Do NOT mention any company name in it. This MUST be a SINGLE JSON string value (not a list, not multiple keys) containing EXACTLY 5 sentences of 17-20 words each, written back-to-back in one paragraph.

JOB DESCRIPTION:
\"\"\"
{jd_text}
\"\"\"

Output JSON with exactly these 2 keys: subtitle, summary. Nothing else."""


def build_skills_prompt(jd_text: str, profile_name: str, experience_summary: str) -> str:
    return f"""You are a resume-tailoring engine. Output ONLY valid JSON, no markdown fences, no commentary.

Analyze the job description below and produce these 2 fields for an {experience_summary} experienced candidate named {profile_name}:

- "skills": a JSON array of arrays, 6-9 categories, covering every programming language, framework, database, cloud service, tool, and testing framework mentioned or implied by the JD. Each inner array has EXACTLY 2 strings: [category, comma-separated-items]. Example of the full field: "skills": [["Languages & Frameworks", "JavaScript, TypeScript, React"], ["Cloud & DevOps", "AWS, Docker, CI/CD"]]
- "keywords": one long comma-separated string of 30+ properly-capitalized ATS keywords pulled from the JD (e.g. "React, TypeScript, AWS", not "react, typescript, aws")

JOB DESCRIPTION:
\"\"\"
{jd_text}
\"\"\"

Output JSON with exactly these 2 keys: skills, keywords. Nothing else."""


def build_role_prompt(jd_text: str, spec: dict, avoid_bullets: list, profile_name: str) -> str:
    avoid_block = ""
    if avoid_bullets:
        avoid_list = "\n".join(f"- {b}" for b in avoid_bullets)
        avoid_block = f"""
These bullets were already used for a DIFFERENT company on this resume. Do not repeat them or write near-duplicates — vary the wording, angle, and specific technology emphasized:
{avoid_list}
"""

    intern_note = ""
    if "intern" in spec["seniority"].lower():
        intern_note = 'This is an INTERNSHIP — the title must literally end in "Intern" (e.g. "Frontend Engineering Intern"), and bullets should read as junior/learning-focused, not senior ownership.'

    return f"""You are a resume-tailoring engine. Output ONLY valid JSON, no markdown fences, no commentary.

Write a resume work-experience entry for {profile_name} at a role that is {spec['seniority']}.
The job title must be a variation of the JD's job title below, adjusted for this seniority level. {intern_note}
Write exactly {spec['bullet_count']} bullet points. Each bullet must be 17-20 words, start with a strong action verb, mention a concrete technology from the JD, and where natural include a metric/outcome (e.g. "increased X by Y%"). Simple, professional, easy to read.
{avoid_block}
JOB DESCRIPTION (use its responsibilities/technologies as the basis for these bullets):
\"\"\"
{jd_text}
\"\"\"

Output JSON with exactly these 2 keys:
{{"title": "<job title>", "bullets": ["<bullet 1>", "... exactly {spec['bullet_count']} bullets total ..."]}}"""


def main():
    if len(sys.argv) < 4:
        print("Usage: python ollama_generate.py <jd_text_file> <Company Name> <Profile Name> [model]")
        sys.exit(1)

    jd_path = sys.argv[1]
    company = sys.argv[2]
    profile_name = sys.argv[3]
    model = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_MODEL

    with open(jd_path, encoding="utf-8") as f:
        jd_text = f.read()

    profile_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles", f"{profile_name}.json")
    if not os.path.exists(profile_path):
        print(f"Error: Profile file not found: {profile_path}")
        sys.exit(1)

    with open(profile_path, "r", encoding="utf-8") as f:
        profile_data = json.load(f)

    print(f"Using local Ollama model '{model}' for {profile_name} — generating in pieces (this may take a few minutes on CPU).")

    total_steps = 2 + len(profile_data["FIXED_EXPERIENCE"])

    print(f"  [1/{total_steps}] subtitle + summary...")
    summary_piece = generate_piece(build_summary_prompt(jd_text, profile_name, profile_data.get("experience_summary", "")), model, ["subtitle", "summary"])

    print(f"  [2/{total_steps}] skills + keywords...")
    skills_piece = generate_piece(build_skills_prompt(jd_text, profile_name, profile_data.get("experience_summary", "")), model, ["skills", "keywords"])

    overview = {**summary_piece, **skills_piece}

    experience = []
    all_prior_bullets = []
    for i, spec in enumerate(profile_data["FIXED_EXPERIENCE"], start=3):
        print(f"  [{i}/{total_steps}] {spec['company']} role...")
        role = generate_piece(build_role_prompt(jd_text, spec, all_prior_bullets, profile_name), model, ["title", "bullets"])
        all_prior_bullets.extend(role["bullets"])
        experience.append({
            "title": role["title"],
            "company": spec["company"],
            "location": spec["location"],
            "dates": spec["dates"],
            "bullets": role["bullets"],
        })
        n = len(role["bullets"])
        if n != spec["bullet_count"]:
            print(f"    WARNING: got {n} bullets, expected {spec['bullet_count']}")

    data = {
        "target_company": company,
        "name": profile_name,
        "email": profile_data.get("email", ""),
        "phone": profile_data.get("phone", ""),
        "address": profile_data.get("address", ""),
        "experience_summary": profile_data.get("experience_summary", ""),
        "subtitle": overview["subtitle"],
        "summary": overview["summary"],
        "experience": experience,
        "skills": overview["skills"],
        "education": profile_data["FIXED_EDUCATION"],
        "keywords": overview["keywords"],
    }

    out_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(out_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    data_path = os.path.join(data_dir, f"{company.lower().replace(' ', '_')}_{profile_name.lower().replace(' ', '_')}_ollama.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved tailored content: {data_path}")

    # PDF only (not docx) - rendered straight from the data above, not a
    # docx->PDF conversion (that needs Word/LibreOffice, neither of which
    # is installed here).
    from templates.pdf_template import build as build_pdf
    company_dir = os.path.join(CVS_DIR, company)
    os.makedirs(company_dir, exist_ok=True)
    pdf_path = os.path.join(company_dir, f"{profile_name}.pdf")
    build_pdf(data, pdf_path)
    print(f"Saved resume: {pdf_path}")

    # The JD text file has served its purpose now that the resume is
    # generated - delete it so jd_*.txt files don't pile up indefinitely in
    # this folder. Only reached on success (an exception earlier leaves the
    # file in place, e.g. for retrying/debugging a failed generation).
    try:
        os.remove(jd_path)
    except OSError as e:
        print(f"  Could not delete {jd_path}: {e}")


if __name__ == "__main__":
    main()
