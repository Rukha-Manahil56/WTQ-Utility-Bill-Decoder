"""WTQ Build Track: template-preserving utility bill decoder."""
import argparse
import csv
import json
import logging
import os
import random
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

FIELDS = ('provider tariff sanctioned_load_kw bill_month reading_date issue_date due_date previous_reading current_reading units_consumed charges total_charges taxes total_taxes current_bill arrears payable_within_due_date payable_after_due_date').split()
CHARGE_TYPES = {'energy','fixed','fpa','quarterly_adjustment','surcharge','meter_rent','subsidy','other'}
TAX_TYPES = {'gst','electricity_duty','income_tax','municipal_tax','other_tax'}
NUMERIC = {'sanctioned_load_kw','previous_reading','current_reading','units_consumed','total_charges','total_taxes','current_bill','arrears','payable_within_due_date','payable_after_due_date'}
PROMPT_EXTRACT = """You are extracting structured data from ONE Pakistani electricity bill image. Inspect the entire bill carefully, including small print, tables, and rotated text. PAYMENT RULE: If the bill shows multiple after-due-date payment periods, select the amount associated with the latest payment period for payable_after_due_date. For example, if a bill shows Rs. 3,574 for 22–24 April and Rs. 3,716 after 24 April, select Rs. 3,716. Do not select an intermediate late-payment amount. If the bill says NOT TO BE PAID or shows a credit balance, do not report that credit as money owed. Return a single JSON object with EXACTLY these keys: {fields}.

GENERAL:
- provider: KE, LESCO, or IESCO. bill_month YYYY-MM; dates YYYY-MM-DD.
- Use JSON numbers for numerical values, never quoted strings. Use null when a required value is not explicitly readable or unambiguously derivable from a clearly labeled reading pair. Do not fabricate values or silently turn a missing value into zero.
- Never include names, addresses, account/reference/consumer numbers, or meter IDs.
- Use ONLY the CURRENT billing cycle, not historical rows, for main bill fields; historical usage is for question answering.

METER READINGS:
- Locate previous and current readings and units for the current billing cycle. Do not substitute a meter ID, a previous month's history, or a total from an unrelated register.
- If multiple peak/off-peak registers are shown and there is no unambiguous single aggregate previous/current reading, use null for those aggregate reading fields rather than selecting an arbitrary register. Preserve the printed total units_consumed when shown.

CHARGES AND TAXES:
- charges and taxes are lists of objects containing ONLY type and amount.
- Preserve EVERY separately printed, nonzero line item even when types repeat; never combine multiple energy lines into one. Do not double-count a subtotal as a separate line item.
- Allowed charge types: energy, fixed, fpa (including FCA), quarterly_adjustment, surcharge, meter_rent, subsidy, other.
- Allowed tax types: gst (including GST on FPA), electricity_duty, income_tax, municipal_tax, other_tax.
- Determine classification from printed context and section. Put actual tax items in taxes, not charges.
- Credits, rebates, subsidies and CR amounts are NEGATIVE. Do not turn credits into positive payment demands.
- If only a combined tax total is printed with no itemized tax lines, return taxes=[] and retain the printed total_taxes. Do not invent a breakdown.
- Use printed total_charges, total_taxes, and current_bill values where explicitly labeled. Do not substitute a grand total for a component subtotal.

PAYMENTS AND DATES:
- Distinguish the bill's primary due_date, payable_within_due_date and payable_after_due_date from secondary installment schedules, later surcharge dates, and unrelated history.
- For payable_within_due_date, use ONLY the amount explicitly labeled PAYABLE WITHIN DUE DATE / GRAND TOTAL payable on or before the primary due date. Never use a late-payment tier for this field.
- For payable_after_due_date, use the full payable amount for the LATEST printed late-payment period, not an intermediate tier. Never use the surcharge alone.
- Example of interpreting labels (do not copy numbers into other bills): Payable Within Due Date 3535; Till 12-Oct 3674; After 12-Oct 3812 means within=3535 and after=3812.
- If the bill explicitly says NOT TO BE PAID or indicates no amount is currently due because of a credit balance, payable_within_due_date must be 0 if zero is explicitly printed, otherwise null. Never use the positive magnitude of a CR credit as payable. Set arrears negative only if that negative balance is actually shown as arrears; do not conflate total credit balance with arrears.
- Do not interpret the late-payment surcharge as the payable_after_due_date; use the actual printed payable total when available.

Return only the requested JSON object."""
PROMPT_ANSWERS = '''You are explaining an electricity bill. Answer ALL provided questions in English using ONLY the attached bill image and the extracted data. Read every part of the image, especially historical consumption and small tables. For every question, provide specific numbers and relevant breakdowns, compute arithmetic carefully, show basis of averages, percentages and differences, and label estimates with their method. For taxes include tax total, current bill, percentage and tax breakdown if relevant and printed. Explain ambiguities and missing information; never guess or use external tariffs. Never include names, addresses, account numbers, consumer numbers, reference IDs or meter numbers. Output ONLY a JSON object mapping EACH question ID to a nonempty plain-English string. Extracted data: {data}\nQuestions: {questions}'''

def read_csv(path, expected):
    with path.open('r', newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != expected:
            raise ValueError(f'{path}: expected columns {expected}; got {reader.fieldnames}')
        return list(reader)

def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)

def image_for(directory, bill_id):
    for ext in ('.png','.jpg','.jpeg','.PNG','.JPG','.JPEG'):
        candidate = directory / (bill_id + ext)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f'No image for {bill_id} in {directory}')

def image_part(path):
    mime = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)

def call_json(client, model, parts, attempts=5):
    for i in range(attempts):
        try:
            response = client.models.generate_content(
                model=model, contents=parts,
                config=types.GenerateContentConfig(response_mime_type='application/json', temperature=0))
            if not response.text:
                raise ValueError('Empty model response')
            return json.loads(response.text)
        except Exception as exc:
            if i == attempts - 1:
                raise
            delay = min(35, 3 * 2**i) + random.random()
            logging.warning('Request failed (%s), retry in %.1fs', type(exc).__name__, delay)
            time.sleep(delay)

def validate_bill(raw):
    if not isinstance(raw, dict):
        raise ValueError('Extraction not a JSON object')
    obj = {k: raw.get(k) for k in FIELDS}
    if obj['provider'] not in ('KE','LESCO','IESCO'):
        raise ValueError('Invalid provider')
    for k in NUMERIC:
        v = obj[k]
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int,float))):
            raise ValueError(f'{k}: expected number/null, got {v!r}')
    for field, valid_types in (('charges', CHARGE_TYPES), ('taxes', TAX_TYPES)):
        if obj[field] is None:
            obj[field] = []
        if not isinstance(obj[field], list):
            raise ValueError(f'{field} must be array')
        cleaned = []
        for item in obj[field]:
            if not isinstance(item, dict) or item.get('type') not in valid_types:
                raise ValueError(f'Invalid {field} item: {item!r}')
            amount = item.get('amount')
            if isinstance(amount, bool) or not isinstance(amount, (int,float)):
                raise ValueError(f'Invalid {field} amount: {item!r}')
            if amount != 0:
                cleaned.append({'type':item['type'], 'amount':amount})
        obj[field] = cleaned
    return obj

def run(test, output, client, model, refresh_level1=False):
    l1_rows = read_csv(test/'csv'/'level1.csv', ['bill_id','json'])
    l2_rows = read_csv(test/'csv'/'level2.csv', ['bill_id','question_id','question','answer'])
    if len({r['bill_id'] for r in l1_rows}) != len(l1_rows):
        raise ValueError('Duplicate Level 1 bill IDs')
    l1_out = output/'level1.csv'
    previous_l1 = {}
    if l1_out.exists() and not refresh_level1:
        previous_l1 = {r['bill_id']:r['json'] for r in read_csv(l1_out,['bill_id','json']) if r['json'].strip()}
    bill_data = {}
    for n,row in enumerate(l1_rows,1):
        bid = row['bill_id']
        if bid in previous_l1:
            try:
                obj = validate_bill(json.loads(previous_l1[bid]))
            except (ValueError, TypeError):
                obj = None
        else:
            obj = None
        if obj is None:
            logging.info('Level 1 %d/%d: %s',n,len(l1_rows),bid)
            obj = validate_bill(call_json(client,model,[image_part(image_for(test/'bills',bid)), PROMPT_EXTRACT.format(fields=', '.join(FIELDS))]))
        bill_data[bid] = obj
        row['json'] = json.dumps(obj,ensure_ascii=False,separators=(',',':'))
        write_csv(l1_out,['bill_id','json'],l1_rows)
    logging.info('Level 1 complete: %d bills',len(l1_rows))

    l2_out = output/'level2.csv'
    previous_answers = {}
    if l2_out.exists() and not refresh_level1:
        for row in read_csv(l2_out,['bill_id','question_id','question','answer']):
            if row['answer'].strip():
                previous_answers[(row['bill_id'],row['question_id'],row['question'])] = row['answer']
    groups = {}
    for row in l2_rows:
        key = (row['bill_id'],row['question_id'],row['question'])
        row['answer'] = previous_answers.get(key,'')
        groups.setdefault(row['bill_id'],[]).append(row)
    for n,(bid,group) in enumerate(groups.items(),1):
        if bid not in bill_data:
            raise ValueError(f'Level 2 references unknown bill: {bid}')
        pending = [r for r in group if not r['answer'].strip()]
        if not pending:
            continue
        questions = {r['question_id']:r['question'] for r in pending}
        if len(questions) != len(pending):
            raise ValueError(f'Duplicate question ID for {bid}')
        logging.info('Level 2 %d/%d: %s (%d questions)',n,len(groups),bid,len(pending))
        prompt = PROMPT_ANSWERS.format(data=json.dumps(bill_data[bid],ensure_ascii=False),questions=json.dumps(questions,ensure_ascii=False))
        answers = call_json(client,model,[image_part(image_for(test/'bills',bid)),prompt])
        if not isinstance(answers,dict):
            raise ValueError(f'Answers for {bid} not a JSON object')
        for r in pending:
            answer = answers.get(r['question_id'])
            if not isinstance(answer,str) or not answer.strip():
                raise ValueError(f'Missing answer for {bid}/{r["question_id"]}; rerun after fixing')
            r['answer'] = answer.strip()
        write_csv(l2_out,['bill_id','question_id','question','answer'],l2_rows)
        time.sleep(4)
    write_csv(l2_out,['bill_id','question_id','question','answer'],l2_rows)
    logging.info('Level 2 complete: %d answers',len(l2_rows))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='WTQ utility bill decoder')
    parser.add_argument('--test',type=Path,default=Path('test'))
    parser.add_argument('--output',type=Path,default=Path('output'))
    parser.add_argument('--refresh-level1', action='store_true', help='Re-extract all Level 1 bills instead of using cached output')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(levelname)s: %(message)s')
    load_dotenv()
    key = os.getenv('API_KEY')
    model = os.getenv('MODEL_NAME','gemini-3.1-flash-lite')
    if not key:
        parser.error('Missing API_KEY in .env')
    if model not in ('gemini-3.1-flash-lite','gemini-3.5-flash-lite'):
        parser.error('MODEL_NAME must be an allowed Gemini model')
    run(args.test,args.output,genai.Client(api_key=key),model,refresh_level1=args.refresh_level1)
