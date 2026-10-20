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
import sys
if not os.environ.get("JOBBOT_LAUNCHER_AUTH"):
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, "Access Denied: This module must be run from the Job Bot Launcher.", "Security Alert", 0x10)
    sys.exit(1)
import os
import sys
import os
if getattr(sys, 'frozen', False):
    CURRENT_DIR = os.path.dirname(sys.executable)
else:
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
import re
import json
import time
import hashlib
import requests

# Windows' console defaults to a codepage (e.g. cp1252) that can't encode
# non-ASCII characters (e.g. Cyrillic) - without this, print() raises
# UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen3.5:9b"

# Rendered .docx resumes are saved here rather than inside the project
# folder, so they're easy to find/attach to applications directly.
import os as _os
CVS_DIR = _os.getenv("RESUMES_SAVE_PATH", _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "CVs"))
_os.makedirs(CVS_DIR, exist_ok=True)




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
            # num_ctx is pinned (the loaded model already uses 4096) so a
            # different value on another call can't force a model reload or
            # drop Ollama's saved reading of the shared prompt start.
            "options": {"temperature": 0.3, "num_ctx": 4096},
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
    body = resp.json()
    # Ollama reports how long it spent reading the prompt vs writing the
    # answer (nanoseconds). A small read time on calls 2+ means the shared
    # prompt start was reused; a full read every time means it wasn't.
    print(
        f"    [timing] read {body.get('prompt_eval_duration', 0) / 1e9:.1f}s "
        f"({body.get('prompt_eval_count', 0)} tokens) | "
        f"write {body.get('eval_duration', 0) / 1e9:.1f}s "
        f"({body.get('eval_count', 0)} tokens)",
        flush=True,
    )
    return body["response"]


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
    print(f"[FILE_ONLY]--- PROMPT TO OLLAMA ---\n[FILE_ONLY]{prompt_out}\n[FILE_ONLY]------------------------", flush=True)
    last_err = None
    for i in range(1, attempts + 1):
        try:
            raw = call_ollama(prompt, model)
            raw_out = raw.replace("\n", "\n[FILE_ONLY]")
            print(f"[FILE_ONLY]--- RESPONSE FROM OLLAMA (Attempt {i}) ---\n[FILE_ONLY]{raw_out}\n[FILE_ONLY]----------------------------------", flush=True)
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


def shared_block(jd_text: str) -> str:
    """The start of EVERY prompt: identical, character for character, in all 5
    calls (and for every candidate on the same job). Ollama keeps what it just
    read and skips re-reading any prompt start that matches the previous
    request, so the long job description is only read once. Nothing that
    changes between calls (candidate name, seniority, avoid-list...) may go
    in here - it goes in the task part that follows."""
    return f"""You are a resume-tailoring engine. Output ONLY valid JSON, no markdown fences, no commentary.

JOB DESCRIPTION:
\"\"\"
{jd_text}
\"\"\"

"""


def build_summary_prompt(jd_text: str, profile_name: str, experience_summary: str) -> str:
    return shared_block(jd_text) + f"""Analyze the job description above and produce these 2 fields for an {experience_summary} experienced candidate named {profile_name}:

- "subtitle": "<Job Title Matching The JD> | <3-4 key skills from the JD separated by the bullet char •>"
- "summary": a professional summary highlighting skills/tools/value proposition from the JD, professional and easy to read. Do NOT mention any company name in it. This MUST be a SINGLE JSON string value (not a list, not multiple keys) containing EXACTLY 5 sentences of 17-20 words each, written back-to-back in one paragraph.

Output JSON with exactly these 2 keys: subtitle, summary. Nothing else."""


def build_skills_prompt(jd_text: str) -> str:
    # No candidate name / years here on purpose: the answer describes the JOB,
    # so it is the same for every candidate and can be saved and reused (see
    # load_job_skills / save_job_skills).
    return shared_block(jd_text) + """Analyze the job description above and produce these 2 fields:

- "skills": a JSON array of arrays, 6-9 categories, covering every programming language, framework, database, cloud service, tool, and testing framework mentioned or implied by the JD. Each inner array has EXACTLY 2 strings: [category, comma-separated-items]. Example of the full field: "skills": [["Languages & Frameworks", "JavaScript, TypeScript, React"], ["Cloud & DevOps", "AWS, Docker, CI/CD"]]
- "keywords": one long comma-separated string of 30+ properly-capitalized ATS keywords pulled from the JD (e.g. "React, TypeScript, AWS", not "react, typescript, aws")

Output JSON with exactly these 2 keys: skills, keywords. Nothing else."""


# With the JD moved to the front (needed for prompt reuse), the small model wrote
# noticeably shorter bullets (median 14 words instead of 17; 18% inside the
# required 17-20 vs 40%). A reminder line or moving the rules did not help;
# two example bullets of the right length did (33%, median 16). Generic on
# purpose, and the prompt says not to copy them (none were copied in testing).
LENGTH_EXAMPLES = (
    "Led the migration of legacy services to a cloud platform using Java and Docker, cutting deployment time by 40%.",
    "Built RESTful APIs in Python and AWS Lambda that handled high-volume requests reliably, reducing average response time by 35 percent.",
)


def build_role_prompt(jd_text: str, spec: dict, avoid_bullets: list, profile_name: str) -> str:
    avoid_block = ""
    if avoid_bullets:
        avoid_list = "\n".join(f"- {b}" for b in avoid_bullets)
        avoid_block = f"""
These bullets were already used for a DIFFERENT company on this resume. Do not repeat them or write near-duplicates — vary the wording, angle, and specific technology emphasized:
{avoid_list}
"""

    intern_note = ""
    if "intern" in spec.get("seniority", "").lower():
        intern_note = 'This is an INTERNSHIP — the title must literally end in "Intern" (e.g. "Frontend Engineering Intern"), and bullets should read as junior/learning-focused, not senior ownership.'

    # Order matters: the avoid-list is different for every role, so it goes
    # last (just before the output line) to keep it from breaking the shared
    # start of the prompt.
    return shared_block(jd_text) + f"""Write a resume work-experience entry for {profile_name} at a role that is {spec.get('seniority', 'senior')}.
The job title must be a variation of the JD's job title above, adjusted for this seniority level. {intern_note}
Write exactly {spec.get('bullet_count', 5)} bullet points. Each bullet must be 17-20 words, start with a strong action verb, mention a concrete technology from the JD, and where natural include a metric/outcome (e.g. "increased X by Y%"). Simple, professional, easy to read. Examples of the required length (19 and 20 words, do NOT copy them): "{LENGTH_EXAMPLES[0]}" "{LENGTH_EXAMPLES[1]}" Use the job description's responsibilities/technologies as the basis for these bullets.
{avoid_block}
Output JSON with exactly these 2 keys:
{{"title": "<job title>", "bullets": ["<bullet 1>", "... exactly {spec.get('bullet_count', 5)} bullets total ..."]}}"""


def trim_jd(text, limit=6000):
    """Keep prompts short for the small local model: cut at a line break near the limit."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    brk = cut.rfind("\n")
    return (cut[:brk] if brk > limit * 0.6 else cut).rstrip()


# --- Skills + keywords are saved once per JOB ---------------------------------
# They describe the job, not the candidate, so the 2nd/3rd candidate for the same
# job reuses the file instead of asking the model again. The launcher starts a
# new process per (job, candidate), so this has to be a file, not memory.
# Bump PROMPT_VERSION whenever build_skills_prompt() changes, so old files stop
# matching.
PROMPT_VERSION = "skills-v1"
JOB_CACHE_MAX_AGE_DAYS = 30


def _job_cache_dir() -> str:
    return os.path.join(CURRENT_DIR, "data", "job_cache")


def _job_cache_path(jd_text: str, model: str) -> str:
    key = hashlib.sha1(f"{PROMPT_VERSION}\0{model}\0{jd_text}".encode("utf-8")).hexdigest()[:20]
    return os.path.join(_job_cache_dir(), f"{key}.json")


def prune_job_cache() -> None:
    """Delete saved skills older than JOB_CACHE_MAX_AGE_DAYS (also stray temp files)."""
    cutoff = time.time() - JOB_CACHE_MAX_AGE_DAYS * 86400
    try:
        for name in os.listdir(_job_cache_dir()):
            path = os.path.join(_job_cache_dir(), name)
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
    except OSError:
        pass


def load_job_skills(jd_text: str, model: str):
    """Saved {"skills", "keywords"} for this job, or None. A missing, unreadable
    or malformed file just counts as "not saved" - never an error."""
    try:
        with open(_job_cache_path(jd_text, model), encoding="utf-8") as f:
            piece = json.load(f)
        skills = _normalize_skills(piece["skills"])
        keywords = piece["keywords"]
        if skills and isinstance(keywords, str) and keywords.strip():
            return {"skills": skills, "keywords": keywords}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def save_job_skills(jd_text: str, model: str, piece: dict) -> None:
    """Write via a temp file + os.replace so another panel running at the same
    time can never read a half-written file. Failing to save is not fatal."""
    path = _job_cache_path(jd_text, model)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(_job_cache_dir(), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"skills": piece["skills"], "keywords": piece["keywords"]}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        print(f"    (could not save skills for reuse: {e})", flush=True)
        try:
            os.remove(tmp)
        except OSError:
            pass


# --- Mechanical clean-ups done in code, not by the model ------------------------
_LEGAL_SUFFIX = re.compile(r"[,\s]+(inc|llc|ltd|corp|corporation|co|company|gmbh|plc)\.?$", re.IGNORECASE)


def strip_company_name(text: str, company: str) -> str:
    """The prompt forbids naming the company in the summary but the model often
    does anyway, so replace it with "the company" (keeps the sentence readable).
    Multi-word names match any case; a one-word name must match as written, so
    a company called e.g. "Extend" does not eat the ordinary verb "extend"."""
    names = {company.strip()}
    short = _LEGAL_SUFFIX.sub("", company.strip()).strip(" ,.")
    if short:
        names.add(short)
    for name in sorted((n for n in names if n), key=len, reverse=True):
        flags = re.IGNORECASE if " " in name else 0
        pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?P<poss>['’]s)?(?!\w)", flags)

        def repl(m):
            before = m.string[:m.start()].rstrip()
            word = "The company" if (not before or before.endswith((".", "!", "?"))) else "the company"
            return word + (m.group("poss") or "")

        text = pattern.sub(repl, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\s+([,.;:])", r"\1", text).strip()


def dedupe_csv(value: str) -> str:
    """Drop repeated items from a comma-separated string (case-insensitive, first
    spelling wins). Commas inside brackets, e.g. "AWS (EC2, S3)", are kept."""
    seen, out = set(), []
    for item in re.split(r",\s*(?![^()]*\))", value):
        item = item.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return ", ".join(out)


def main():
    if len(sys.argv) < 4:
        print("Usage: python ollama_generate.py <jd_text_file> <Company Name> <Profile Name> [model]")
        sys.exit(1)

    jd_path = sys.argv[1]
    company = sys.argv[2]
    profile_name = sys.argv[3]
    model = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_MODEL

    with open(jd_path, encoding="utf-8") as f:
        jd_text = trim_jd(f.read())

    prune_job_cache()

    import re
    def slugify(s):
        s = s.lower().strip()
        s = re.sub(r'[^a-z0-9]+', '-', s)
        return s.strip('-')
    profile_path = os.path.join(CURRENT_DIR, "profiles", f"{profile_name}.json")
    if not os.path.exists(profile_path):
        print(f"Error: Profile file not found: {profile_path}")
        sys.exit(1)

    with open(profile_path, "r", encoding="utf-8") as f:
        profile_data = json.load(f)

    print(f"Using local Ollama model '{model}' for {profile_name} — generating in pieces (this may take 3-5 minutes on CPU. Please be patient!)...", flush=True)

    total_steps = 2 + len(profile_data.get("FIXED_EXPERIENCE", profile_data.get("work_experience", [])))

    print(f"  [1/{total_steps}] Generating subtitle + summary (Running inference, please wait...)", flush=True)
    summary_piece = generate_piece(build_summary_prompt(jd_text, profile_name, profile_data.get("experience_summary", "")), model, ["subtitle", "summary"])

    skills_piece = load_job_skills(jd_text, model)
    if skills_piece:
        print(f"  [2/{total_steps}] Using the skills + keywords already saved for this job (no model call)", flush=True)
    else:
        print(f"  [2/{total_steps}] Generating skills + keywords (Running inference, please wait...)", flush=True)
        skills_piece = generate_piece(build_skills_prompt(jd_text), model, ["skills", "keywords"])
        save_job_skills(jd_text, model, skills_piece)

    overview = {**summary_piece, **skills_piece}

    # Code-side clean-ups (the model ignores these rules too often).
    overview["summary"] = strip_company_name(overview["summary"], company)
    overview["skills"] = [[label, dedupe_csv(items)] for label, items in overview["skills"]]
    overview["keywords"] = dedupe_csv(overview["keywords"])

    experience = []
    all_prior_bullets = []
    for i, spec in enumerate(profile_data.get("FIXED_EXPERIENCE", profile_data.get("work_experience", [])), start=3):
        print(f"  [{i}/{total_steps}] Generating {spec['company']} role (Running inference, please wait...)", flush=True)
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
            print(f"    WARNING: got {n} bullets, expected {spec.get('bullet_count', 5)}", flush=True)

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

    out_dir = CURRENT_DIR
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
    os.makedirs(CVS_DIR, exist_ok=True)
    pdf_path = os.path.join(CVS_DIR, f"{profile_name} - {company}.pdf")
    build_pdf(data, pdf_path)
    print(f"Saved resume: {pdf_path}")

    # The JD text file is no longer deleted here; launcher.py handles cleanup.


if __name__ == "__main__":
    main()
