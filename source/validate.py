import csv
import json
from pathlib import Path
from main import FIELDS, validate_bill, read_csv

def validate(test=Path('test'), output=Path('output')):
    src1=read_csv(test/'csv'/'level1.csv',['bill_id','json'])
    src2=read_csv(test/'csv'/'level2.csv',['bill_id','question_id','question','answer'])
    out1=read_csv(output/'level1.csv',['bill_id','json'])
    out2=read_csv(output/'level2.csv',['bill_id','question_id','question','answer'])
    assert len(src1)==len(out1)==10, 'Level 1 must have 10 rows'
    assert len(src2)==len(out2)==120, 'Level 2 must have 120 rows'
    for a,b in zip(src1,out1):
        assert a['bill_id']==b['bill_id'], 'Level 1 row order changed'
        assert b['json'].strip(), f'Empty JSON for {b["bill_id"]}'
        raw=json.loads(b['json'])
        assert set(raw)==set(FIELDS), f'Wrong keys for {b["bill_id"]}'
        validate_bill(raw)
    for a,b in zip(src2,out2):
        assert all(a[k]==b[k] for k in ('bill_id','question_id','question')), 'Level 2 template modified'
        assert b['answer'].strip(), f'Empty answer for {b["bill_id"]}/{b["question_id"]}'
    print('PASS: 10 Level 1 rows, 120 Level 2 rows, all cells filled, templates preserved.')
    print('Note: This checks structure, not factual accuracy of the AI answers.')

if __name__=='__main__':
    validate()
