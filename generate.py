# -*- coding: utf-8 -*-
"""Usage: python generate.py data/<company>.json
Reads a tailored content file and renders it into
CVS_DIR/<Company>/Jimmy Tran.pdf."""

import sys
import json
import os
from templates.pdf_template import build as build_pdf
from ollama_generate import CVS_DIR

if len(sys.argv) != 2:
    print("Usage: python generate.py data/<company>.json")
    sys.exit(1)

data_path = sys.argv[1]
with open(data_path, encoding="utf-8") as f:
    data = json.load(f)

company = data["target_company"]
company_dir = os.path.join(CVS_DIR, company)
os.makedirs(company_dir, exist_ok=True)
output_path = os.path.join(company_dir, "Jimmy Tran.pdf")

build_pdf(data, output_path)

print(f"Saved: {output_path}")
