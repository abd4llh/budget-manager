import base64
import hashlib
import json
import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import jwt
from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .models import (Account, AccountOwner, BankConnection, BankLinkedAccount, BankSyncTransaction,
                     BudgetImpact, Category, Transaction, ZERO)
from .services import classify_import_row, create_transaction
from .categorization import load_categorization_history

API_BASE='https://api.enablebanking.com'

class BankSyncError(RuntimeError):
    def __init__(self, message, *, code=None, error_name='', payload=None):
        super().__init__(message)
        self.code=code
        self.error_name=error_name or ''
        self.payload=payload or {}

class BankRateLimitError(BankSyncError):
    pass

RATE_LIMIT_ERROR='ASPSP_RATE_LIMIT_EXCEEDED'

def _env_int(name, default):
    try:
        return int(os.getenv(name,str(default)) or default)
    except (TypeError,ValueError):
        return default

def is_rate_limit_error(value):
    text=str(value or '')
    return RATE_LIMIT_ERROR in text or ('HTTP 429' in text and 'rate' in text.casefold())

def psu_headers_from_request(request):
    """Build Enable Banking PSU headers from the browser request that triggered a manual refresh."""
    meta=getattr(request,'META',{}) or {}
    forwarded=(meta.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    remote=(meta.get('REMOTE_ADDR') or '').strip()
    headers={
        'Psu-Ip-Address': forwarded or remote,
        'Psu-User-Agent': meta.get('HTTP_USER_AGENT','').strip(),
        'Psu-Referer': meta.get('HTTP_REFERER','').strip(),
        'Psu-Accept': meta.get('HTTP_ACCEPT','').strip() or '*/*',
        'Psu-Accept-Charset': meta.get('HTTP_ACCEPT_CHARSET','').strip() or 'utf-8',
        'Psu-Accept-Encoding': meta.get('HTTP_ACCEPT_ENCODING','').strip(),
        'Psu-Accept-Language': meta.get('HTTP_ACCEPT_LANGUAGE','').strip(),
    }
    return {k:v for k,v in headers.items() if v}

def bank_sync_config():
    app_id=os.getenv('ENABLE_BANKING_APP_ID','').strip()
    key_b64=os.getenv('ENABLE_BANKING_PRIVATE_KEY_B64','').strip()
    try:
        lookback_days=int(os.getenv('BANK_SYNC_LOOKBACK_DAYS','90') or '90')
    except (TypeError,ValueError):
        lookback_days=90
    return {
        'configured': bool(app_id and key_b64),
        'app_id': app_id,
        'base_url': os.getenv('BANK_SYNC_BASE_URL','').strip().rstrip('/'),
        'lookback_days': max(1,min(lookback_days,730)),
        'background_interval_hours': max(6,min(_env_int('BANK_SYNC_BACKGROUND_INTERVAL_HOURS',6),24)),
    }

def _private_key():
    raw=os.getenv('ENABLE_BANKING_PRIVATE_KEY_B64','').strip()
    if not raw:
        raise BankSyncError('ENABLE_BANKING_PRIVATE_KEY_B64 is not configured.')
    try:
        return base64.b64decode(raw).decode('utf-8')
    except Exception as exc:
        raise BankSyncError('ENABLE_BANKING_PRIVATE_KEY_B64 is not valid base64 PEM data.') from exc

class EnableBankingClient:
    def __init__(self):
        cfg=bank_sync_config()
        if not cfg['configured']:
            raise BankSyncError('Enable Banking is not configured yet.')
        self.app_id=cfg['app_id']
        self.api_base=os.getenv('ENABLE_BANKING_API_BASE',API_BASE).rstrip('/')
        self.private_key=_private_key()

    def _jwt(self):
        now=int(timezone.now().timestamp())
        return jwt.encode(
            {'iss':'enablebanking.com','aud':'api.enablebanking.com','iat':now,'exp':now+300},
            self.private_key,
            algorithm='RS256',
            headers={'typ':'JWT','kid':self.app_id},
        )

    def request(self, method, path, *, params=None, data=None, psu_headers=None):
        url=f'{self.api_base}{path}'
        if params:
            url += '?' + urlencode({k:v for k,v in params.items() if v not in (None,'')})
        body=None if data is None else json.dumps(data).encode('utf-8')
        headers={'Accept':'application/json','Authorization':f'Bearer {self._jwt()}'}
        if psu_headers:
            headers.update({str(k):str(v) for k,v in psu_headers.items() if v})
        if body is not None: headers['Content-Type']='application/json'
        req=Request(url,data=body,headers=headers,method=method)
        try:
            with urlopen(req,timeout=30) as r:
                raw=r.read().decode('utf-8')
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            try: detail=exc.read().decode('utf-8')
            except Exception: detail=''
            try: payload=json.loads(detail) if detail else {}
            except Exception: payload={}
            error_name=str(payload.get('error') or (payload.get('detail') or {}).get('error_name') or '')
            provider_message=str(payload.get('message') or (payload.get('detail') or {}).get('message') or '').strip()
            if exc.code==429 and (error_name==RATE_LIMIT_ERROR or 'multiplicity' in provider_message.casefold()):
                msg='Bank background refresh limit reached (HTTP 429 / ASPSP_RATE_LIMIT_EXCEEDED).'
                if provider_message: msg += ' ' + provider_message
                raise BankRateLimitError(msg,code=exc.code,error_name=RATE_LIMIT_ERROR,payload=payload) from exc
            raise BankSyncError(f'Enable Banking returned HTTP {exc.code}: {detail[:800]}',code=exc.code,error_name=error_name,payload=payload) from exc
        except URLError as exc:
            raise BankSyncError(f'Could not reach Enable Banking: {exc.reason}') from exc

    def list_aspsps(self, country='DE'):
        data=self.request('GET','/aspsps',params={'country':country,'psu_type':'personal','service':'AIS'})
        return data.get('aspsps',[])

    def start_authorization(self, *, aspsp_name, country, state, redirect_url, psu_id, maximum_consent_validity=None):
        max_seconds=maximum_consent_validity
        try: max_seconds=int(max_seconds) if max_seconds else 90*86400
        except Exception: max_seconds=90*86400
        # Avoid asking for more than 180 days even when a connector advertises a larger value.
        seconds=max(3600,min(max_seconds,180*86400))
        valid_until=timezone.now()+timedelta(seconds=seconds-300 if seconds>3600 else seconds)
        payload={
            'access':{'balances':True,'transactions':True,'valid_until':valid_until.isoformat()},
            'aspsp':{'name':aspsp_name,'country':country},
            'state':state,
            'redirect_url':redirect_url,
            'psu_type':'personal',
            'language':'en',
            'psu_id':psu_id,
        }
        return self.request('POST','/auth',data=payload)

    def authorize_session(self, code): return self.request('POST','/sessions',data={'code':code})
    def session(self, session_id): return self.request('GET',f'/sessions/{session_id}')
    def close_session(self, session_id): return self.request('DELETE',f'/sessions/{session_id}')
    def balances(self, account_uid, *, psu_headers=None): return self.request('GET',f'/accounts/{account_uid}/balances',psu_headers=psu_headers)
    def transactions(self, account_uid, date_from, date_to, *, psu_headers=None):
        base={'date_from':date_from.isoformat(),'transaction_status':'BOOK','strategy':'longest'}
        key=None
        while True:
            params=dict(base)
            if key: params['continuation_key']=key
            page=self.request('GET',f'/accounts/{account_uid}/transactions',params=params,psu_headers=psu_headers)
            for item in page.get('transactions',[]): yield item
            key=page.get('continuation_key')
            if not key: break


def mask_iban(raw):
    if not raw: return ''
    s=''.join(str(raw).split())
    return ('•••• ' + s[-4:]) if len(s)>4 else s

def _account_iban(account_payload):
    aid=account_payload.get('account_id') or {}
    return aid.get('iban') or aid.get('bban') or ''

def store_authorized_session(*, household, user, payload, connection=None):
    aspsp=payload.get('aspsp') or {}
    access=payload.get('access') or {}
    valid=parse_datetime(access.get('valid_until') or '') if access.get('valid_until') else None
    now=timezone.now()
    defaults={
        'provider':BankConnection.Provider.ENABLE_BANKING,
        'aspsp_name':aspsp.get('name') or (connection.aspsp_name if connection else 'Bank'),
        'country':aspsp.get('country') or (connection.country if connection else 'DE'),
        'psu_type':payload.get('psu_type') or 'personal',
        'session_id':payload.get('session_id',''),
        'psu_id_hash':payload.get('psu_id_hash',''),
        'status':BankConnection.Status.AUTHORIZED,
        'valid_until':valid,
        'authorized_at':now,
        'last_error':'',
        'created_by':user,
    }
    if connection:
        for k,v in defaults.items(): setattr(connection,k,v)
        connection.save()
    else:
        connection=BankConnection.objects.create(household=household,**defaults)

    seen=[]
    for item in payload.get('accounts',[]):
        ident=item.get('identification_hash') or ''
        uid=item.get('uid') or ''
        if not ident or not uid: continue
        iban=_account_iban(item)
        link,created=BankLinkedAccount.objects.update_or_create(
            connection=connection,identification_hash=ident,
            defaults={
                'provider_account_uid':uid,
                'identification_hashes':item.get('identification_hashes') or [ident],
                'name':item.get('name') or item.get('product') or '',
                'currency':(item.get('currency') or '').upper(),
                'masked_iban':mask_iban(iban),
                'product':item.get('product') or '',
                'cash_account_type':item.get('cash_account_type') or '',
                'is_active':True,
            },
        )
        seen.append(link.pk)
    if seen:
        connection.linked_accounts.exclude(pk__in=seen).update(is_active=False)
    return connection

def _balance_choice(balances):
    rows=balances.get('balances',[]) or []
    if not rows: return None
    preferred=['CLBD','CLAV','ITAV','XPCD','OPBD']
    for typ in preferred:
        for b in rows:
            if b.get('balance_type')==typ: return b
    return rows[0]

def _party_name(tx):
    indicator=tx.get('credit_debit_indicator')
    party=(tx.get('debtor') if indicator=='CRDT' else tx.get('creditor')) or {}
    return (party.get('name') or '').strip()

def normalise_bank_transaction(tx):
    amount_obj=tx.get('transaction_amount') or {}
    try: amount=Decimal(str(amount_obj.get('amount','0')))
    except (InvalidOperation,TypeError): amount=ZERO
    if tx.get('credit_debit_indicator')=='DBIT': amount=-abs(amount)
    else: amount=abs(amount)
    d=parse_date(tx.get('booking_date') or '') or parse_date(tx.get('value_date') or '') or parse_date(tx.get('transaction_date') or '') or date.today()
    vd=parse_date(tx.get('value_date') or '')
    party=_party_name(tx)
    rem=tx.get('remittance_information') or []
    if isinstance(rem,str): rem=[rem]
    pieces=[party]+[str(x).strip() for x in rem if str(x).strip()]
    if not pieces and tx.get('note'): pieces=[str(tx['note']).strip()]
    if not pieces:
        code=tx.get('bank_transaction_code') or {}; pieces=[code.get('description') or 'Bank transaction']
    description=' · '.join(dict.fromkeys([p for p in pieces if p]))[:500]
    return {
        'entry_reference':str(tx.get('entry_reference') or ''),
        'booking_date':d,
        'value_date':vd,
        'amount':amount.quantize(Decimal('0.01')),
        'currency':str(amount_obj.get('currency') or '').upper()[:3],
        'description':description or 'Bank transaction',
        'counterparty':party[:240],
        'provider_status':str(tx.get('status') or '')[:16],
    }

def provider_tx_key(link, tx, normal):
    entry=normal['entry_reference']
    if entry:
        raw=f'{link.identification_hash}|entry|{entry}'
    else:
        raw='|'.join([
            link.identification_hash,
            normal['booking_date'].isoformat(),
            str(normal['amount']),
            normal['currency'],
            normal['description'].casefold(),
            str(tx.get('reference_number') or ''),
            str((tx.get('balance_after_transaction') or {}).get('amount') or ''),
        ])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def _interactive_headers_for_connection(client, connection, provided):
    provided={k:v for k,v in (provided or {}).items() if v}
    if not provided:
        return None
    try:
        banks=client.list_aspsps(connection.country)
        selected=next((b for b in banks if (b.get('name') or '')==connection.aspsp_name),None)
        required=list((selected or {}).get('required_psu_headers') or [])
        available={str(k).casefold():v for k,v in provided.items() if v}
        missing=[name for name in required if not available.get(str(name).casefold())]
        if missing:
            raise BankSyncError('Interactive bank refresh cannot supply required browser header(s): ' + ', '.join(missing))
    except BankSyncError:
        raise
    except Exception:
        # The account fetch can still proceed with the browser context we have if metadata lookup is temporarily unavailable.
        pass
    return provided

def next_background_sync_at(connection, *, now=None):
    now=now or timezone.now()
    interval=timedelta(hours=bank_sync_config()['background_interval_hours'])
    anchor=None
    if connection.last_error and is_rate_limit_error(connection.last_error):
        anchor=connection.updated_at
    elif connection.last_synced_at:
        anchor=connection.last_synced_at
    return (anchor+interval) if anchor else None

def background_sync_due(connection, *, now=None):
    now=now or timezone.now()
    next_at=next_background_sync_at(connection,now=now)
    return next_at is None or now>=next_at

def describe_sync_issue(connection):
    if not connection.last_error:
        return None
    if is_rate_limit_error(connection.last_error):
        return {
            'kind':'rate_limit',
            'title':'Bank refresh limit reached',
            'message':'The bank has temporarily limited unattended background refreshes. Your consent and previously synced data are still valid.',
            'next_at':next_background_sync_at(connection),
            'technical':connection.last_error,
        }
    return {
        'kind':'error',
        'title':'Last sync issue',
        'message':'The last bank refresh did not complete. Existing imported data is unaffected.',
        'next_at':None,
        'technical':connection.last_error,
    }

def sync_connection(connection, *, client=None, psu_headers=None):
    if not connection.session_id: raise BankSyncError('Connection has no authorised session.')
    client=client or EnableBankingClient()
    psu_headers=_interactive_headers_for_connection(client,connection,psu_headers) if psu_headers else None
    session=client.session(connection.session_id)
    provider_status=session.get('status','')
    status_map={'AUTHORIZED':BankConnection.Status.AUTHORIZED,'EXPIRED':BankConnection.Status.EXPIRED,'REVOKED':BankConnection.Status.REVOKED,'CLOSED':BankConnection.Status.CLOSED}
    if provider_status!='AUTHORIZED':
        connection.status=status_map.get(provider_status,BankConnection.Status.ERROR)
        connection.last_error=f'Provider session status: {provider_status or "unknown"}'
        connection.save(update_fields=['status','last_error','updated_at'])
        raise BankSyncError(connection.last_error)

    cfg=bank_sync_config(); now=timezone.now(); staged=0
    categorization_history=load_categorization_history(connection.household)
    for link in connection.linked_accounts.filter(is_active=True):
        try:
            choice=_balance_choice(client.balances(link.provider_account_uid,psu_headers=psu_headers))
            if choice:
                amount=(choice.get('balance_amount') or {}).get('amount')
                if amount is not None:
                    link.last_balance=Decimal(str(amount)).quantize(Decimal('0.01'))
                    link.last_balance_type=choice.get('balance_type') or choice.get('name') or ''
                    link.last_balance_at=now
            if link.last_transaction_sync:
                start=max(date.today()-timedelta(days=cfg['lookback_days']),link.last_transaction_sync.date()-timedelta(days=7))
            else:
                start=date.today()-timedelta(days=cfg['lookback_days'])
            for raw in client.transactions(link.provider_account_uid,start,date.today(),psu_headers=psu_headers):
                n=normalise_bank_transaction(raw)
                if not n['currency']: n['currency']=link.currency
                key=provider_tx_key(link,raw,n)
                kind,impact_kind,cat=classify_import_row(connection.household,n['description'],n['amount'],counterparty=n['counterparty'],raw_data=raw,bank_account=link,history_rows=categorization_history)
                obj,created=BankSyncTransaction.objects.get_or_create(
                    bank_account=link,provider_tx_key=key,
                    defaults={**n,'raw_data':raw,'suggested_kind':kind or '','suggested_impact_kind':impact_kind or '','suggested_category':cat},
                )
                if created: staged+=1
            link.last_transaction_sync=now
            link.save(update_fields=['last_balance','last_balance_type','last_balance_at','last_transaction_sync','updated_at'])
        except Exception as exc:
            connection.last_error=f'{link}: {exc}'[:2000]
            connection.save(update_fields=['last_error','updated_at'])
            raise
    connection.status=BankConnection.Status.AUTHORIZED; connection.last_synced_at=now; connection.last_error=''
    connection.save(update_fields=['status','last_synced_at','last_error','updated_at'])
    return staged

def _outside_account(household):
    outside=Account.objects.filter(household=household,account_type=Account.Type.EXTERNAL,name='Outside world').first() or Account.objects.filter(household=household,account_type=Account.Type.EXTERNAL).first()
    if outside: return outside
    owner=AccountOwner.objects.filter(household=household,kind=AccountOwner.Kind.SYSTEM).first() or AccountOwner.objects.create(household=household,name='System / External',kind=AccountOwner.Kind.SYSTEM,sort_order=900)
    return Account.objects.create(household=household,name='Outside world',account_type=Account.Type.EXTERNAL,purpose=Account.Purpose.EXTERNAL,owner=owner,include_in_net_worth=False,currency=household.settings.base_currency)

def transfer_candidates(row, queryset=None):
    if not row.bank_account.account_id: return []
    queryset=queryset if queryset is not None else BankSyncTransaction.objects.filter(bank_account__connection__household=row.bank_account.connection.household,status=BankSyncTransaction.Status.PENDING)
    start=row.booking_date-timedelta(days=2); end=row.booking_date+timedelta(days=2)
    out=[]
    for other in queryset.select_related('bank_account__account').filter(booking_date__gte=start,booking_date__lte=end).exclude(pk=row.pk):
        if not other.bank_account.account_id or other.bank_account.account_id==row.bank_account.account_id: continue
        if other.currency!=row.currency: continue
        if other.amount == -row.amount: out.append(other)
    return out

@db_transaction.atomic
def import_bank_row(row, *, user, category=None, match=None):
    row=BankSyncTransaction.objects.select_for_update(of=('self',)).select_related('bank_account__connection__household','bank_account__account').get(pk=row.pk)
    if row.status!=BankSyncTransaction.Status.PENDING: return row.transaction
    household=row.bank_account.connection.household
    account=row.bank_account.account
    if not account: raise ValueError('Map this bank account to a Budget Manager account first.')
    base=household.settings.base_currency
    if account.currency!=base or row.currency!=base:
        raise ValueError(f'Automatic import currently requires {base} accounts. Import foreign-currency rows manually so the base-currency value is explicit.')

    if match:
        match=BankSyncTransaction.objects.select_for_update(of=('self',)).select_related('bank_account__account').get(pk=match.pk)
        if match.status!=BankSyncTransaction.Status.PENDING: raise ValueError('The matching bank row has already been handled.')
        if not match.bank_account.account_id: raise ValueError('Map both bank accounts before importing a transfer.')
        if match.currency!=row.currency or match.amount != -row.amount: raise ValueError('Selected rows are not opposite sides of the same amount/currency.')
        if match.bank_account.account_id==account.id: raise ValueError('A transfer needs two different mapped accounts.')
        incoming=row if row.amount>0 else match
        outgoing=match if row.amount>0 else row
        src=outgoing.bank_account.account; dst=incoming.bank_account.account; amount=abs(row.amount)
        impacts=[{'kind':category.kind,'category':category,'amount':amount}] if category else []
        tx=create_transaction(household=household,user=user,posted_date=max(row.booking_date,match.booking_date),kind=Transaction.Kind.TRANSFER,
            description=f'Transfer: {src.name} → {dst.name}',from_account=src,to_account=dst,amount=amount,impacts=impacts,
            external_id=f'bank-transfer:{row.provider_tx_key[:24]}:{match.provider_tx_key[:24]}')
        row.transaction=tx; row.status=BankSyncTransaction.Status.IMPORTED; row.save(update_fields=['transaction','status','updated_at'])
        match.transaction=tx; match.status=BankSyncTransaction.Status.IMPORTED; match.save(update_fields=['transaction','status','updated_at'])
        return tx

    outside=_outside_account(household)
    amount=abs(row.amount)
    from_acc,to_acc=(outside,account) if row.amount>0 else (account,outside)
    kind=row.suggested_kind or (Transaction.Kind.INCOME if row.amount>0 else Transaction.Kind.EXPENSE)
    impacts=[]
    if category:
        impacts=[{'kind':category.kind,'category':category,'amount':amount}]
        kind=Transaction.Kind.INCOME if category.kind==Category.Kind.FUNDING else (Transaction.Kind.SAVINGS if category.kind==Category.Kind.SAVINGS else Transaction.Kind.EXPENSE)
    tx=create_transaction(household=household,user=user,posted_date=row.booking_date,kind=kind,description=row.description,
        payee=row.counterparty,from_account=from_acc,to_account=to_acc,amount=amount,impacts=impacts,
        external_id=(row.entry_reference or f'bank:{row.provider_tx_key}')[:160],fingerprint=row.provider_tx_key)
    # Persist the category the user actually confirmed, not the old suggestion.
    # Future suggestions learn from the resulting transaction BudgetImpact, while
    # this keeps the staging row itself honest for audit/review displays.
    row.transaction=tx; row.status=BankSyncTransaction.Status.IMPORTED
    row.suggested_category=category
    row.suggested_impact_kind=category.kind if category else ''
    row.suggested_kind=kind
    row.save(update_fields=['transaction','status','suggested_category','suggested_impact_kind','suggested_kind','updated_at'])
    return tx

def sync_all_connections():
    if not bank_sync_config()['configured']: return {'connections':0,'staged':0,'errors':0,'skipped':0}
    result={'connections':0,'staged':0,'errors':0,'skipped':0}
    client=EnableBankingClient()
    now=timezone.now()
    for c in BankConnection.objects.filter(status=BankConnection.Status.AUTHORIZED).order_by('id'):
        if not background_sync_due(c,now=now):
            result['skipped']+=1
            continue
        result['connections']+=1
        try: result['staged']+=sync_connection(c,client=client)
        except Exception: result['errors']+=1
    return result
