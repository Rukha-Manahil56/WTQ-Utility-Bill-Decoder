# WTQ 2026 — Utility Bill Decoder

## Requirements
Python 3.11 or later; internet access; Gemini API key for one of the event's permitted Gemini models.

## Setup
1. Place `main.py`, `validate.py`, `requirements.txt` and `.env.example` in `source/`.
2. From the project root, install: `python -m pip install -r source/requirements.txt`.
3. Put a local `.env` file in the project root (never submit it):
   `MODEL_PROVIDER=google`, `MODEL_NAME=gemini-3.1-flash-lite`, `API_KEY=<your secret key>`.
4. Place official test bill images in `test/bills/` and original test templates in `test/csv/level1.csv` and `test/csv/level2.csv`.

## Run
From the project root:
`python source/main.py --test test --output output`
`python source/validate.py`

The program writes `output/level1.csv` and `output/level2.csv`. It saves progress and resumes after interruption. It does not modify official templates. If changing code or prompts and wanting to regenerate results, first back up and remove the previous generated output files.

## Design
Gemini reads bill images and generates JSON for extraction and question answering. Python enforces the required extraction schema, CSV column order, output serialization, retries, and basic output validation. Missing or unreadable bill fields are represented by JSON null. Answers use bill images, including history tables. No personal identifiers should be output. Model calculations and image interpretation should be manually spot-checked; passing the validator does not guarantee accuracy.

## AI usage disclosure
- Runtime LLM: Google Gemini 3.1 Flash-Lite (`gemini-3.1-flash-lite`), via Google Gen AI API. If you change the model, update this disclosure.
- Coding assistant: ChatGPT (OpenAI), used to develop and review Python code, prompts, and documentation. ChatGPT is not called by the runtime pipeline.
- Other AI tools: None
- Other APIs/services: Google Gen AI API. No Azure extraction services.

## Security
Never include `.env`, API keys, raw bill images, or other secrets in the submission ZIP. Include `.env.example` with placeholders only.
