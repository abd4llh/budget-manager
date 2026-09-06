import csv, json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Max, Q, Sum
from django.db.models.functions import Abs
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import (AccountForm, AccountOwnerForm, CategoryForm, HouseholdSetupForm, ImportMappingForm, ImportRuleForm, ImportUploadForm,
                    BankAccountMappingForm, BankInboxImportForm, LoanForm, MemberCreateForm, MemberRoleForm, ReconciliationForm, ReimbursementPairForm, RecurringRuleForm, SavingsGoalForm,
                    SettingsForm, TransactionForm)
from .importers import commit_batch, detect_headers, parse_batch, save_profile_from_mapping
from .models import (Account, AccountOwner, Attachment, AuditLog, BankAuthorizationAttempt, BankConnection, BankLinkedAccount, BankSyncTransaction,
                     BudgetImpact, BudgetPlan, Category, Household, HouseholdSettings, ImportBatch, ImportProfile, ImportRow, ImportRule, LoanProfile,
                     Membership, Reconciliation, ReimbursementLink, RecurringRule, SavingsGoal, Tag, Transaction, ZERO)
from .permissions import current_household, get_membership, require_role
from .reports import category_expenses, monthly_series
from .seed import seed_household
from .services import (account_balance_at, active_budget_month, create_transaction, effective_budget_date, loan_schedule, log_action, net_worth_at, pair_reimbursement, recalculate_budget_cycle,
                       process_recurring_rule, savings_goal_progress, unpair_reimbursement, update_transaction)
from .utils import add_months, month_start
from .bank_sync import (BankRateLimitError, BankSyncError, EnableBankingClient, bank_sync_config, describe_sync_issue,
                        import_bank_row, next_background_sync_at, psu_headers_from_request, store_authorized_session,
                        sync_connection, transfer_candidates)
from .version import get_version
from .categorization import load_categorization_history, refresh_bank_row_suggestion

EDIT_ROLES=(Membership.Role.OWNER,Membership.Role.MEMBER)

def _month(request, household=None):
    h=household or _household(request)
    base=active_budget_month(h) if h else date.today().replace(day=1)
    try:
        return month_start(int(request.GET.get('year',base.year)),int(request.GET.get('month',base.month)))
    except Exception:
        return base

def _household(request): return current_household(request.user)
def _safe_next(request, fallback='bank_inbox'):
    target=request.POST.get('next','')
    if target and url_has_allowed_host_and_scheme(target,allowed_hosts={request.get_host()},require_https=request.is_secure()):
        return redirect(target)
    return redirect(fallback)
def _ensure(request):
    h=_household(request)
    return h

def _system_owner(household):
    owner=household.account_owners.filter(kind=AccountOwner.Kind.SYSTEM).order_by('sort_order','id').first()
    if owner: return owner
    return AccountOwner.objects.create(household=household,name='System / External',kind=AccountOwner.Kind.SYSTEM,sort_order=900)

def _owner_for_user(household,user):
    owner=household.account_owners.filter(linked_user=user).first()
    if owner: return owner
    label=(user.get_full_name() or user.username).strip() or user.username
    existing=household.account_owners.filter(kind=AccountOwner.Kind.PERSON,name__iexact=label).first()
    if existing and existing.linked_user_id is None:
        existing.linked_user=user; existing.save(update_fields=['linked_user']); return existing
    base=label; n=2
    while household.account_owners.filter(name__iexact=label).exists():
        label=f'{base} ({n})'; n+=1
    return AccountOwner.objects.create(household=household,name=label,kind=AccountOwner.Kind.PERSON,linked_user=user,sort_order=10)

def _money(raw):
    if raw is None or str(raw).strip()=='': return ZERO
    return Decimal(str(raw).strip().replace(',','.'))


def _filter_words(qs, raw, fields):
    words=[w for w in str(raw or '').strip().split() if w]
    for word in words:
        clause=Q()
        for field in fields: clause |= Q(**{f'{field}__icontains':word})
        qs=qs.filter(clause)
    return qs

def _parse_filter_date(raw):
    if not raw: return None
    try: return datetime.strptime(raw,'%Y-%m-%d').date()
    except (TypeError,ValueError): return None

def _parse_filter_month(raw):
    if not raw: return None
    try: return datetime.strptime(raw,'%Y-%m').date().replace(day=1)
    except (TypeError,ValueError): return None

def _parse_filter_money(raw):
    if raw is None or str(raw).strip()=='': return None
    try: return abs(_money(raw))
    except Exception: return None

def _query_without_page(request):
    q=request.GET.copy(); q.pop('page',None); return q.urlencode()

def _paginate(request, qs_or_list, default=100, allowed=(50,100,250)):
    try: size=int(request.GET.get('page_size',default))
    except (TypeError,ValueError): size=default
    if size not in allowed: size=default
    paginator=Paginator(qs_or_list,size)
    page=paginator.get_page(request.GET.get('page') or 1)
    return page,size,_query_without_page(request)

def _impact_rows_from_post(request,household,default_amount=None):
    kinds=request.POST.getlist('impact_kind'); cats=request.POST.getlist('impact_category'); amounts=request.POST.getlist('impact_amount'); dates=request.POST.getlist('impact_date'); memos=request.POST.getlist('impact_memo')
    impacts=[]
    for i,kind in enumerate(kinds):
        if not kind: continue
        try:
            cat=Category.objects.get(pk=int(cats[i]),household=household,is_active=True)
            amount=_money(amounts[i]) if i<len(amounts) and str(amounts[i]).strip() else (default_amount or ZERO)
            bd=datetime.strptime(dates[i],'%Y-%m-%d').date() if i<len(dates) and dates[i] else None
            impacts.append({'kind':kind,'category':cat,'amount':amount,'budget_date':bd,'memo':memos[i] if i<len(memos) else ''})
        except Exception as e: raise ValueError(f'Invalid budget split row {i+1}: {e}')
    return impacts

def _impact_initial(tx=None):
    if not tx:return [{'kind':'','category_id':'','amount':'','budget_date':'','memo':''}]
    rows=[]
    for x in tx.budget_impacts.select_related('category'):
        rows.append({'kind':x.kind,'category_id':x.category_id,'amount':x.amount,'budget_date':x.budget_date.isoformat() if x.budget_date_locked else '','memo':x.memo})
    return rows or [{'kind':'','category_id':'','amount':'','budget_date':'','memo':''}]

@login_required
def setup_household(request):
    if _household(request): return redirect('dashboard')
    form=HouseholdSetupForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        with db_transaction.atomic():
            h=Household.objects.create(name=form.cleaned_data['household_name'])
            Membership.objects.create(household=h,user=request.user,role=Membership.Role.OWNER)
            HouseholdSettings.objects.create(household=h)
            seed_household(h,form.cleaned_data['load_existing_plan'])
        messages.success(request,'Budget Manager is ready. Add opening balances to your accounts, then begin tracking.')
        return redirect('dashboard')
    return render(request,'finance/setup.html',{'form':form})

@login_required
def dashboard(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    m=_month(request,h); nxt=add_months(m,1)
    actual=BudgetImpact.objects.filter(transaction__household=h,budget_date__gte=m,budget_date__lt=nxt).values('kind','category_id','category__name').annotate(total=Sum('amount'))
    planned=BudgetPlan.objects.filter(household=h,month=m).values('category__kind','category_id','category__name').annotate(total=Sum('amount'))
    ak=defaultdict(lambda:ZERO); ac={}; pk=defaultdict(lambda:ZERO); pc={}
    for r in actual: ak[r['kind']]+=r['total'] or ZERO; ac[r['category_id']]=r['total'] or ZERO
    for r in planned: pk[r['category__kind']]+=r['total'] or ZERO; pc[r['category_id']]=r['total'] or ZERO
    funding,expenses,savings=ak['funding'],ak['expense'],ak['savings']; remaining=funding-expenses-savings
    settings,_=HouseholdSettings.objects.get_or_create(household=h)
    savings_rate=ZERO
    if funding:
        savings_rate=((funding-expenses)/funding*100) if settings.savings_rate_basis==HouseholdSettings.SavingsRateBasis.SURPLUS else (savings/funding*100)
    category_rows=[]
    for c in h.categories.filter(is_active=True):
        p=pc.get(c.id,ZERO); a=ac.get(c.id,ZERO)
        if p or a:
            pct=(a/p*100) if p else None
            category_rows.append({'category':c,'planned':p,'actual':a,'variance':p-a,'pct':pct,'progress_width':min(pct,Decimal('100')) if pct is not None else ZERO})
    goals=[]
    for g in h.savings_goals.filter(is_active=True)[:6]:
        current=savings_goal_progress(g); goals.append({'goal':g,'current':current,'pct':min(Decimal('100'),current/g.target_amount*100) if g.target_amount else ZERO})
    upcoming=h.recurring_rules.filter(is_active=True).order_by('next_run')[:6]
    active_accounts=list(h.accounts.filter(is_active=True).select_related('owner','owner__linked_user').prefetch_related('owners__linked_user').order_by('purpose','account_type','name'))
    for a in active_accounts:
        selected=list(a.owners.all())
        a.owner_labels=['You' if o.linked_user_id==request.user.id else o.name for o in selected] if selected else (['You' if a.owner and a.owner.linked_user_id==request.user.id else a.owner.name] if a.owner else [])
    planned_remaining=pk['funding']-pk['expense']-pk['savings']
    has_financial_activity=h.transactions.exists() or any((a.opening_balance or ZERO)!=ZERO for a in active_accounts)
    ctx={'month':m,'prev_month':add_months(m,-1),'next_month':nxt,'funding':funding,'expenses':expenses,'savings':savings,'remaining':remaining,'savings_rate':savings_rate,
         'planned_funding':pk['funding'],'planned_expenses':pk['expense'],'planned_savings':pk['savings'],'planned_remaining':planned_remaining,
         'net_worth':net_worth_at(h),'category_rows':category_rows,'accounts':active_accounts,'has_financial_activity':has_financial_activity,
         'goals':goals,'upcoming':upcoming,'recent':h.transactions.prefetch_related('entries__account')[:8]}
    return render(request,'finance/dashboard.html',ctx)

@login_required
def transactions(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    qs=h.transactions.prefetch_related('entries__account','budget_impacts__category','tags').annotate(filter_amount=Max(Abs('entries__base_amount')))
    f=request.GET
    q=f.get('q','').strip(); kind=f.get('kind',''); status=f.get('status',''); account=f.get('account',''); category=f.get('category',''); tag=f.get('tag',''); sort=f.get('sort','newest')
    qs=_filter_words(qs,q,['description','payee','notes','external_id','entries__account__name','budget_impacts__category__name','tags__name'])
    if kind in dict(Transaction.Kind.choices): qs=qs.filter(kind=kind)
    if status in dict(Transaction.Status.choices): qs=qs.filter(status=status)
    if account.isdigit(): qs=qs.filter(entries__account_id=int(account))
    if category.isdigit(): qs=qs.filter(budget_impacts__category_id=int(category))
    if tag.isdigit(): qs=qs.filter(tags__id=int(tag))
    exact=_parse_filter_date(f.get('exact_date')); start=_parse_filter_date(f.get('date_from')); end=_parse_filter_date(f.get('date_to')); bm=_parse_filter_month(f.get('budget_month'))
    if exact: qs=qs.filter(date=exact)
    else:
        if start: qs=qs.filter(date__gte=start)
        if end: qs=qs.filter(date__lte=end)
    if bm: qs=qs.filter(budget_impacts__budget_date__gte=bm,budget_impacts__budget_date__lt=add_months(bm,1))
    exact_amt=_parse_filter_money(f.get('amount_exact')); amin=_parse_filter_money(f.get('amount_min')); amax=_parse_filter_money(f.get('amount_max'))
    if exact_amt is not None: qs=qs.filter(filter_amount=exact_amt)
    else:
        if amin is not None: qs=qs.filter(filter_amount__gte=amin)
        if amax is not None: qs=qs.filter(filter_amount__lte=amax)
    ordering={'newest':('-date','-id'),'oldest':('date','id'),'amount_high':('-filter_amount','-date'),'amount_low':('filter_amount','-date'),'description':('description','-date')}.get(sort,('-date','-id'))
    qs=qs.order_by(*ordering).distinct()
    page,page_size,query_without_page=_paginate(request,qs,100)
    return render(request,'finance/transactions.html',{'transactions':page.object_list,'page_obj':page,'page_size':page_size,'query_without_page':query_without_page,'kinds':Transaction.Kind.choices,'statuses':Transaction.Status.choices,'accounts':h.accounts.filter(is_active=True),'categories':h.categories.filter(is_active=True),'tags':h.tags.all(),'filters':f,'sort_filter':sort})

@login_required
@require_role(*EDIT_ROLES)
def transaction_add(request):
    h=request.household
    main=h.accounts.filter(is_active=True,account_type=Account.Type.CHECKING).first() or h.accounts.filter(is_active=True).exclude(account_type=Account.Type.EXTERNAL).first(); outside=h.accounts.filter(account_type=Account.Type.EXTERNAL,name='Outside world').first()
    form=TransactionForm(request.POST or None,request.FILES or None,household=h,initial={'from_account':main,'to_account':outside,'kind':Transaction.Kind.EXPENSE})
    impacts=[{'kind':'expense','category_id':'','amount':'','budget_date':'','memo':''}]
    if request.method=='POST' and form.is_valid():
        try:
            impacts=_impact_rows_from_post(request,h,form.cleaned_data.get('base_value') or form.cleaned_data['amount'])
            tx=create_transaction(household=h,user=request.user,posted_date=form.cleaned_data['date'],kind=form.cleaned_data['kind'],status=form.cleaned_data['status'],description=form.cleaned_data['description'],payee=form.cleaned_data['payee'],from_account=form.cleaned_data['from_account'],to_account=form.cleaned_data['to_account'],amount=form.cleaned_data['amount'],destination_amount=form.cleaned_data['destination_amount'],base_value=form.cleaned_data['base_value'],impacts=impacts,notes=form.cleaned_data['notes'],tags=form.cleaned_data['tags'])
            if form.cleaned_data.get('attachment'): Attachment.objects.create(transaction=tx,file=form.cleaned_data['attachment'])
            messages.success(request,'Transaction saved.'); return redirect('transactions')
        except Exception as e: messages.error(request,str(e))
    elif request.method=='POST': impacts=[]
    categories=list(h.categories.filter(is_active=True).values('id','name','kind'))
    account_meta=list(h.accounts.filter(is_active=True).values('id','name','account_type','purpose'))
    return render(request,'finance/transaction_form.html',{'form':form,'impact_rows':impacts,'categories_data':categories,'accounts_data':account_meta,'title':'Add transaction'})

@login_required
@require_role(*EDIT_ROLES)
def transaction_edit(request,pk):
    h=request.household; tx=get_object_or_404(Transaction,pk=pk,household=h); entries=list(tx.entries.all()[:2])
    linked_purchase=ReimbursementLink.objects.filter(purchase=tx).select_related('reimbursement').first()
    linked_reimbursement=ReimbursementLink.objects.filter(reimbursement=tx).select_related('purchase').first()
    if request.method=='POST' and (linked_purchase or linked_reimbursement):
        messages.error(request,'Unpair the reimbursement before editing either linked transaction.')
        return redirect('transaction_edit',pk=tx.pk)
    initial={'date':tx.date,'kind':tx.kind,'status':tx.status,'description':tx.description,'payee':tx.payee,'notes':tx.notes,'tags':tx.tags.all()}
    if len(entries)>=2:
        initial.update({'from_account':entries[0].account,'to_account':entries[1].account,'amount':abs(entries[0].amount),'destination_amount':abs(entries[1].amount) if abs(entries[1].amount)!=abs(entries[0].amount) else None,'base_value':abs(entries[0].base_amount)})
    form=TransactionForm(request.POST or None,request.FILES or None,household=h,initial=initial)
    impacts=_impact_initial(tx)
    if request.method=='POST' and form.is_valid():
        try:
            impacts=_impact_rows_from_post(request,h,form.cleaned_data.get('base_value') or form.cleaned_data['amount'])
            update_transaction(tx,user=request.user,posted_date=form.cleaned_data['date'],kind=form.cleaned_data['kind'],status=form.cleaned_data['status'],description=form.cleaned_data['description'],payee=form.cleaned_data['payee'],from_account=form.cleaned_data['from_account'],to_account=form.cleaned_data['to_account'],amount=form.cleaned_data['amount'],destination_amount=form.cleaned_data['destination_amount'],base_value=form.cleaned_data['base_value'],impacts=impacts,notes=form.cleaned_data['notes'],tags=form.cleaned_data['tags'])
            if form.cleaned_data.get('attachment'): Attachment.objects.create(transaction=tx,file=form.cleaned_data['attachment'])
            messages.success(request,'Transaction updated.'); return redirect('transactions')
        except Exception as e: messages.error(request,str(e))
    categories=list(h.categories.filter(is_active=True).values('id','name','kind'))
    account_meta=list(h.accounts.filter(is_active=True).values('id','name','account_type','purpose'))
    can_pair=(not linked_purchase and not linked_reimbursement
              and tx.entries.filter(amount__lt=0,account__purpose=Account.Purpose.EXTERNAL).exists()
              and tx.entries.filter(amount__gt=0,account__purpose__in=[Account.Purpose.AVAILABLE,Account.Purpose.SAVINGS]).exists())
    return render(request,'finance/transaction_form.html',{'form':form,'impact_rows':impacts,'categories_data':categories,'accounts_data':account_meta,'title':'Edit transaction','transaction':tx,'can_pair_reimbursement':can_pair,'reimbursement_link':linked_purchase or linked_reimbursement,'is_reimbursement_side':bool(linked_reimbursement)})

@login_required
@require_role(*EDIT_ROLES)
@require_POST
def transaction_delete(request,pk):
    tx=get_object_or_404(Transaction,pk=pk,household=request.household); summary=tx.description
    if ReimbursementLink.objects.filter(Q(purchase=tx)|Q(reimbursement=tx)).exists():
        messages.error(request,'Unpair the reimbursement before deleting either linked transaction.'); return redirect('transaction_edit',pk=tx.pk)
    for attachment in tx.attachments.all():
        try: attachment.file.delete(save=False)
        except Exception: pass
    log_action(request.household,request.user,'delete','transaction',tx,f'Deleted {summary}'); tx.delete(); messages.success(request,'Transaction deleted.'); return redirect('transactions')

@login_required
@require_role(*EDIT_ROLES)
def reimbursement_pair(request,pk):
    h=request.household
    reimbursement=get_object_or_404(Transaction,pk=pk,household=h)
    if ReimbursementLink.objects.filter(reimbursement=reimbursement).exists() or ReimbursementLink.objects.filter(purchase=reimbursement).exists():
        messages.info(request,'This transaction is already part of a reimbursement pair.'); return redirect('transaction_edit',pk=pk)
    form=ReimbursementPairForm(request.POST or None,household=h,reimbursement=reimbursement)
    if request.method=='POST' and form.is_valid():
        try:
            link=pair_reimbursement(purchase=form.cleaned_data['purchase'],reimbursement=reimbursement,user=request.user)
            messages.success(request,f'Paired reimbursement. {link.amount:.2f} {h.settings.base_currency} was removed from the purchase expense, so only your share remains in the budget.')
            return redirect('transaction_edit',pk=reimbursement.pk)
        except Exception as exc: messages.error(request,str(exc))
    return render(request,'finance/reimbursement_pair.html',{'form':form,'reimbursement':reimbursement,'amount':reimbursement.base_amount})

@login_required
@require_role(*EDIT_ROLES)
@require_POST
def reimbursement_unpair(request,pk):
    link=get_object_or_404(ReimbursementLink,pk=pk,household=request.household)
    reimbursement=link.reimbursement
    try:
        unpair_reimbursement(link,user=request.user); messages.success(request,'Reimbursement pair removed and the original budget classifications were restored.')
    except Exception as exc: messages.error(request,f'Could not unpair reimbursement: {exc}')
    return redirect('transaction_edit',pk=reimbursement.pk)

@login_required
def accounts(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    f=request.GET; q=f.get('q','').strip(); owner=f.get('owner',''); purpose=f.get('purpose',''); account_type=f.get('type',''); currency=f.get('currency',''); state=f.get('state','active'); networth=f.get('networth',''); sort=f.get('sort','purpose')
    qs=h.accounts.all().select_related('owner','owner__linked_user','release_destination').prefetch_related('owners__linked_user')
    qs=_filter_words(qs,q,['name','institution','owner__name','owners__name','notes'])
    if owner.isdigit(): qs=qs.filter(Q(owners__id=int(owner))|Q(owner_id=int(owner))).distinct()
    if purpose in dict(Account.Purpose.choices): qs=qs.filter(purpose=purpose)
    if account_type in dict(Account.Type.choices): qs=qs.filter(account_type=account_type)
    if currency: qs=qs.filter(currency__iexact=currency.strip())
    if state=='active': qs=qs.filter(is_active=True)
    elif state=='archived': qs=qs.filter(is_active=False)
    if networth=='yes': qs=qs.filter(include_in_net_worth=True)
    elif networth=='no': qs=qs.filter(include_in_net_worth=False)
    accounts_qs=list(qs)
    bmin=_parse_filter_money(f.get('balance_min')); bmax=_parse_filter_money(f.get('balance_max'))
    if bmin is not None: accounts_qs=[a for a in accounts_qs if abs(a.display_base_balance)>=bmin]
    if bmax is not None: accounts_qs=[a for a in accounts_qs if abs(a.display_base_balance)<=bmax]
    for a in accounts_qs:
        selected=list(a.owners.all())
        if selected:
            a.owner_labels=['You' if o.linked_user_id==request.user.id else o.name for o in selected]
        elif a.owner:
            a.owner_labels=['You' if a.owner.linked_user_id==request.user.id else a.owner.name]
        else:
            a.owner_labels=['Unassigned']
    if sort=='name': accounts_qs.sort(key=lambda a:a.name.casefold())
    elif sort=='balance_high': accounts_qs.sort(key=lambda a:a.display_base_balance,reverse=True)
    elif sort=='balance_low': accounts_qs.sort(key=lambda a:a.display_base_balance)
    else: accounts_qs.sort(key=lambda a:(a.purpose,a.account_type,a.name.casefold()))
    groups=[]
    group_defs=[(Account.Purpose.AVAILABLE,'Available money','Everyday checking and cash you can use now.'),(Account.Purpose.SAVINGS,'Savings','Money deliberately set aside, including joint savings.'),(Account.Purpose.RESTRICTED,'Restricted funds','Money you own but cannot freely spend yet.'),(Account.Purpose.INVESTMENT,'Investments & long-term assets','Long-term holdings and assets.'),(Account.Purpose.LIABILITY,'Liabilities','Credit cards and loans.'),(Account.Purpose.EXTERNAL,'System / external','Bookkeeping destinations outside household net worth.')]
    for key,label,description in group_defs:
        rows=[a for a in accounts_qs if a.purpose==key]
        if rows: groups.append({'key':key,'label':label,'description':description,'accounts':rows})
    all_accounts=list(h.accounts.all().select_related('owner','owner__linked_user').prefetch_related('owners__linked_user'))
    owner_totals=[]
    for own in h.account_owners.filter(is_active=True).exclude(kind=AccountOwner.Kind.SYSTEM).order_by('sort_order','name'):
        owned=[a for a in all_accounts if a.owner_id==own.id and a.is_active and a.include_in_net_worth]
        total=sum((a.base_balance for a in owned),ZERO); label='You' if own.linked_user_id==request.user.id else own.name
        owner_totals.append({'owner':own,'label':label,'total':total,'count':len(owned)})
    return render(request,'finance/accounts.html',{'account_groups':groups,'owners':h.account_owners.filter(is_active=True),'owner_totals':owner_totals,'filters':f,'purposes':Account.Purpose.choices,'account_types':Account.Type.choices,'matching_count':len(accounts_qs),'sort_filter':sort})

@login_required
@require_role(*EDIT_ROLES)
def account_form(request,pk=None):
    h=request.household; obj=get_object_or_404(Account,pk=pk,household=h) if pk else None
    initial={}
    if not obj:
        initial['owners']=[_owner_for_user(h,request.user).pk]
        initial['currency']=h.settings.base_currency
    form=AccountForm(request.POST or None,instance=obj,household=h,initial=initial)
    if request.method=='POST' and form.is_valid():
        a=form.save(commit=False); a.household=h; a.save(); form.apply_owners(a); log_action(h,request.user,'update' if obj else 'create','account',a,a.name); messages.success(request,'Account saved.'); return redirect('accounts')
    return render(request,'finance/account_form.html',{'form':form,'account':obj,'title':'Edit account' if obj else 'Add account','back_url':reverse('accounts')})

@login_required
def account_owners(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    owners=h.account_owners.select_related('linked_user').all().order_by('sort_order','name')
    rows=[]
    for owner in owners:
        account_count=h.accounts.filter(Q(owner=owner)|Q(owners=owner)).distinct().count()
        rows.append({'owner':owner,'account_count':account_count,'label':'You' if owner.linked_user_id==request.user.id else owner.name})
    return render(request,'finance/account_owners.html',{'owner_rows':rows})

@login_required
@require_role(*EDIT_ROLES)
def account_owner_form(request,pk=None):
    h=request.household; obj=get_object_or_404(AccountOwner,pk=pk,household=h) if pk else None
    form=AccountOwnerForm(request.POST or None,instance=obj,household=h)
    if request.method=='POST' and form.is_valid():
        owner=form.save(commit=False); owner.household=h; owner.save(); log_action(h,request.user,'update' if obj else 'create','account_owner',owner,owner.name); messages.success(request,'Account owner saved.'); return redirect('account_owners')
    return render(request,'finance/form_page.html',{'form':form,'title':'Edit account owner' if obj else 'Add account owner','back_url':reverse('account_owners'),'help_text':'Owners describe who an account belongs to. They are separate from login permissions.'})

@login_required
@require_role(*EDIT_ROLES)
def reconcile_account(request,pk):
    h=request.household; a=get_object_or_404(Account,pk=pk,household=h); form=ReconciliationForm(request.POST or None,initial={'statement_date':date.today()})
    if request.method=='POST' and form.is_valid():
        sd=form.cleaned_data['statement_date']; displayed_statement=form.cleaned_data['statement_balance']; signed_target=-displayed_statement if a.is_liability else displayed_statement
        current=account_balance_at(a,sd); diff=signed_target-current
        wants_adjustment=form.cleaned_data['create_adjustment'] and bool(diff)
        base_value=form.cleaned_data.get('adjustment_base_value')
        if wants_adjustment and a.currency!=h.settings.base_currency and not base_value:
            form.add_error('adjustment_base_value',f'Enter the {h.settings.base_currency} value of this foreign-currency adjustment.')
        else:
            with db_transaction.atomic():
                Reconciliation.objects.create(account=a,statement_date=sd,statement_balance=displayed_statement,book_balance=(-current if a.is_liability else current),difference=(-diff if a.is_liability else diff),note=form.cleaned_data['note'],reconciled_by=request.user)
                if wants_adjustment:
                    outside=h.accounts.filter(account_type=Account.Type.EXTERNAL,name='Outside world').first() or h.accounts.filter(account_type=Account.Type.EXTERNAL).first()
                    if not outside: outside=Account.objects.create(household=h,name='Outside world',account_type=Account.Type.EXTERNAL,purpose=Account.Purpose.EXTERNAL,owner=_system_owner(h),include_in_net_worth=False,currency=h.settings.base_currency)
                    from_acc,to_acc=(outside,a) if diff>0 else (a,outside)
                    if a.currency==h.settings.base_currency:
                        source_amount=destination_amount=abs(diff); tx_base_value=None
                    elif diff>0:
                        source_amount=base_value; destination_amount=abs(diff); tx_base_value=base_value
                    else:
                        source_amount=abs(diff); destination_amount=base_value; tx_base_value=base_value
                    create_transaction(household=h,user=request.user,posted_date=sd,kind=Transaction.Kind.ADJUSTMENT,description=f'Reconciliation adjustment — {a.name}',from_account=from_acc,to_account=to_acc,amount=source_amount,destination_amount=destination_amount,base_value=tx_base_value,impacts=[],notes=form.cleaned_data['note'])
            messages.success(request,'Reconciliation recorded.'); return redirect('accounts')
    return render(request,'finance/form_page.html',{'form':form,'title':f'Reconcile {a.name}','back_url':reverse('accounts'),'help_text':f'Current displayed balance: {a.display_balance:.2f} {a.currency}'})

@login_required
def categories(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    f=request.GET; q=f.get('q','').strip(); kind=f.get('kind',''); state=f.get('state','active'); parent=f.get('parent',''); usage=f.get('usage',''); sort=f.get('sort','default')
    qs=h.categories.select_related('parent').annotate(use_count=Sum('budget_impacts__amount'))
    qs=_filter_words(qs,q,['name','notes','parent__name'])
    if kind in dict(Category.Kind.choices): qs=qs.filter(kind=kind)
    if state=='active': qs=qs.filter(is_active=True)
    elif state=='inactive': qs=qs.filter(is_active=False)
    if parent=='root': qs=qs.filter(parent__isnull=True)
    elif parent.isdigit(): qs=qs.filter(parent_id=int(parent))
    if usage=='used': qs=qs.filter(budget_impacts__isnull=False)
    elif usage=='unused': qs=qs.filter(budget_impacts__isnull=True)
    ordering={'name':('name',),'kind':('kind','sort_order','name'),'default':('kind','sort_order','name')}.get(sort,('kind','sort_order','name'))
    qs=qs.order_by(*ordering).distinct()
    return render(request,'finance/categories.html',{'categories':qs,'tags':h.tags.all(),'filters':f,'kinds':Category.Kind.choices,'parents':h.categories.filter(parent__isnull=True).order_by('name'),'matching_count':qs.count(),'sort_filter':sort})

@login_required
@require_role(*EDIT_ROLES)
def category_form(request,pk=None):
    h=request.household; obj=get_object_or_404(Category,pk=pk,household=h) if pk else None
    initial={}
    requested_kind=request.GET.get('kind','')
    if not obj and requested_kind in dict(Category.Kind.choices): initial['kind']=requested_kind
    form=CategoryForm(request.POST or None,instance=obj,household=h,initial=initial)
    if request.method=='POST' and form.is_valid():
        c=form.save(commit=False); c.household=h; c.save(); messages.success(request,'Category saved.'); return redirect('categories')
    return render(request,'finance/form_page.html',{'form':form,'title':'Edit category' if obj else 'Add category','back_url':reverse('categories')})

@login_required
def budget_month(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    m=_month(request,h); cats=h.categories.filter(is_active=True); existing={x.category_id:x for x in BudgetPlan.objects.filter(household=h,month=m)}
    if request.method=='POST':
        membership=get_membership(request.user)
        if membership.role==Membership.Role.VIEWER: messages.error(request,'View-only users cannot change the budget.'); return redirect('budget_month')
        parsed=[]
        for c in cats:
            try: amt=_money(request.POST.get(f'amount_{c.id}','0'))
            except Exception: messages.error(request,f'Invalid amount for {c.name}.'); return redirect(f'{request.path}?year={m.year}&month={m.month}')
            if amt<0: messages.error(request,f'Budget amount for {c.name} cannot be negative.'); return redirect(f'{request.path}?year={m.year}&month={m.month}')
            note=request.POST.get(f'note_{c.id}','').strip()[:160]
            parsed.append((c,amt,note))
        with db_transaction.atomic():
            for c,amt,note in parsed: BudgetPlan.objects.update_or_create(household=h,month=m,category=c,defaults={'amount':amt,'note':note})
        messages.success(request,f'Budget for {m:%B %Y} saved.'); return redirect(f'{request.path}?year={m.year}&month={m.month}')
    sections=[]
    for kind,label in Category.Kind.choices:
        rows=[{'category':c,'amount':existing.get(c.id).amount if c.id in existing else ZERO,'note':existing.get(c.id).note if c.id in existing else ''} for c in cats.filter(kind=kind)]
        sections.append({'kind':kind,'label':label,'rows':rows,'total':sum((r['amount'] for r in rows),ZERO)})
    planned_funding=next((x['total'] for x in sections if x['kind']==Category.Kind.FUNDING),ZERO)
    planned_expenses=next((x['total'] for x in sections if x['kind']==Category.Kind.EXPENSE),ZERO)
    planned_savings=next((x['total'] for x in sections if x['kind']==Category.Kind.SAVINGS),ZERO)
    return render(request,'finance/budget_month.html',{'month':m,'prev_month':add_months(m,-1),'next_month':add_months(m,1),'sections':sections,
        'planned_funding':planned_funding,'planned_expenses':planned_expenses,'planned_savings':planned_savings,'planned_remaining':planned_funding-planned_expenses-planned_savings})

@login_required
@require_role(*EDIT_ROLES)
@require_POST
def budget_category_add(request):
    h=request.household
    kind=request.POST.get('kind','').strip()
    name=request.POST.get('name','').strip()
    if kind not in dict(Category.Kind.choices):
        messages.error(request,'Choose a valid budget section.')
        return redirect('budget_month')
    try:
        active=active_budget_month(h); year=int(request.POST.get('year',active.year)); month=int(request.POST.get('month',active.month)); m=month_start(year,month)
    except Exception:
        m=active_budget_month(h)
    target=f"{reverse('budget_month')}?year={m.year}&month={m.month}"
    if not name:
        messages.error(request,'Enter a name for the new budget category.')
        return redirect(target)
    try:
        amount=_money(request.POST.get('amount','0'))
    except Exception:
        messages.error(request,'The starting budget amount is invalid.')
        return redirect(target)
    if amount < 0:
        messages.error(request,'The starting budget amount cannot be negative.')
        return redirect(target)
    existing=h.categories.filter(kind=kind,name__iexact=name).first()
    with db_transaction.atomic():
        if existing:
            category=existing
            if not category.is_active:
                category.is_active=True; category.save(update_fields=['is_active'])
                action='Reactivated'
            else:
                action='Updated'
        else:
            max_order=h.categories.filter(kind=kind).aggregate(v=Max('sort_order'))['v'] or 0
            category=Category.objects.create(household=h,kind=kind,name=name,sort_order=max_order+10)
            action='Added'
        BudgetPlan.objects.update_or_create(household=h,month=m,category=category,defaults={'amount':amount,'note':request.POST.get('note','').strip()[:160]})
    messages.success(request,f'{action} {category.name} and set {m:%B} to {amount:.2f} {h.settings.base_currency}.')
    return redirect(target)

@login_required
def budget_year(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    active=active_budget_month(h)
    try: year=int(request.GET.get('year',active.year))
    except ValueError: year=active.year
    cats=list(h.categories.filter(is_active=True)); plans=BudgetPlan.objects.filter(household=h,month__year=year); lookup={(p.category_id,p.month.month):p.amount for p in plans}
    if request.method=='POST':
        if get_membership(request.user).role==Membership.Role.VIEWER: messages.error(request,'View-only users cannot change the budget.'); return redirect('budget_year')
        parsed=[]
        for c in cats:
            for mo in range(1,13):
                try: amt=_money(request.POST.get(f'b_{c.id}_{mo}','0'))
                except Exception: messages.error(request,f'Invalid amount for {c.name}, month {mo}.'); return redirect(f'{request.path}?year={year}')
                if amt<0: messages.error(request,f'Budget amount for {c.name}, month {mo}, cannot be negative.'); return redirect(f'{request.path}?year={year}')
                parsed.append((c,mo,amt))
        with db_transaction.atomic():
            for c,mo,amt in parsed: BudgetPlan.objects.update_or_create(household=h,month=date(year,mo,1),category=c,defaults={'amount':amt})
        messages.success(request,f'{year} plan saved.'); return redirect(f'{request.path}?year={year}')
    rows=[]
    for c in cats: rows.append({'category':c,'months':[lookup.get((c.id,mo),ZERO) for mo in range(1,13)],'total':sum((lookup.get((c.id,mo),ZERO) for mo in range(1,13)),ZERO)})
    month_totals=[]
    for mo in range(1,13):
        f=sum((lookup.get((c.id,mo),ZERO) for c in cats if c.kind==Category.Kind.FUNDING),ZERO); e=sum((lookup.get((c.id,mo),ZERO) for c in cats if c.kind==Category.Kind.EXPENSE),ZERO); s=sum((lookup.get((c.id,mo),ZERO) for c in cats if c.kind==Category.Kind.SAVINGS),ZERO)
        month_totals.append({'funding':f,'expense':e,'savings':s,'remaining':f-e-s})
    return render(request,'finance/budget_year.html',{'year':year,'rows':rows,'month_totals':month_totals,'months':['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']})

@login_required
@require_role(*EDIT_ROLES)
@require_POST
def budget_copy_year(request):
    h=request.household; active=active_budget_month(h); year=int(request.POST.get('year',active.year)); source=year-1
    for p in BudgetPlan.objects.filter(household=h,month__year=source): BudgetPlan.objects.update_or_create(household=h,month=p.month.replace(year=year),category=p.category,defaults={'amount':p.amount,'note':p.note})
    messages.success(request,f'Copied {source} into {year}.'); return redirect(f"{reverse('budget_year')}?year={year}")

@login_required
def recurring_list(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    f=request.GET; q=f.get('q','').strip(); kind=f.get('kind',''); frequency=f.get('frequency',''); state=f.get('state','active'); mode=f.get('mode',''); account=f.get('account',''); sort=f.get('sort','next')
    qs=h.recurring_rules.select_related('from_account','to_account').all(); qs=_filter_words(qs,q,['name','description','notes','from_account__name','to_account__name'])
    if kind in dict(Transaction.Kind.choices): qs=qs.filter(transaction_kind=kind)
    if frequency in dict(RecurringRule.Frequency.choices): qs=qs.filter(frequency=frequency)
    if state=='active': qs=qs.filter(is_active=True)
    elif state=='inactive': qs=qs.filter(is_active=False)
    if mode=='auto': qs=qs.filter(auto_post=True)
    elif mode=='manual': qs=qs.filter(auto_post=False)
    if account.isdigit(): qs=qs.filter(Q(from_account_id=int(account))|Q(to_account_id=int(account)))
    exact=_parse_filter_date(f.get('exact_date')); start=_parse_filter_date(f.get('date_from')); end=_parse_filter_date(f.get('date_to'))
    if exact: qs=qs.filter(next_run=exact)
    else:
        if start: qs=qs.filter(next_run__gte=start)
        if end: qs=qs.filter(next_run__lte=end)
    ae=_parse_filter_money(f.get('amount_exact')); amin=_parse_filter_money(f.get('amount_min')); amax=_parse_filter_money(f.get('amount_max'))
    if ae is not None: qs=qs.filter(amount=ae)
    else:
        if amin is not None: qs=qs.filter(amount__gte=amin)
        if amax is not None: qs=qs.filter(amount__lte=amax)
    ordering={'next':('next_run','name'),'name':('name',),'amount_high':('-amount','next_run'),'amount_low':('amount','next_run')}.get(sort,('next_run','name'))
    page,page_size,query_without_page=_paginate(request,qs.order_by(*ordering),100)
    return render(request,'finance/recurring.html',{'rules':page.object_list,'page_obj':page,'page_size':page_size,'query_without_page':query_without_page,'filters':f,'kinds':Transaction.Kind.choices,'frequencies':RecurringRule.Frequency.choices,'accounts':h.accounts.filter(is_active=True),'sort_filter':sort})

@login_required
@require_role(*EDIT_ROLES)
def recurring_form(request,pk=None):
    h=request.household; obj=get_object_or_404(RecurringRule,pk=pk,household=h) if pk else None; form=RecurringRuleForm(request.POST or None,instance=obj,household=h)
    if request.method=='POST' and form.is_valid():
        r=form.save(commit=False); r.household=h; r.save(); messages.success(request,'Recurring rule saved.'); return redirect('recurring')
    return render(request,'finance/form_page.html',{'form':form,'title':'Edit recurring rule' if obj else 'Add recurring rule','back_url':reverse('recurring'),'help_text':'Enable auto-post only for predictable transactions. The scheduler checks due rules hourly.'})

@login_required
@require_role(*EDIT_ROLES)
@require_POST
def recurring_run(request,pk):
    r=get_object_or_404(RecurringRule,pk=pk,household=request.household); old=r.auto_post; r.auto_post=True; generated=process_recurring_rule(r,user=request.user,today=date.today()); r.auto_post=old; r.save(update_fields=['auto_post']); messages.success(request,f'Generated {len(generated)} due transaction(s).'); return redirect('recurring')

@login_required
def goals(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    f=request.GET; q=f.get('q','').strip(); state=f.get('state','active'); account=f.get('account',''); category=f.get('category',''); sort=f.get('sort','name')
    qs=h.savings_goals.select_related('account','category').all(); qs=_filter_words(qs,q,['name','notes','account__name','category__name'])
    if state=='active': qs=qs.filter(is_active=True)
    elif state=='inactive': qs=qs.filter(is_active=False)
    if account.isdigit(): qs=qs.filter(account_id=int(account))
    if category.isdigit(): qs=qs.filter(category_id=int(category))
    td_from=_parse_filter_date(f.get('date_from')); td_to=_parse_filter_date(f.get('date_to'))
    if td_from: qs=qs.filter(target_date__gte=td_from)
    if td_to: qs=qs.filter(target_date__lte=td_to)
    tmin=_parse_filter_money(f.get('amount_min')); tmax=_parse_filter_money(f.get('amount_max'))
    if tmin is not None: qs=qs.filter(target_amount__gte=tmin)
    if tmax is not None: qs=qs.filter(target_amount__lte=tmax)
    rows=[]
    for g in qs:
        cur=savings_goal_progress(g); rows.append({'goal':g,'current':cur,'remaining':max(g.target_amount-cur,ZERO),'pct':min(Decimal('100'),cur/g.target_amount*100) if g.target_amount else ZERO})
    if sort=='target': rows.sort(key=lambda x:(x['goal'].target_date or date.max,x['goal'].name.casefold()))
    elif sort=='progress_high': rows.sort(key=lambda x:x['pct'],reverse=True)
    elif sort=='progress_low': rows.sort(key=lambda x:x['pct'])
    elif sort=='amount_high': rows.sort(key=lambda x:x['goal'].target_amount,reverse=True)
    else: rows.sort(key=lambda x:x['goal'].name.casefold())
    return render(request,'finance/goals.html',{'rows':rows,'filters':f,'accounts':h.accounts.filter(is_active=True),'categories':h.categories.filter(is_active=True,kind=Category.Kind.SAVINGS),'sort_filter':sort})

@login_required
@require_role(*EDIT_ROLES)
def goal_form(request,pk=None):
    h=request.household; obj=get_object_or_404(SavingsGoal,pk=pk,household=h) if pk else None; form=SavingsGoalForm(request.POST or None,instance=obj,household=h)
    if request.method=='POST' and form.is_valid():
        g=form.save(commit=False); g.household=h; g.save(); messages.success(request,'Savings goal saved.'); return redirect('goals')
    return render(request,'finance/form_page.html',{'form':form,'title':'Edit savings goal' if obj else 'Add savings goal','back_url':reverse('goals')})

@login_required
def loans(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    f=request.GET; q=f.get('q','').strip(); sort=f.get('sort','name'); qs=h.loans.select_related('account').filter(account__is_active=True); qs=_filter_words(qs,q,['account__name','lender','notes'])
    apr_min=_parse_filter_money(f.get('apr_min')); apr_max=_parse_filter_money(f.get('apr_max')); pay_min=_parse_filter_money(f.get('payment_min')); pay_max=_parse_filter_money(f.get('payment_max'))
    if apr_min is not None: qs=qs.filter(annual_interest_rate__gte=apr_min)
    if apr_max is not None: qs=qs.filter(annual_interest_rate__lte=apr_max)
    if pay_min is not None: qs=qs.filter(regular_payment__gte=pay_min)
    if pay_max is not None: qs=qs.filter(regular_payment__lte=pay_max)
    rows=[]
    for loan in qs:
        sched=loan_schedule(loan); rows.append({'loan':loan,'balance':loan.account.display_balance,'months':len(sched),'payoff':sched[-1]['month'] if sched and not sched[-1].get('negative_amortization') else None,'interest':sum((r['interest'] for r in sched),ZERO),'negative':bool(sched and sched[-1].get('negative_amortization'))})
    bmin=_parse_filter_money(f.get('balance_min')); bmax=_parse_filter_money(f.get('balance_max'))
    if bmin is not None: rows=[r for r in rows if abs(r['balance'])>=bmin]
    if bmax is not None: rows=[r for r in rows if abs(r['balance'])<=bmax]
    if sort=='balance_high': rows.sort(key=lambda r:abs(r['balance']),reverse=True)
    elif sort=='apr_high': rows.sort(key=lambda r:r['loan'].annual_interest_rate,reverse=True)
    elif sort=='payment_high': rows.sort(key=lambda r:r['loan'].regular_payment,reverse=True)
    elif sort=='payoff': rows.sort(key=lambda r:(r['payoff'] or date.max))
    else: rows.sort(key=lambda r:r['loan'].account.name.casefold())
    return render(request,'finance/loans.html',{'rows':rows,'filters':f,'sort_filter':sort})

@login_required
@require_role(*EDIT_ROLES)
def loan_form(request,pk=None):
    h=request.household; obj=get_object_or_404(LoanProfile,pk=pk,household=h) if pk else None; form=LoanForm(request.POST or None,instance=obj,household=h)
    if request.method=='POST' and form.is_valid():
        l=form.save(commit=False); l.household=h; l.save(); messages.success(request,'Loan saved.'); return redirect('loans')
    return render(request,'finance/form_page.html',{'form':form,'title':'Edit loan' if obj else 'Add loan','back_url':reverse('loans'),'help_text':'Create the associated Loan account first. Its displayed balance is the amount still owed.'})

@login_required
def reports(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    active=active_budget_month(h); end=active; f=request.GET; preset=f.get('preset','12m')
    presets={'3m':2,'6m':5,'12m':11,'ytd':None}
    if preset not in {*presets,'custom'}: preset='12m'
    if preset=='custom':
        start=add_months(end,-11)
        try:
            if f.get('start'): start=datetime.strptime(f['start'],'%Y-%m').date().replace(day=1)
            if f.get('end'): end=datetime.strptime(f['end'],'%Y-%m').date().replace(day=1)
        except ValueError: pass
    elif preset=='ytd':
        start=date(end.year,1,1)
    else:
        start=add_months(end,-presets[preset])
    if start>end: start,end=end,start
    series=monthly_series(h,start,end); cats=category_expenses(h,start,add_months(end,1)-timedelta(days=1)); q=f.get('q','').strip(); min_total=_parse_filter_money(f.get('min_total'))
    for word in [w for w in q.split() if w]: cats=[r for r in cats if word.casefold() in str(r['category__name']).casefold()]
    if min_total is not None: cats=[r for r in cats if (r['total'] or ZERO)>=min_total]
    max_exp=max([r['total'] for r in cats] or [Decimal('1')])
    for r in cats: r['pct']=r['total']/max_exp*100 if max_exp else ZERO
    max_month=max([max(x['funding'],x['expenses'],x['savings']) for x in series] or [Decimal('1')])
    for x in series:
        x['funding_pct']=x['funding']/max_month*100 if max_month else 0; x['expenses_pct']=x['expenses']/max_month*100 if max_month else 0; x['savings_pct']=x['savings']/max_month*100 if max_month else 0
    totals={'funding':sum((x['funding'] for x in series),ZERO),'expenses':sum((x['expenses'] for x in series),ZERO),'savings':sum((x['savings'] for x in series),ZERO)}; totals['surplus']=totals['funding']-totals['expenses']-totals['savings']; totals['savings_rate']=(totals['savings']/totals['funding']*100) if totals['funding'] else ZERO
    start_nw=net_worth_at(h,start-timedelta(days=1)); end_nw=series[-1]['net_worth'] if series else start_nw; totals['net_worth_change']=end_nw-start_nw
    return render(request,'finance/reports.html',{'series':series,'categories':cats,'start':start,'end':end,'totals':totals,'filters':f,'preset':preset,'active_month':active})

@login_required
def import_batches(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    f=request.GET; q=f.get('q','').strip(); status=f.get('status',''); account=f.get('account',''); errors=f.get('errors',''); sort=f.get('sort','newest'); qs=h.import_batches.select_related('account').all(); qs=_filter_words(qs,q,['original_filename','account__name','error_message'])
    if status in dict(ImportBatch.Status.choices): qs=qs.filter(status=status)
    if account.isdigit(): qs=qs.filter(account_id=int(account))
    if errors=='yes': qs=qs.filter(error_count__gt=0)
    elif errors=='no': qs=qs.filter(error_count=0)
    start=_parse_filter_date(f.get('date_from')); end=_parse_filter_date(f.get('date_to'))
    if start: qs=qs.filter(created_at__date__gte=start)
    if end: qs=qs.filter(created_at__date__lte=end)
    ordering={'newest':('-created_at',),'oldest':('created_at',),'file':('original_filename',)}.get(sort,('-created_at',)); page,page_size,query_without_page=_paginate(request,qs.order_by(*ordering),100)
    return render(request,'finance/imports.html',{'batches':page.object_list,'page_obj':page,'page_size':page_size,'query_without_page':query_without_page,'rules':h.import_rules.all()[:20],'filters':f,'statuses':ImportBatch.Status.choices,'accounts':h.accounts.filter(is_active=True),'sort_filter':sort})

@login_required
@require_role(*EDIT_ROLES)
def import_upload(request):
    h=request.household; form=ImportUploadForm(request.POST or None,request.FILES or None,household=h)
    if request.method=='POST' and form.is_valid():
        b=form.save(commit=False); b.household=h; b.created_by=request.user; b.original_filename=form.cleaned_data['raw_file'].name; b.save()
        headers=detect_headers(b)
        if b.profile:
            p=b.profile; mapping={'delimiter':p.delimiter,'encoding':p.encoding,'date_column':p.date_column,'description_column':p.description_column,'amount_column':p.amount_column,'debit_column':p.debit_column,'credit_column':p.credit_column,'date_format':p.date_format,'decimal_separator':p.decimal_separator,'thousands_separator':p.thousands_separator,'invert_sign':p.invert_sign}
            try: parse_batch(b,mapping); return redirect('import_review',pk=b.pk)
            except Exception as e: messages.error(request,f'Profile could not parse this file: {e}')
        return redirect('import_map',pk=b.pk)
    return render(request,'finance/form_page.html',{'form':form,'title':'Upload bank CSV','back_url':reverse('imports'),'help_text':'The file stays on your server. You will map its columns before anything is imported.'})

@login_required
@require_role(*EDIT_ROLES)
def import_map(request,pk):
    h=request.household; b=get_object_or_404(ImportBatch,pk=pk,household=h)
    if not b.headers: detect_headers(b)
    initial={'delimiter':b.mapping.get('delimiter',','),'encoding':'utf-8-sig','date_format':'%d.%m.%Y','decimal_separator':',','thousands_separator':'.'}
    form=ImportMappingForm(request.POST or None,headers=b.headers,initial=initial)
    if request.method=='POST' and form.is_valid():
        mapping={k:v for k,v in form.cleaned_data.items() if k!='save_profile_as'}
        try:
            parse_batch(b,mapping); save_profile_from_mapping(b,form.cleaned_data.get('save_profile_as')); messages.success(request,'CSV parsed. Review the rows before importing.'); return redirect('import_review',pk=b.pk)
        except Exception as e: messages.error(request,str(e))
    return render(request,'finance/import_map.html',{'form':form,'batch':b,'headers':b.headers})

@login_required
def import_review(request,pk):
    h=_ensure(request); b=get_object_or_404(ImportBatch,pk=pk,household=h); rows=b.rows.select_related('suggested_category').all()[:1000]
    return render(request,'finance/import_review.html',{'batch':b,'rows':rows,'categories':h.categories.filter(is_active=True),'kinds':Transaction.Kind.choices})

@login_required
@require_role(*EDIT_ROLES)
@require_POST
def import_commit(request,pk):
    b=get_object_or_404(ImportBatch,pk=pk,household=request.household); overrides={}
    for row in b.rows.filter(status__in=[ImportRow.Status.PENDING,ImportRow.Status.DUPLICATE]):
        overrides[str(row.id)]={'skip':bool(request.POST.get(f'skip_{row.id}')),'force':bool(request.POST.get(f'force_{row.id}')),'kind':request.POST.get(f'kind_{row.id}',''),'category_id':request.POST.get(f'category_{row.id}','')}
    n=commit_batch(b,request.user,overrides); messages.success(request,f'Imported {n} transaction(s).'); return redirect('import_review',pk=b.pk)

@login_required
def import_rules(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    f=request.GET; q=f.get('q','').strip(); direction=f.get('direction',''); state=f.get('state','active'); kind=f.get('kind',''); impact=f.get('impact',''); sort=f.get('sort','priority'); qs=h.import_rules.select_related('category').all(); qs=_filter_words(qs,q,['name','pattern','category__name'])
    if direction in dict(ImportRule.Direction.choices): qs=qs.filter(direction=direction)
    if state=='active': qs=qs.filter(is_active=True)
    elif state=='inactive': qs=qs.filter(is_active=False)
    if kind in dict(Transaction.Kind.choices): qs=qs.filter(transaction_kind=kind)
    if impact in dict(BudgetImpact.Kind.choices): qs=qs.filter(impact_kind=impact)
    ordering={'priority':('priority','name'),'name':('name',),'newest':('-id',)}.get(sort,('priority','name'))
    return render(request,'finance/import_rules.html',{'rules':qs.order_by(*ordering),'filters':f,'directions':ImportRule.Direction.choices,'kinds':Transaction.Kind.choices,'impacts':BudgetImpact.Kind.choices,'sort_filter':sort})

@login_required
@require_role(*EDIT_ROLES)
def import_rule_form(request,pk=None):
    h=request.household; obj=get_object_or_404(ImportRule,pk=pk,household=h) if pk else None; form=ImportRuleForm(request.POST or None,instance=obj,household=h)
    if request.method=='POST' and form.is_valid():
        r=form.save(commit=False); r.household=h; r.save(); messages.success(request,'Import rule saved.'); return redirect('import_rules')
    return render(request,'finance/form_page.html',{'form':form,'title':'Edit import rule' if obj else 'Add import rule','back_url':reverse('import_rules')})

@login_required
def settings_view(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    s,_=HouseholdSettings.objects.get_or_create(household=h); can_edit=get_membership(request.user).role==Membership.Role.OWNER; form=SettingsForm(request.POST or None,instance=s)
    if request.method=='POST':
        if not can_edit: messages.error(request,'Only the owner can change household settings.')
        elif form.is_valid():
            form.save(); moved=recalculate_budget_cycle(h)
            messages.success(request,f'Settings saved. Reassigned {moved} existing budget impact(s) to the correct pay cycle.' if moved else 'Settings saved.')
            return redirect('settings')
    return render(request,'finance/settings.html',{'form':form,'can_edit':can_edit})

@login_required
def members(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    return render(request,'finance/members.html',{'memberships':h.memberships.select_related('user'),'can_edit':get_membership(request.user).role==Membership.Role.OWNER})

@login_required
@require_role(Membership.Role.OWNER)
def member_add(request):
    form=MemberCreateForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        role=form.cleaned_data['role']; user=form.save(); Membership.objects.create(household=request.household,user=user,role=role); _owner_for_user(request.household,user); messages.success(request,'User created and an account owner profile was linked to them.'); return redirect('members')
    return render(request,'finance/form_page.html',{'form':form,'title':'Add household user','back_url':reverse('members')})

@login_required
@require_role(Membership.Role.OWNER)
@require_POST
def member_role(request,pk):
    m=get_object_or_404(Membership,pk=pk,household=request.household)
    role=request.POST.get('role','')
    valid_roles={value for value,_ in Membership.Role.choices}
    if role not in valid_roles: messages.error(request,'Invalid household role.')
    elif m.user==request.user and role!=Membership.Role.OWNER: messages.error(request,'You cannot demote your own owner account.')
    else: m.role=role; m.save(update_fields=['role']); messages.success(request,'Role updated.')
    return redirect('members')

@login_required
def audit(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    f=request.GET; q=f.get('q','').strip(); action=f.get('action',''); entity=f.get('entity',''); user=f.get('user',''); sort=f.get('sort','newest'); qs=h.audit_logs.select_related('user').all(); qs=_filter_words(qs,q,['summary','entity','entity_id','action','user__username','user__first_name','user__last_name'])
    if action: qs=qs.filter(action=action)
    if entity: qs=qs.filter(entity=entity)
    if user.isdigit(): qs=qs.filter(user_id=int(user))
    start=_parse_filter_date(f.get('date_from')); end=_parse_filter_date(f.get('date_to'))
    if start: qs=qs.filter(created_at__date__gte=start)
    if end: qs=qs.filter(created_at__date__lte=end)
    ordering=('created_at',) if sort=='oldest' else ('-created_at',); page,page_size,query_without_page=_paginate(request,qs.order_by(*ordering),100)
    actions=h.audit_logs.order_by().values_list('action',flat=True).distinct(); entities=h.audit_logs.order_by().values_list('entity',flat=True).distinct(); users=get_user_model().objects.filter(budget_memberships__household=h).distinct().order_by('username')
    return render(request,'finance/audit.html',{'logs':page.object_list,'page_obj':page,'page_size':page_size,'query_without_page':query_without_page,'filters':f,'actions_filter':actions,'entities_filter':entities,'users_filter':users,'sort_filter':sort})

@login_required
def export_transactions_csv(request):
    h=_ensure(request); resp=HttpResponse(content_type='text/csv'); resp['Content-Disposition']=f'attachment; filename="budget-transactions-{date.today().isoformat()}.csv"'
    w=csv.writer(resp); w.writerow(['date','type','status','description','payee','from_account','to_account','amount_base','budget_impacts','notes'])
    for tx in h.transactions.prefetch_related('entries__account','budget_impacts__category'):
        es=list(tx.entries.all()); impacts='; '.join(f'{i.kind}:{i.category.name}:{i.amount}:{i.budget_date}' for i in tx.budget_impacts.all())
        w.writerow([tx.date,tx.kind,tx.status,tx.description,tx.payee,es[0].account.name if es else '',es[1].account.name if len(es)>1 else '',tx.base_amount,impacts,tx.notes])
    return resp

@login_required
def export_json(request):
    h=_ensure(request); data={'schema_version':'2.7','exported_at':datetime.now().isoformat(),'household':{'name':h.name},'settings':{},'account_owners':[],'accounts':[],'categories':[],'transactions':[],'budget_plans':[],'goals':[],'loans':[],'recurring':[],'reimbursements':[]}
    s,_=HouseholdSettings.objects.get_or_create(household=h); data['settings']={'base_currency':s.base_currency,'budget_cycle_mode':s.budget_cycle_mode,'cycle_anchor_category':s.cycle_anchor_category.name if s.cycle_anchor_category_id else None,'shift_late_funding':s.shift_late_funding,'shift_day':s.shift_day,'savings_rate_basis':s.savings_rate_basis}
    for o in h.account_owners.select_related('linked_user').all(): data['account_owners'].append({'name':o.name,'kind':o.kind,'linked_username':o.linked_user.username if o.linked_user else '','active':o.is_active,'sort_order':o.sort_order})
    for a in h.accounts.select_related('owner','release_destination').prefetch_related('owners').all(): data['accounts'].append({'name':a.name,'owner':a.owner.name if a.owner else '','owners':[o.name for o in a.owners.all()],'type':a.account_type,'purpose':a.purpose,'currency':a.currency,'opening_balance':str(a.opening_balance),'opening_base_balance':str(a.opening_base_balance if a.opening_base_balance is not None else ''),'opening_date':str(a.opening_date or ''),'monthly_release_amount':str(a.monthly_release_amount if a.monthly_release_amount is not None else ''),'release_day':a.release_day or '','release_destination':a.release_destination.name if a.release_destination else '','include_in_net_worth':a.include_in_net_worth,'active':a.is_active})
    for c in h.categories.all(): data['categories'].append({'name':c.name,'kind':c.kind,'sort_order':c.sort_order,'active':c.is_active})
    for tx in h.transactions.prefetch_related('entries__account','budget_impacts__category','tags'):
        data['transactions'].append({'id':tx.id,'date':str(tx.date),'kind':tx.kind,'status':tx.status,'description':tx.description,'payee':tx.payee,'notes':tx.notes,'tags':[t.name for t in tx.tags.all()],'entries':[{'account':e.account.name,'amount':str(e.amount),'base_amount':str(e.base_amount)} for e in tx.entries.all()],'budget_impacts':[{'kind':i.kind,'category':i.category.name,'amount':str(i.amount),'budget_date':str(i.budget_date),'budget_date_locked':i.budget_date_locked} for i in tx.budget_impacts.all()]})
    for p in h.budget_plans.select_related('category'): data['budget_plans'].append({'month':str(p.month),'category':p.category.name,'kind':p.category.kind,'amount':str(p.amount),'note':p.note})
    for g in h.savings_goals.all(): data['goals'].append({'name':g.name,'target_amount':str(g.target_amount),'target_date':str(g.target_date or ''),'starting_amount':str(g.starting_amount)})
    for l in h.loans.select_related('account'): data['loans'].append({'account':l.account.name,'lender':l.lender,'original_principal':str(l.original_principal),'apr':str(l.annual_interest_rate),'regular_payment':str(l.regular_payment)})
    for r in h.recurring_rules.all(): data['recurring'].append({'name':r.name,'kind':r.transaction_kind,'amount':str(r.amount),'frequency':r.frequency,'interval':r.interval,'next_run':str(r.next_run),'auto_post':r.auto_post})
    for link in h.reimbursement_links.select_related('purchase','reimbursement').all():
        data['reimbursements'].append({'purchase_id':link.purchase_id,'reimbursement_id':link.reimbursement_id,'amount':str(link.amount),'created_at':link.created_at.isoformat()})
    resp=HttpResponse(json.dumps(data,indent=2),content_type='application/json'); resp['Content-Disposition']=f'attachment; filename="budget-backup-{date.today().isoformat()}.json"'; return resp

def health(request): return JsonResponse({'status':'ok','version':get_version()})

def mobile_info(request):
    """Public, non-sensitive endpoint used by Budget Manager mobile clients."""
    return JsonResponse({
        'product':'budget-manager',
        'name':'Budget Manager',
        'version':get_version(),
        'protocol':2,
        'auth':'token',
        'web_path':'/',
        'mobile_api':'/api/mobile/v1/',
        'mobile_api_version':1,
    })


@login_required
def attachment_download(request,pk):
    from django.http import FileResponse, Http404
    from .models import Attachment
    h=_ensure(request); obj=get_object_or_404(Attachment,pk=pk,transaction__household=h)
    try: return FileResponse(obj.file.open('rb'),as_attachment=True,filename=obj.label or obj.file.name.rsplit('/',1)[-1])
    except FileNotFoundError: raise Http404('Attachment file not found')

@login_required
@require_role(*EDIT_ROLES)
@require_POST
def tag_add(request):
    name=request.POST.get('name','').strip()
    if name:
        Tag.objects.get_or_create(household=request.household,name=name)
        messages.success(request,'Tag added.')
    return redirect('categories')

@login_required
@require_role(*EDIT_ROLES)
@require_POST
def tag_delete(request,pk):
    t=get_object_or_404(Tag,pk=pk,household=request.household); t.delete(); messages.success(request,'Tag deleted.'); return redirect('categories')

@login_required
@require_role(*EDIT_ROLES)
@require_POST
def attachment_delete(request,pk):
    from .models import Attachment
    obj=get_object_or_404(Attachment,pk=pk,transaction__household=request.household); tx_id=obj.transaction_id
    try: obj.file.delete(save=False)
    except Exception: pass
    obj.delete(); messages.success(request,'Attachment deleted.'); return redirect('transaction_edit',pk=tx_id)

@login_required
@require_role(Membership.Role.OWNER)
@require_POST
def member_remove(request,pk):
    m=get_object_or_404(Membership,pk=pk,household=request.household)
    if m.user==request.user:
        messages.error(request,'You cannot remove your own owner membership.')
    else:
        username=m.user.username; m.delete(); messages.success(request,f'Removed {username} from the household.')
    return redirect('members')


@login_required
def bank_connections(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    cfg=bank_sync_config()
    display_base=cfg['base_url'] or request.build_absolute_uri('/').rstrip('/')
    connections=list(h.bank_connections.prefetch_related('linked_accounts__account__owner').all())
    for connection in connections:
        connection.sync_issue=describe_sync_issue(connection)
        connection.next_background_sync_at=next_background_sync_at(connection)
    pending=BankSyncTransaction.objects.filter(bank_account__connection__household=h,status=BankSyncTransaction.Status.PENDING).count()
    return render(request,'finance/bank_connections.html',{'connections':connections,'bank_config':cfg,'pending_count':pending,'bank_display_base':display_base})

@login_required
@require_role(*EDIT_ROLES)
def bank_connect(request):
    h=request.household; cfg=bank_sync_config()
    if not cfg['configured']:
        messages.error(request,'Configure the Enable Banking application ID and private key first.')
        return redirect('bank_connections')
    client=EnableBankingClient()
    try:
        banks=client.list_aspsps('DE')
    except Exception as exc:
        messages.error(request,f'Could not load the bank list: {exc}')
        return redirect('bank_connections')
    banks=sorted(banks,key=lambda b:(str((b.get('name') or b.get('group',{}).get('name') or '')).casefold()))
    if request.method=='POST':
        bank_name=request.POST.get('bank_name','').strip(); country=request.POST.get('country','DE').strip().upper()
        selected=next((b for b in banks if (b.get('name') or '')==bank_name and (b.get('country') or country)==country),None)
        if not selected:
            messages.error(request,'Choose a bank from the current provider list.')
        else:
            state=__import__('secrets').token_urlsafe(32)
            base=cfg['base_url']
            callback=(base + reverse('bank_callback')) if base else request.build_absolute_uri(reverse('bank_callback'))
            attempt=BankAuthorizationAttempt.objects.create(household=h,user=request.user,state=state,aspsp_name=bank_name,country=country,redirect_url=callback)
            try:
                auth=client.start_authorization(aspsp_name=bank_name,country=country,state=state,redirect_url=callback,
                                                psu_id=f'h{h.pk}-u{request.user.pk}',maximum_consent_validity=selected.get('maximum_consent_validity'))
                attempt.authorization_id=auth.get('authorization_id',''); attempt.psu_id_hash=auth.get('psu_id_hash',''); attempt.save(update_fields=['authorization_id','psu_id_hash'])
                return redirect(auth['url'])
            except Exception as exc:
                attempt.error=str(exc); attempt.save(update_fields=['error']); messages.error(request,f'Could not start bank authorization: {exc}')
    return render(request,'finance/bank_connect.html',{'banks':banks,'bank_config':cfg})

@login_required
def bank_callback(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    state=request.GET.get('state',''); code=request.GET.get('code','')
    attempt=BankAuthorizationAttempt.objects.filter(state=state,household=h,user=request.user,completed_at__isnull=True).first()
    if not attempt:
        messages.error(request,'The bank authorization state is missing, expired, or does not belong to this login.')
        return redirect('bank_connections')
    if timezone.now()-attempt.created_at>timedelta(hours=1):
        attempt.error='Authorization callback arrived after the one-hour safety window.'; attempt.completed_at=timezone.now(); attempt.save(update_fields=['error','completed_at'])
        messages.error(request,'That bank authorization attempt expired. Please start again.')
        return redirect('bank_connections')
    if not code:
        attempt.error=request.GET.get('error_description') or request.GET.get('error') or 'No authorization code returned.'; attempt.completed_at=timezone.now(); attempt.save(update_fields=['error','completed_at'])
        messages.error(request,f'Bank authorization was not completed: {attempt.error}')
        return redirect('bank_connections')
    try:
        payload=EnableBankingClient().authorize_session(code)
        connection=store_authorized_session(household=h,user=request.user,payload=payload,connection=attempt.connection)
        attempt.connection=connection; attempt.completed_at=timezone.now(); attempt.save(update_fields=['connection','completed_at'])
        try:
            staged=sync_connection(connection,psu_headers=psu_headers_from_request(request))
            messages.success(request,f'{connection.aspsp_name} connected. {staged} transaction(s) were added to the review inbox.')
        except Exception as sync_exc:
            messages.warning(request,f'Bank connected, but the first sync needs attention: {sync_exc}')
        return redirect('bank_connections')
    except Exception as exc:
        attempt.error=str(exc); attempt.completed_at=timezone.now(); attempt.save(update_fields=['error','completed_at'])
        messages.error(request,f'Could not finish bank authorization: {exc}')
        return redirect('bank_connections')

@login_required
@require_role(*EDIT_ROLES)
def bank_reauthorize(request,pk):
    if request.method!='POST': return redirect('bank_connections')
    h=request.household; connection=get_object_or_404(BankConnection,pk=pk,household=h)
    cfg=bank_sync_config(); client=EnableBankingClient()
    try:
        banks=client.list_aspsps(connection.country)
        selected=next((b for b in banks if (b.get('name') or '')==connection.aspsp_name),{})
        state=__import__('secrets').token_urlsafe(32)
        callback=(cfg['base_url']+reverse('bank_callback')) if cfg['base_url'] else request.build_absolute_uri(reverse('bank_callback'))
        attempt=BankAuthorizationAttempt.objects.create(household=h,user=request.user,connection=connection,state=state,aspsp_name=connection.aspsp_name,country=connection.country,redirect_url=callback)
        auth=client.start_authorization(aspsp_name=connection.aspsp_name,country=connection.country,state=state,redirect_url=callback,
                                        psu_id=f'h{h.pk}-u{request.user.pk}',maximum_consent_validity=selected.get('maximum_consent_validity'))
        attempt.authorization_id=auth.get('authorization_id',''); attempt.psu_id_hash=auth.get('psu_id_hash',''); attempt.save(update_fields=['authorization_id','psu_id_hash'])
        return redirect(auth['url'])
    except Exception as exc:
        messages.error(request,f'Could not start re-authorization: {exc}'); return redirect('bank_connections')

@login_required
@require_role(*EDIT_ROLES)
def bank_sync_now(request,pk):
    if request.method!='POST': return redirect('bank_connections')
    connection=get_object_or_404(BankConnection,pk=pk,household=request.household)
    try:
        staged=sync_connection(connection,psu_headers=psu_headers_from_request(request))
        messages.success(request,f'Interactive sync complete. {staged} new transaction(s) need review.')
    except BankRateLimitError:
        messages.warning(request,'The bank is still rate-limiting this refresh. Your connection is healthy; wait and try again later. Automatic sync will retry on its own.')
    except Exception as exc:
        messages.error(request,f'Bank sync failed: {exc}')
    return redirect('bank_connections')

@login_required
@require_role(*EDIT_ROLES)
def bank_disconnect(request,pk):
    if request.method!='POST': return redirect('bank_connections')
    connection=get_object_or_404(BankConnection,pk=pk,household=request.household)
    try:
        if connection.session_id: EnableBankingClient().close_session(connection.session_id)
    except Exception as exc:
        messages.warning(request,f'The provider could not confirm consent closure: {exc}')
    connection.status=BankConnection.Status.CLOSED; connection.save(update_fields=['status','updated_at'])
    messages.success(request,f'{connection.aspsp_name} marked disconnected. Previously imported data was kept.')
    return redirect('bank_connections')

@login_required
@require_role(*EDIT_ROLES)
def bank_account_map(request,pk):
    link=get_object_or_404(BankLinkedAccount,pk=pk,connection__household=request.household)
    form=BankAccountMappingForm(request.POST or None,instance=link,household=request.household)
    if request.method=='POST' and form.is_valid():
        form.save(); messages.success(request,'Bank account mapping saved.'); return redirect('bank_connections')
    return render(request,'finance/bank_account_map.html',{'form':form,'link':link})

@login_required
def bank_inbox(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    status=request.GET.get('status',BankSyncTransaction.Status.PENDING)
    qs=BankSyncTransaction.objects.filter(bank_account__connection__household=h).select_related(
        'bank_account__account','bank_account__connection','suggested_category','transaction'
    ).annotate(abs_amount=Abs('amount'))
    if status in dict(BankSyncTransaction.Status.choices):
        qs=qs.filter(status=status)
    elif status!='all':
        status=BankSyncTransaction.Status.PENDING; qs=qs.filter(status=status)

    filter_errors=[]
    q=request.GET.get('q','').strip()
    if q:
        # Each word must match somewhere, so multiple search words are more useful than a literal phrase search.
        for token in q.split():
            qs=qs.filter(
                Q(description__icontains=token)|Q(counterparty__icontains=token)|Q(entry_reference__icontains=token)|
                Q(bank_account__connection__aspsp_name__icontains=token)|Q(bank_account__name__icontains=token)|
                Q(bank_account__account__name__icontains=token)
            )

    def parsed_date(name):
        raw=request.GET.get(name,'').strip()
        if not raw:return None
        try:return datetime.strptime(raw,'%Y-%m-%d').date()
        except ValueError:
            filter_errors.append(f'Invalid date in {name.replace("_"," ")}.'); return None

    exact_date=parsed_date('exact_date'); date_from=parsed_date('date_from'); date_to=parsed_date('date_to')
    if exact_date: qs=qs.filter(booking_date=exact_date)
    else:
        if date_from: qs=qs.filter(booking_date__gte=date_from)
        if date_to: qs=qs.filter(booking_date__lte=date_to)

    def parsed_decimal(name):
        raw=request.GET.get(name,'').strip().replace(',','.')
        if not raw:return None
        try:
            value=abs(Decimal(raw))
            return value
        except (InvalidOperation,ValueError):
            filter_errors.append(f'Invalid amount in {name.replace("_"," ")}.'); return None

    amount_exact=parsed_decimal('amount_exact'); amount_min=parsed_decimal('amount_min'); amount_max=parsed_decimal('amount_max')
    if amount_exact is not None: qs=qs.filter(abs_amount=amount_exact)
    else:
        if amount_min is not None: qs=qs.filter(abs_amount__gte=amount_min)
        if amount_max is not None: qs=qs.filter(abs_amount__lte=amount_max)

    direction=request.GET.get('direction','')
    if direction=='in': qs=qs.filter(amount__gt=0)
    elif direction=='out': qs=qs.filter(amount__lt=0)

    bank=request.GET.get('bank','').strip(); account=request.GET.get('account','').strip()
    if bank.isdigit(): qs=qs.filter(bank_account__connection_id=int(bank))
    if account.isdigit(): qs=qs.filter(bank_account_id=int(account))

    sort=request.GET.get('sort','newest')
    ordering={
        'newest':('-booking_date','-id'),'oldest':('booking_date','id'),
        'amount_high':('-abs_amount','-booking_date','-id'),'amount_low':('abs_amount','-booking_date','-id'),
    }
    if sort not in ordering: sort='newest'
    qs=qs.order_by(*ordering[sort])

    try: page_size=int(request.GET.get('page_size','100'))
    except ValueError: page_size=100
    if page_size not in (50,100,250): page_size=100
    paginator=Paginator(qs,page_size)
    page_obj=paginator.get_page(request.GET.get('page','1'))
    rows=list(page_obj.object_list)
    pending_qs=BankSyncTransaction.objects.filter(bank_account__connection__household=h,status=BankSyncTransaction.Status.PENDING)
    categorization_history=load_categorization_history(h)
    for row in rows:
        row.candidates=transfer_candidates(row,pending_qs)[:3] if row.status==BankSyncTransaction.Status.PENDING else []
        if row.status==BankSyncTransaction.Status.PENDING:
            refresh_bank_row_suggestion(row,history_rows=categorization_history)
        row.import_form=BankInboxImportForm(
            household=h,suggested_category=row.suggested_category,
            impact_kind=row.suggested_impact_kind or (Category.Kind.FUNDING if row.amount>0 else Category.Kind.EXPENSE),
        )

    params=request.GET.copy(); params.pop('page',None); query_without_page=params.urlencode()
    bank_connections=h.bank_connections.exclude(status=BankConnection.Status.CLOSED).order_by('aspsp_name','id')
    linked_accounts=BankLinkedAccount.objects.filter(connection__household=h).select_related('connection','account').order_by('connection__aspsp_name','name')
    return render(request,'finance/bank_inbox.html',{
        'rows':rows,'page_obj':page_obj,'status_filter':status,'q':q,'filter_errors':filter_errors,
        'bank_connections':bank_connections,'linked_accounts':linked_accounts,'query_without_page':query_without_page,
        'filters':request.GET,'sort_filter':sort,'page_size':page_size,'current_path':request.get_full_path(),
    })

@login_required
@require_role(*EDIT_ROLES)
@require_POST
def bank_inbox_bulk_ignore(request):
    ids=[]
    for raw in request.POST.getlist('selected')[:500]:
        try: ids.append(int(raw))
        except (TypeError,ValueError): pass
    if not ids:
        messages.warning(request,'Select at least one bank transaction first.')
    else:
        qs=BankSyncTransaction.objects.filter(
            pk__in=ids,bank_account__connection__household=request.household,status=BankSyncTransaction.Status.PENDING
        )
        count=qs.update(status=BankSyncTransaction.Status.IGNORED,updated_at=timezone.now())
        messages.success(request,f'Ignored {count} selected bank transaction(s).')
        AuditLog.objects.create(household=request.household,user=request.user,action='bulk_ignore',entity='bank_sync_transaction',summary=f'Ignored {count} selected bank inbox rows.',metadata={'ids':ids})
    return _safe_next(request)

@login_required
@require_role(*EDIT_ROLES)
def bank_inbox_import(request,pk):
    if request.method!='POST': return redirect('bank_inbox')
    row=get_object_or_404(BankSyncTransaction,pk=pk,bank_account__connection__household=request.household)
    refresh_bank_row_suggestion(row)
    form=BankInboxImportForm(
        request.POST,household=request.household,suggested_category=row.suggested_category,
        impact_kind=row.suggested_impact_kind or (Category.Kind.FUNDING if row.amount>0 else Category.Kind.EXPENSE),
    )
    if form.is_valid():
        match=None
        match_id=request.POST.get('match_id')
        if match_id:
            match=get_object_or_404(BankSyncTransaction,pk=match_id,bank_account__connection__household=request.household)
        try:
            tx=import_bank_row(row,user=request.user,category=form.cleaned_data['category'],match=match)
            messages.success(request,'Bank transaction imported.')
            if request.POST.get('pair_reimbursement') and row.amount>0 and tx:
                return redirect('reimbursement_pair',pk=tx.pk)
        except Exception as exc: messages.error(request,f'Could not import bank transaction: {exc}')
    else: messages.error(request,'Choose a valid category or leave it blank.')
    return _safe_next(request)

@login_required
@require_role(*EDIT_ROLES)
def bank_inbox_ignore(request,pk):
    if request.method!='POST': return redirect('bank_inbox')
    row=get_object_or_404(BankSyncTransaction,pk=pk,bank_account__connection__household=request.household)
    if row.status==BankSyncTransaction.Status.PENDING:
        row.status=BankSyncTransaction.Status.IGNORED; row.save(update_fields=['status','updated_at'])
    return _safe_next(request)

@login_required
def help_page(request):
    h=_ensure(request)
    if not h:return redirect('setup_household')
    return render(request,'finance/help.html',{'active_month':active_budget_month(h)})

def banking_privacy(request):
    return render(request,'finance/banking_privacy.html')

def banking_terms(request):
    return render(request,'finance/banking_terms.html')


@login_required
@require_role(*EDIT_ROLES)
def bank_inbox_restore(request,pk):
    if request.method!='POST': return redirect('bank_inbox')
    row=get_object_or_404(BankSyncTransaction,pk=pk,bank_account__connection__household=request.household)
    if row.status==BankSyncTransaction.Status.IGNORED:
        row.status=BankSyncTransaction.Status.PENDING; row.save(update_fields=['status','updated_at'])
    return _safe_next(request)
