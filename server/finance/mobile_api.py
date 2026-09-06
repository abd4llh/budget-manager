import hashlib, json, secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth import authenticate
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Max, Q, Sum
from django.db.models.functions import Abs
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .bank_sync import BankRateLimitError, describe_sync_issue, import_bank_row, next_background_sync_at, psu_headers_from_request, sync_connection, transfer_candidates
from .models import (Account, BankConnection, BankSyncTransaction, BudgetImpact, BudgetPlan, Category, HouseholdSettings,
                     Membership, MobileApiToken, ReimbursementLink, Transaction, ZERO)
from .reports import category_expenses, monthly_series
from .services import active_budget_month, net_worth_at, pair_reimbursement, unpair_reimbursement
from .utils import add_months, month_start
from .version import get_version
from .categorization import load_categorization_history, refresh_bank_row_suggestion

TOKEN_DAYS=90
EDIT_ROLES={Membership.Role.OWNER,Membership.Role.MEMBER}


def _money(v):
    return f'{Decimal(v or 0).quantize(Decimal("0.01")):.2f}'


def _json_body(request):
    if not request.body:
        return {}
    try:
        data=json.loads(request.body.decode('utf-8'))
        return data if isinstance(data,dict) else {}
    except Exception:
        return None


def _error(message,status=400,code='bad_request',**extra):
    payload={'ok':False,'error':code,'message':str(message)}
    payload.update(extra)
    return JsonResponse(payload,status=status)


def _ok(**payload):
    return JsonResponse({'ok':True,**payload})


def _token_digest(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _bearer(request):
    header=request.META.get('HTTP_AUTHORIZATION','')
    if not header.lower().startswith('bearer '): return ''
    return header.split(' ',1)[1].strip()


def mobile_auth(view):
    @csrf_exempt
    def wrapped(request,*args,**kwargs):
        raw=_bearer(request)
        if not raw: return _error('Authentication required.',401,'unauthorized')
        token=(MobileApiToken.objects.select_related('user','household')
               .filter(token_hash=_token_digest(raw),is_active=True).first())
        if not token or not token.user.is_active:
            return _error('The mobile session is invalid or has been revoked.',401,'unauthorized')
        if token.expires_at and token.expires_at<=timezone.now():
            token.is_active=False; token.save(update_fields=['is_active'])
            return _error('The mobile session expired. Sign in again.',401,'token_expired')
        membership=Membership.objects.filter(household=token.household,user=token.user).first()
        if not membership:
            return _error('This login no longer belongs to the household.',403,'forbidden')
        now=timezone.now()
        if not token.last_used_at or now-token.last_used_at>timedelta(minutes=15):
            token.last_used_at=now; token.save(update_fields=['last_used_at'])
        request.mobile_token=token; request.mobile_user=token.user; request.mobile_household=token.household; request.mobile_membership=membership
        return view(request,*args,**kwargs)
    return wrapped


def _require_edit(request):
    if request.mobile_membership.role not in EDIT_ROLES:
        return _error('This household login is read-only.',403,'read_only')
    return None


def _owners(account,user):
    selected=list(account.owners.all())
    if not selected and account.owner_id: selected=[account.owner]
    return [{'id':o.id,'name':'You' if o.linked_user_id==user.id else o.name,'kind':o.kind} for o in selected if o]


def _account_json(a,user):
    return {'id':a.id,'name':a.name,'institution':a.institution,'currency':a.currency,'type':a.account_type,'type_label':a.get_account_type_display(),
            'purpose':a.purpose,'purpose_label':a.get_purpose_display(),'balance':_money(a.display_balance),'base_balance':_money(a.display_base_balance),
            'include_in_net_worth':a.include_in_net_worth,'owners':_owners(a,user),'active':a.is_active}


def _impact_json(i):
    return {'id':i.id,'kind':i.kind,'category_id':i.category_id,'category':i.category.name,'amount':_money(i.amount),
            'budget_month':i.budget_date.strftime('%Y-%m'),'memo':i.memo}


def _transaction_json(tx):
    entries=list(tx.entries.select_related('account').all())
    source=next((e for e in entries if e.amount<0),None); dest=next((e for e in entries if e.amount>0),None)
    return {'id':tx.id,'date':tx.date.isoformat(),'kind':tx.kind,'kind_label':tx.get_kind_display(),'status':tx.status,'description':tx.description,
            'payee':tx.payee,'notes':tx.notes,'amount':_money(tx.base_amount),
            'from_account':{'id':source.account_id,'name':source.account.name} if source else None,
            'to_account':{'id':dest.account_id,'name':dest.account.name} if dest else None,
            'budget_impacts':[_impact_json(i) for i in tx.budget_impacts.select_related('category').all()],
            'tags':[t.name for t in tx.tags.all()],
            'reimbursement':({'role':'purchase','paired_transaction_id':tx.purchase_reimbursement_link.reimbursement_id,'amount':_money(tx.purchase_reimbursement_link.amount)} if hasattr(tx,'purchase_reimbursement_link') else
                             {'role':'reimbursement','paired_transaction_id':tx.reimbursement_link.purchase_id,'amount':_money(tx.reimbursement_link.amount)} if hasattr(tx,'reimbursement_link') else None)}


@csrf_exempt
@require_http_methods(['POST'])
def login(request):
    data=_json_body(request)
    if data is None: return _error('Invalid JSON body.')
    username=str(data.get('username','')).strip(); password=str(data.get('password','')); device=str(data.get('device_name','Android')).strip()[:120] or 'Android'
    if not username or not password: return _error('Username and password are required.')
    user=authenticate(request,username=username,password=password)
    if not user: return _error('Incorrect username or password.',401,'invalid_credentials')
    membership=Membership.objects.select_related('household').filter(user=user).order_by('id').first()
    if not membership: return _error('This login is not attached to a Budget Manager household.',403,'no_household')
    raw=secrets.token_urlsafe(32)
    expires=timezone.now()+timedelta(days=TOKEN_DAYS)
    token=MobileApiToken.objects.create(user=user,household=membership.household,name=device,token_hash=_token_digest(raw),last_used_at=timezone.now(),expires_at=expires)
    # Keep a small number of active device sessions per user without surprising old phones forever.
    stale=MobileApiToken.objects.filter(user=user,is_active=True).exclude(pk=token.pk).order_by('-last_used_at','-created_at')[8:]
    MobileApiToken.objects.filter(pk__in=[x.pk for x in stale]).update(is_active=False)
    return _ok(token=raw,expires_at=expires.isoformat(),user={'id':user.id,'username':user.username,'name':user.get_full_name() or user.username},
               household={'id':membership.household_id,'name':membership.household.name,'role':membership.role},api_version=1,server_version=get_version())


@mobile_auth
@require_http_methods(['POST'])
def logout(request):
    request.mobile_token.is_active=False; request.mobile_token.save(update_fields=['is_active'])
    return _ok()


@mobile_auth
def bootstrap(request):
    h=request.mobile_household; settings,_=HouseholdSettings.objects.get_or_create(household=h); active=active_budget_month(h)
    return _ok(api_version=1,server_version=get_version(),user={'id':request.mobile_user.id,'username':request.mobile_user.username,'name':request.mobile_user.get_full_name() or request.mobile_user.username},
               household={'id':h.id,'name':h.name,'role':request.mobile_membership.role,'base_currency':settings.base_currency},active_month=active.strftime('%Y-%m'),
               can_edit=request.mobile_membership.role in EDIT_ROLES,
               categories=list(h.categories.filter(is_active=True).values('id','name','kind','sort_order')))


def _parse_month(raw,fallback):
    try:
        if raw:
            y,m=map(int,raw.split('-',1)); return month_start(y,m)
    except Exception: pass
    return fallback


@mobile_auth
def dashboard(request):
    h=request.mobile_household; m=_parse_month(request.GET.get('month'),active_budget_month(h)); nxt=add_months(m,1)
    actual=BudgetImpact.objects.filter(transaction__household=h,budget_date__gte=m,budget_date__lt=nxt).values('kind').annotate(total=Sum('amount'))
    planned=BudgetPlan.objects.filter(household=h,month=m).values('category__kind').annotate(total=Sum('amount'))
    a={r['kind']:r['total'] or ZERO for r in actual}; p={r['category__kind']:r['total'] or ZERO for r in planned}
    recent=list(h.transactions.prefetch_related('entries__account','budget_impacts__category','tags')[:8])
    accounts=list(h.accounts.filter(is_active=True).select_related('owner','owner__linked_user').prefetch_related('owners__linked_user').order_by('purpose','name'))
    review_count=BankSyncTransaction.objects.filter(bank_account__connection__household=h,status=BankSyncTransaction.Status.PENDING).count()
    return _ok(month=m.strftime('%Y-%m'),net_worth=_money(net_worth_at(h)),review_count=review_count,
               actual={'funding':_money(a.get('funding',ZERO)),'expenses':_money(a.get('expense',ZERO)),'savings':_money(a.get('savings',ZERO)),'remaining':_money(a.get('funding',ZERO)-a.get('expense',ZERO)-a.get('savings',ZERO))},
               planned={'funding':_money(p.get('funding',ZERO)),'expenses':_money(p.get('expense',ZERO)),'savings':_money(p.get('savings',ZERO)),'remaining':_money(p.get('funding',ZERO)-p.get('expense',ZERO)-p.get('savings',ZERO))},
               accounts=[_account_json(x,request.mobile_user) for x in accounts],recent=[_transaction_json(x) for x in recent])


@mobile_auth
def accounts(request):
    h=request.mobile_household
    rows=h.accounts.filter(is_active=True).select_related('owner','owner__linked_user').prefetch_related('owners__linked_user').order_by('purpose','name')
    return _ok(accounts=[_account_json(a,request.mobile_user) for a in rows],net_worth=_money(net_worth_at(h)))


@mobile_auth
def budget(request):
    h=request.mobile_household; m=_parse_month(request.GET.get('month'),active_budget_month(h)); nxt=add_months(m,1)
    actual={r['category_id']:r['total'] or ZERO for r in BudgetImpact.objects.filter(transaction__household=h,budget_date__gte=m,budget_date__lt=nxt).values('category_id').annotate(total=Sum('amount'))}
    plans={x.category_id:x for x in BudgetPlan.objects.filter(household=h,month=m).select_related('category')}
    rows=[]
    for c in h.categories.filter(is_active=True).order_by('kind','sort_order','name'):
        plan=plans.get(c.id); rows.append({'category_id':c.id,'category':c.name,'kind':c.kind,'planned':_money(plan.amount if plan else ZERO),'actual':_money(actual.get(c.id,ZERO)),
                                            'note':plan.note if plan else '','plan_id':plan.id if plan else None})
    return _ok(month=m.strftime('%Y-%m'),previous=add_months(m,-1).strftime('%Y-%m'),next=nxt.strftime('%Y-%m'),lines=rows)


@mobile_auth
@require_http_methods(['POST'])
def budget_line(request):
    denied=_require_edit(request)
    if denied:return denied
    data=_json_body(request)
    if data is None:return _error('Invalid JSON body.')
    try:
        category_id=int(data.get('category_id')); c=Category.objects.get(pk=category_id,household=request.mobile_household,is_active=True)
        m=_parse_month(str(data.get('month','')),None)
        if not m: raise ValueError('month')
        amount=Decimal(str(data.get('amount','0')).replace(',','.')).quantize(Decimal('0.01'))
        if amount<0: raise ValueError('amount')
    except (ValueError,TypeError,InvalidOperation,Category.DoesNotExist):
        return _error('Choose a valid month, category and non-negative amount.')
    plan,_=BudgetPlan.objects.update_or_create(household=request.mobile_household,month=m,category=c,defaults={'amount':amount,'note':str(data.get('note',''))[:160]})
    return _ok(line={'plan_id':plan.id,'category_id':c.id,'category':c.name,'kind':c.kind,'planned':_money(plan.amount),'note':plan.note})


def _bank_row_json(row):
    candidates=getattr(row,'mobile_candidates',[])
    return {'id':row.id,'date':row.booking_date.isoformat(),'amount':_money(row.amount),'direction':'in' if row.amount>0 else 'out','currency':row.currency,'description':row.description,
            'counterparty':row.counterparty,'reference':row.entry_reference,'bank':row.bank_account.connection.aspsp_name,'feed':row.bank_account.name or row.bank_account.masked_iban,
            'mapped_account':row.bank_account.account.name if row.bank_account.account_id else None,'status':row.status,
            'suggested_category_id':row.suggested_category_id,'suggested_category':row.suggested_category.name if row.suggested_category_id else None,
            'suggestion_source':getattr(row,'suggestion_source',''),'suggestion_confidence':getattr(row,'suggestion_confidence',''),
            'suggestion_reason':getattr(row,'suggestion_reason',''),
            'allowed_category_kind':Category.Kind.FUNDING if row.amount>0 else Category.Kind.EXPENSE,
            'transaction_id':row.transaction_id,'transfer_candidates':[{'id':c.id,'date':c.booking_date.isoformat(),'amount':_money(c.amount),'description':c.description,'bank':c.bank_account.connection.aspsp_name,'mapped_account':c.bank_account.account.name if c.bank_account.account_id else None} for c in candidates]}


@mobile_auth
def review(request):
    h=request.mobile_household; status=request.GET.get('status','pending'); q=str(request.GET.get('q','')).strip(); direction=request.GET.get('direction','')
    qs=BankSyncTransaction.objects.filter(bank_account__connection__household=h).select_related('bank_account__connection','bank_account__account','suggested_category','transaction').annotate(abs_amount=Abs('amount'))
    if status in dict(BankSyncTransaction.Status.choices): qs=qs.filter(status=status)
    elif status!='all': qs=qs.filter(status=BankSyncTransaction.Status.PENDING)
    for word in q.split():
        qs=qs.filter(Q(description__icontains=word)|Q(counterparty__icontains=word)|Q(entry_reference__icontains=word)|Q(bank_account__connection__aspsp_name__icontains=word)|Q(bank_account__account__name__icontains=word))
    if direction=='in':qs=qs.filter(amount__gt=0)
    elif direction=='out':qs=qs.filter(amount__lt=0)
    try:
        page_size=max(20,min(100,int(request.GET.get('page_size','50'))))
    except ValueError:page_size=50
    paginator=Paginator(qs.order_by('-booking_date','-id'),page_size); page=paginator.get_page(request.GET.get('page','1'))
    pending_qs=BankSyncTransaction.objects.filter(bank_account__connection__household=h,status=BankSyncTransaction.Status.PENDING).select_related('bank_account__connection','bank_account__account')
    categorization_history=load_categorization_history(h)
    page_rows=list(page.object_list)
    for row in page_rows:
        row.mobile_candidates=transfer_candidates(row,pending_qs)[:3] if row.status==BankSyncTransaction.Status.PENDING else []
        if row.status==BankSyncTransaction.Status.PENDING:
            refresh_bank_row_suggestion(row,history_rows=categorization_history)
    return _ok(count=paginator.count,page=page.number,pages=paginator.num_pages,rows=[_bank_row_json(x) for x in page_rows])


@mobile_auth
@require_http_methods(['POST'])
def review_ignore(request,pk):
    denied=_require_edit(request)
    if denied:return denied
    row=BankSyncTransaction.objects.filter(pk=pk,bank_account__connection__household=request.mobile_household).first()
    if not row:return _error('Bank transaction not found.',404,'not_found')
    if row.status==BankSyncTransaction.Status.PENDING:
        row.status=BankSyncTransaction.Status.IGNORED; row.save(update_fields=['status','updated_at'])
    return _ok(row=_bank_row_json(row))


@mobile_auth
@require_http_methods(['POST'])
def review_restore(request,pk):
    denied=_require_edit(request)
    if denied:return denied
    row=BankSyncTransaction.objects.filter(pk=pk,bank_account__connection__household=request.mobile_household).first()
    if not row:return _error('Bank transaction not found.',404,'not_found')
    if row.status==BankSyncTransaction.Status.IGNORED:
        row.status=BankSyncTransaction.Status.PENDING; row.save(update_fields=['status','updated_at'])
    return _ok(row=_bank_row_json(row))


@mobile_auth
@require_http_methods(['POST'])
def review_import(request,pk):
    denied=_require_edit(request)
    if denied:return denied
    data=_json_body(request)
    if data is None:return _error('Invalid JSON body.')
    row=BankSyncTransaction.objects.select_related('suggested_category').filter(pk=pk,bank_account__connection__household=request.mobile_household).first()
    if not row:return _error('Bank transaction not found.',404,'not_found')
    category=None
    raw=data.get('category_id')
    if raw not in (None,''):
        expected_kind=Category.Kind.FUNDING if row.amount>0 else Category.Kind.EXPENSE
        try: category=Category.objects.get(pk=int(raw),household=request.mobile_household,is_active=True,kind=expected_kind)
        except (ValueError,TypeError,Category.DoesNotExist): return _error('Invalid category for this transaction direction.')
    match=None
    raw_match=data.get('match_id')
    if raw_match not in (None,''):
        try: match=BankSyncTransaction.objects.get(pk=int(raw_match),bank_account__connection__household=request.mobile_household,status=BankSyncTransaction.Status.PENDING)
        except (ValueError,TypeError,BankSyncTransaction.DoesNotExist): return _error('Invalid transfer match.')
    try:
        tx=import_bank_row(row,user=request.mobile_user,category=category,match=match)
        return _ok(row=_bank_row_json(row),transaction=_transaction_json(tx) if tx else None)
    except Exception as exc:
        return _error(str(exc),409,'import_failed')


@mobile_auth
def transactions(request):
    h=request.mobile_household; q=str(request.GET.get('q','')).strip(); kind=request.GET.get('kind',''); month=_parse_month(request.GET.get('month'),None)
    qs=h.transactions.prefetch_related('entries__account','budget_impacts__category','tags').annotate(filter_amount=Max(Abs('entries__base_amount')))
    for word in q.split():
        qs=qs.filter(Q(description__icontains=word)|Q(payee__icontains=word)|Q(notes__icontains=word)|Q(entries__account__name__icontains=word)|Q(budget_impacts__category__name__icontains=word))
    if kind in dict(Transaction.Kind.choices):qs=qs.filter(kind=kind)
    if month:qs=qs.filter(budget_impacts__budget_date__gte=month,budget_impacts__budget_date__lt=add_months(month,1))
    try: page_size=max(20,min(100,int(request.GET.get('page_size','50'))))
    except ValueError:page_size=50
    paginator=Paginator(qs.order_by('-date','-id').distinct(),page_size); page=paginator.get_page(request.GET.get('page','1'))
    return _ok(count=paginator.count,page=page.number,pages=paginator.num_pages,rows=[_transaction_json(x) for x in page.object_list])


@mobile_auth
def transaction_detail(request,pk):
    tx=request.mobile_household.transactions.prefetch_related('entries__account','budget_impacts__category','tags').filter(pk=pk).first()
    if not tx:return _error('Transaction not found.',404,'not_found')
    return _ok(transaction=_transaction_json(tx))


@mobile_auth
def reimbursement_candidates(request,reimbursement_id):
    h=request.mobile_household; reimbursement=h.transactions.filter(pk=reimbursement_id).first()
    if not reimbursement:return _error('Transaction not found.',404,'not_found')
    amount=reimbursement.base_amount
    rows=[]
    qs=h.transactions.filter(date__lte=reimbursement.date).exclude(pk=reimbursement.pk).prefetch_related('budget_impacts__category').order_by('-date','-id')[:100]
    for tx in qs:
        if ReimbursementLink.objects.filter(Q(purchase=tx)|Q(reimbursement=tx)).exists():continue
        expense=sum((i.amount for i in tx.budget_impacts.all() if i.kind==BudgetImpact.Kind.EXPENSE),ZERO)
        if expense>=amount and expense>0:
            rows.append({'id':tx.id,'date':tx.date.isoformat(),'description':tx.description,'amount':_money(tx.base_amount),'expense_impact':_money(expense)})
        if len(rows)>=20:break
    return _ok(rows=rows)


@mobile_auth
@require_http_methods(['POST'])
def reimbursement_pair(request):
    denied=_require_edit(request)
    if denied:return denied
    data=_json_body(request)
    try:
        purchase=request.mobile_household.transactions.get(pk=int(data.get('purchase_id')))
        reimbursement=request.mobile_household.transactions.get(pk=int(data.get('reimbursement_id')))
        link=pair_reimbursement(purchase=purchase,reimbursement=reimbursement,user=request.mobile_user)
    except Exception as exc:
        return _error(str(exc),409,'pair_failed')
    return _ok(link={'id':link.id,'purchase_id':link.purchase_id,'reimbursement_id':link.reimbursement_id,'amount':_money(link.amount)})


@mobile_auth
@require_http_methods(['POST'])
def reimbursement_unpair(request,pk):
    denied=_require_edit(request)
    if denied:return denied
    link=ReimbursementLink.objects.filter(pk=pk,household=request.mobile_household).first()
    if not link:return _error('Reimbursement pair not found.',404,'not_found')
    try:unpair_reimbursement(link,user=request.mobile_user)
    except Exception as exc:return _error(str(exc),409,'unpair_failed')
    return _ok()


@mobile_auth
def reports(request):
    h=request.mobile_household; end=active_budget_month(h); period=request.GET.get('period','6m')
    if period=='3m':start=add_months(end,-2)
    elif period=='12m':start=add_months(end,-11)
    elif period=='ytd':start=date(end.year,1,1)
    else:start=add_months(end,-5);period='6m'
    if request.GET.get('from') or request.GET.get('to'):
        try:
            start=datetime.strptime(request.GET.get('from') or start.isoformat(),'%Y-%m-%d').date().replace(day=1)
            end=datetime.strptime(request.GET.get('to') or end.isoformat(),'%Y-%m-%d').date().replace(day=1); period='custom'
        except ValueError:return _error('Invalid report date.')
    series=monthly_series(h,start,end); cats=category_expenses(h,start,add_months(end,1)-timedelta(days=1))
    total_funding=sum((x['funding'] for x in series),ZERO); total_expenses=sum((x['expenses'] for x in series),ZERO); total_savings=sum((x['savings'] for x in series),ZERO)
    return _ok(period=period,from_month=start.strftime('%Y-%m'),to_month=end.strftime('%Y-%m'),summary={'funding':_money(total_funding),'expenses':_money(total_expenses),'savings':_money(total_savings),'surplus':_money(total_funding-total_expenses-total_savings)},
               months=[{'month':x['month'].strftime('%Y-%m'),'funding':_money(x['funding']),'expenses':_money(x['expenses']),'savings':_money(x['savings']),'surplus':_money(x['surplus']),'net_worth':_money(x['net_worth'])} for x in series],
               categories=[{'name':x['category__name'],'amount':_money(x['total'])} for x in cats])


@mobile_auth
def banks(request):
    h=request.mobile_household
    rows=[]
    for c in h.bank_connections.exclude(status=BankConnection.Status.CLOSED).order_by('aspsp_name','id'):
        rows.append({'id':c.id,'name':c.aspsp_name,'status':c.status,'consent_valid_until':c.valid_until.isoformat() if c.valid_until else None,
                     'last_sync_at':c.last_synced_at.isoformat() if c.last_synced_at else None,'next_background_sync_at':next_background_sync_at(c).isoformat() if next_background_sync_at(c) else None,
                     'last_sync_issue':describe_sync_issue(c) if c.last_error else None})
    return _ok(connections=rows)


@mobile_auth
@require_http_methods(['POST'])
def bank_sync(request,pk):
    denied=_require_edit(request)
    if denied:return denied
    c=BankConnection.objects.filter(pk=pk,household=request.mobile_household).first()
    if not c:return _error('Bank connection not found.',404,'not_found')
    try:
        count=sync_connection(c,psu_headers=psu_headers_from_request(request))
        return _ok(new_review_items=count)
    except BankRateLimitError:
        return _error('The bank is rate-limiting this refresh. Automatic sync will retry later.',429,'bank_rate_limit')
    except Exception as exc:return _error(str(exc),502,'bank_sync_failed')
