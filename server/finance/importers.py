import csv, hashlib, io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from django.db import transaction
from .models import Account, AccountOwner, BudgetImpact, Category, ImportBatch, ImportProfile, ImportRow, Transaction
from .services import classify_import_row, create_transaction, import_fingerprint
from .categorization import load_categorization_history

def _read_text(batch, encoding='utf-8-sig'):
    batch.raw_file.open('rb')
    data=batch.raw_file.read(); batch.raw_file.close()
    return data.decode(encoding,errors='replace')

def detect_headers(batch):
    text=_read_text(batch)
    sample=text[:8192]
    try: dialect=csv.Sniffer().sniff(sample,delimiters=',;\t|'); delimiter=dialect.delimiter
    except Exception: delimiter=','
    reader=csv.reader(io.StringIO(text),delimiter=delimiter)
    headers=next(reader,[])
    batch.headers=[h.strip() for h in headers]; batch.mapping={'delimiter':delimiter,'encoding':'utf-8-sig'}; batch.save(update_fields=['headers','mapping'])
    return batch.headers

def _num(raw, decimal_separator=',', thousands_separator='.'):
    if raw is None or str(raw).strip()=='': return Decimal('0')
    s=str(raw).strip().replace('\xa0','').replace(' ','')
    if thousands_separator: s=s.replace(thousands_separator,'')
    if decimal_separator!='.': s=s.replace(decimal_separator,'.')
    s=s.replace('€','').replace('EUR','').replace('+','')
    if s.startswith('(') and s.endswith(')'): s='-'+s[1:-1]
    return Decimal(s)

def parse_batch(batch, mapping):
    batch.rows.all().delete()
    text=_read_text(batch,mapping.get('encoding','utf-8-sig'))
    reader=csv.DictReader(io.StringIO(text),delimiter=mapping.get('delimiter',','))
    history_rows=load_categorization_history(batch.household)
    count=dups=errs=0
    for idx,row in enumerate(reader,start=2):
        try:
            d=datetime.strptime(row.get(mapping['date_column'],'').strip(),mapping.get('date_format','%d.%m.%Y')).date()
            desc=row.get(mapping['description_column'],'').strip()
            if mapping.get('amount_column'):
                amt=_num(row.get(mapping['amount_column']),mapping.get('decimal_separator',','),mapping.get('thousands_separator','.'))
            else:
                credit=_num(row.get(mapping.get('credit_column')),mapping.get('decimal_separator',','),mapping.get('thousands_separator','.'))
                debit=_num(row.get(mapping.get('debit_column')),mapping.get('decimal_separator',','),mapping.get('thousands_separator','.'))
                amt=credit-debit
            if mapping.get('invert_sign'): amt=-amt
            fp=import_fingerprint(batch.household_id,batch.account_id,d,amt,desc)
            duplicate=Transaction.objects.filter(household=batch.household,import_fingerprint=fp).exists() or ImportRow.objects.filter(batch__household=batch.household,fingerprint=fp,status=ImportRow.Status.IMPORTED).exists()
            kind,impact_kind,cat=classify_import_row(batch.household,desc,amt,history_rows=history_rows)
            ImportRow.objects.create(batch=batch,row_number=idx,transaction_date=d,description=desc,amount=amt,fingerprint=fp,raw_data=row,suggested_kind=kind,suggested_impact_kind=impact_kind,suggested_category=cat,status=ImportRow.Status.DUPLICATE if duplicate else ImportRow.Status.PENDING)
            count+=1; dups+=1 if duplicate else 0
        except Exception as e:
            ImportRow.objects.create(batch=batch,row_number=idx,raw_data=row,status=ImportRow.Status.ERROR,error=str(e)[:500]); errs+=1
    batch.mapping=mapping; batch.status=ImportBatch.Status.PARSED; batch.duplicate_count=dups; batch.error_count=errs; batch.save()
    return count

def save_profile_from_mapping(batch,name):
    if not name: return None
    m=batch.mapping
    p,_=ImportProfile.objects.update_or_create(household=batch.household,name=name,defaults={
        'account':batch.account,'delimiter':m.get('delimiter',','),'encoding':m.get('encoding','utf-8-sig'),'date_column':m.get('date_column',''),
        'description_column':m.get('description_column',''),'amount_column':m.get('amount_column',''),'debit_column':m.get('debit_column',''),'credit_column':m.get('credit_column',''),
        'date_format':m.get('date_format','%d.%m.%Y'),'decimal_separator':m.get('decimal_separator',','),'thousands_separator':m.get('thousands_separator','.'),'invert_sign':bool(m.get('invert_sign')),
    })
    batch.profile=p; batch.save(update_fields=['profile']); return p

@transaction.atomic
def commit_batch(batch,user,overrides=None):
    overrides=overrides or {}; outside=Account.objects.filter(household=batch.household,account_type=Account.Type.EXTERNAL,name='Outside world').first() or Account.objects.filter(household=batch.household,account_type=Account.Type.EXTERNAL).first()
    if not outside:
        owner=AccountOwner.objects.filter(household=batch.household,kind=AccountOwner.Kind.SYSTEM).first() or AccountOwner.objects.create(household=batch.household,name='System / External',kind=AccountOwner.Kind.SYSTEM,sort_order=900)
        outside=Account.objects.create(household=batch.household,name='Outside world',account_type=Account.Type.EXTERNAL,purpose=Account.Purpose.EXTERNAL,owner=owner,include_in_net_worth=False,currency=batch.household.settings.base_currency)
    imported=0
    for row in batch.rows.filter(status__in=[ImportRow.Status.PENDING,ImportRow.Status.DUPLICATE]).select_related('suggested_category'):
        ov=overrides.get(str(row.id),{})
        if row.status==ImportRow.Status.DUPLICATE and not ov.get('force'):
            continue
        if ov.get('skip'):
            row.status=ImportRow.Status.SKIPPED; row.save(update_fields=['status']); continue
        kind=ov.get('kind') or row.suggested_kind or (Transaction.Kind.INCOME if row.amount>0 else Transaction.Kind.EXPENSE)
        cat_id=ov.get('category_id') or row.suggested_category_id
        cat=Category.objects.filter(pk=cat_id,household=batch.household).first() if cat_id else None
        impact_kind=cat.kind if cat else ''
        amount=abs(row.amount)
        from_acc,out_acc=(outside,batch.account) if row.amount>0 else (batch.account,outside)
        impacts=[{'kind':impact_kind,'category':cat,'amount':amount}] if cat else []
        try:
            tx=create_transaction(household=batch.household,user=user,posted_date=row.transaction_date,kind=kind,description=row.description,from_account=from_acc,to_account=out_acc,amount=amount,impacts=impacts,fingerprint=row.fingerprint)
            row.transaction=tx; row.status=ImportRow.Status.IMPORTED; row.save(update_fields=['transaction','status']); imported+=1
        except Exception as e:
            row.status=ImportRow.Status.ERROR; row.error=str(e)[:500]; row.save(update_fields=['status','error'])
    batch.imported_count=batch.rows.filter(status=ImportRow.Status.IMPORTED).count(); batch.error_count=batch.rows.filter(status=ImportRow.Status.ERROR).count(); batch.status=ImportBatch.Status.IMPORTED; batch.save()
    return imported
