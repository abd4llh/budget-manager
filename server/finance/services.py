from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib, json, math, re
from bisect import bisect_right
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from .models import (Account, AuditLog, BudgetImpact, BudgetPlan, Category, HouseholdSettings, ImportRule, ReimbursementLink,
                     RecurringRule, SavingsGoal, Transaction, TransactionEntry, ZERO)
from .utils import add_months, advance_date

TWO=Decimal('0.01')
def q(v): return Decimal(v).quantize(TWO,rounding=ROUND_HALF_UP)

def log_action(household,user,action,entity,obj,summary,metadata=None):
    AuditLog.objects.create(household=household,user=user,action=action,entity=entity,entity_id=str(getattr(obj,'pk','') or ''),summary=summary,metadata=metadata or {})

def _calendar_budget_date(settings, posted_date, impact_kind):
    d=posted_date.replace(day=1)
    if impact_kind==BudgetImpact.Kind.FUNDING and settings.shift_late_funding and posted_date.day>=settings.shift_day:
        return add_months(d,1)
    return d

def _fallback_pay_cycle_date(settings, posted_date):
    d=posted_date.replace(day=1)
    return add_months(d,1) if posted_date.day>=settings.shift_day else d

def active_budget_month(household, as_of=None):
    """Return the budget month that should open by default for a household.

    In pay-cycle mode the latest known salary/pay-cycle anchor opens the following
    budget month. Before an anchor has been imported for the current cycle,
    shift_day provides the same fallback used by automatic budget assignment.
    """
    as_of=as_of or date.today()
    settings,_=HouseholdSettings.objects.get_or_create(household=household)
    current=as_of.replace(day=1)
    if settings.budget_cycle_mode!=HouseholdSettings.BudgetCycleMode.PAY_CYCLE or not settings.cycle_anchor_category_id:
        return current
    latest_anchor=(BudgetImpact.objects.filter(
        transaction__household=household,kind=BudgetImpact.Kind.FUNDING,
        category_id=settings.cycle_anchor_category_id,transaction__date__lte=as_of,
    ).order_by('-transaction__date','-transaction_id').values_list('transaction__date',flat=True).first())
    fallback=_fallback_pay_cycle_date(settings,as_of)
    if latest_anchor:
        anchored=add_months(latest_anchor.replace(day=1),1)
        return max(anchored,fallback)
    return fallback

def effective_budget_date(household, posted_date, impact_kind, explicit=None, category=None):
    if explicit: return explicit.replace(day=1)
    settings,_=HouseholdSettings.objects.get_or_create(household=household)
    if settings.budget_cycle_mode!=HouseholdSettings.BudgetCycleMode.PAY_CYCLE or not settings.cycle_anchor_category_id:
        return _calendar_budget_date(settings,posted_date,impact_kind)
    if category and category.pk==settings.cycle_anchor_category_id and impact_kind==BudgetImpact.Kind.FUNDING:
        return add_months(posted_date.replace(day=1),1)
    latest_anchor=(BudgetImpact.objects.filter(
        transaction__household=household,kind=BudgetImpact.Kind.FUNDING,
        category_id=settings.cycle_anchor_category_id,transaction__date__lte=posted_date,
    ).order_by('-transaction__date','-transaction_id').values_list('transaction__date',flat=True).first())
    if latest_anchor:
        return add_months(latest_anchor.replace(day=1),1)
    return _fallback_pay_cycle_date(settings,posted_date)

def recalculate_budget_cycle(household):
    settings,_=HouseholdSettings.objects.get_or_create(household=household)
    pay_cycle=(settings.budget_cycle_mode==HouseholdSettings.BudgetCycleMode.PAY_CYCLE and bool(settings.cycle_anchor_category_id))
    anchor_dates=[]
    if pay_cycle:
        anchor_dates=list(BudgetImpact.objects.filter(
            transaction__household=household,kind=BudgetImpact.Kind.FUNDING,category_id=settings.cycle_anchor_category_id,
        ).order_by('transaction__date').values_list('transaction__date',flat=True))
        # Multiple salary impacts on the same date are equivalent to one cycle boundary.
        anchor_dates=sorted(set(anchor_dates))
    changed=[]
    impacts=BudgetImpact.objects.filter(transaction__household=household,budget_date_locked=False).select_related('transaction','category')
    for impact in impacts:
        tx_date=impact.transaction.date
        if not pay_cycle:
            desired=_calendar_budget_date(settings,tx_date,impact.kind)
        elif impact.kind==BudgetImpact.Kind.FUNDING and impact.category_id==settings.cycle_anchor_category_id:
            desired=add_months(tx_date.replace(day=1),1)
        else:
            idx=bisect_right(anchor_dates,tx_date)-1
            desired=add_months(anchor_dates[idx].replace(day=1),1) if idx>=0 else _fallback_pay_cycle_date(settings,tx_date)
        if impact.budget_date!=desired:
            impact.budget_date=desired; changed.append(impact)
    if changed: BudgetImpact.objects.bulk_update(changed,['budget_date'])
    return len(changed)

def validate_impact(impact):
    category=impact['category']; kind=impact['kind']
    if category.kind!=kind: raise ValueError(f'Category {category.name} is {category.kind}, not {kind}.')
    if Decimal(impact['amount'])<0: raise ValueError('Budget impact amounts must be non-negative.')

@db_transaction.atomic
def create_transaction(*,household,user,posted_date,kind,description,from_account,to_account,amount,
                       destination_amount=None,base_value=None,impacts=None,notes='',payee='',status=Transaction.Status.CLEARED,
                       external_id='',fingerprint='',tags=None):
    amount=q(amount)
    if amount<=0: raise ValueError('Amount must be greater than zero.')
    if from_account.pk==to_account.pk: raise ValueError('Source and destination accounts must differ.')
    if from_account.household_id!=household.id or to_account.household_id!=household.id: raise ValueError('Account household mismatch.')
    settings,_=HouseholdSettings.objects.get_or_create(household=household)
    if base_value is None:
        if from_account.currency!=settings.base_currency or to_account.currency!=settings.base_currency:
            raise ValueError('For cross-currency transfers, provide the base-currency value.')
        base_value=amount
    base_value=q(base_value)
    dest=q(destination_amount if destination_amount is not None else amount)
    if base_value<=0 or dest<=0: raise ValueError('Destination amount and base value must be greater than zero.')
    tx=Transaction.objects.create(household=household,date=posted_date,kind=kind,status=status,description=description.strip(),payee=payee.strip(),notes=notes,created_by=user,external_id=external_id,import_fingerprint=fingerprint)
    TransactionEntry.objects.bulk_create([
        TransactionEntry(transaction=tx,account=from_account,amount=-amount,base_amount=-base_value),
        TransactionEntry(transaction=tx,account=to_account,amount=dest,base_amount=base_value),
    ])
    for impact in impacts or []:
        validate_impact(impact)
        explicit_date=impact.get('budget_date')
        BudgetImpact.objects.create(transaction=tx,kind=impact['kind'],category=impact['category'],amount=q(impact['amount']),budget_date=effective_budget_date(household,posted_date,impact['kind'],explicit_date,impact['category']),budget_date_locked=bool(explicit_date),memo=impact.get('memo',''))
    if tags: tx.tags.set(tags)
    recalculate_budget_cycle(household)
    log_action(household,user,'create','transaction',tx,f'{kind}: {description} ({amount})')
    return tx

@db_transaction.atomic
def update_transaction(tx, *, user, posted_date, kind, description, from_account, to_account, amount,
                       destination_amount=None, base_value=None, impacts=None, notes='', payee='', status=Transaction.Status.CLEARED,tags=None):
    household=tx.household
    tx.entries.all().delete(); tx.budget_impacts.all().delete()
    settings,_=HouseholdSettings.objects.get_or_create(household=household)
    amount=q(amount); dest=q(destination_amount if destination_amount is not None else amount)
    if amount<=0: raise ValueError('Amount must be greater than zero.')
    if from_account.pk==to_account.pk: raise ValueError('Source and destination accounts must differ.')
    if from_account.household_id!=household.id or to_account.household_id!=household.id: raise ValueError('Account household mismatch.')
    if base_value is None:
        if from_account.currency!=settings.base_currency or to_account.currency!=settings.base_currency: raise ValueError('Cross-currency transfer requires base value.')
        base_value=amount
    base_value=q(base_value)
    if base_value<=0 or dest<=0: raise ValueError('Destination amount and base value must be greater than zero.')
    tx.date=posted_date; tx.kind=kind; tx.description=description.strip(); tx.payee=payee.strip(); tx.notes=notes; tx.status=status; tx.save()
    TransactionEntry.objects.bulk_create([
        TransactionEntry(transaction=tx,account=from_account,amount=-amount,base_amount=-base_value),
        TransactionEntry(transaction=tx,account=to_account,amount=dest,base_amount=base_value),
    ])
    for impact in impacts or []:
        validate_impact(impact)
        explicit_date=impact.get('budget_date')
        BudgetImpact.objects.create(transaction=tx,kind=impact['kind'],category=impact['category'],amount=q(impact['amount']),budget_date=effective_budget_date(household,posted_date,impact['kind'],explicit_date,impact['category']),budget_date_locked=bool(explicit_date),memo=impact.get('memo',''))
    if tags is not None: tx.tags.set(tags)
    recalculate_budget_cycle(household)
    log_action(household,user,'update','transaction',tx,f'Updated {description}')
    return tx

def _impact_snapshot(impacts):
    return [{'id':i.id,'kind':i.kind,'category_id':i.category_id,'amount':str(i.amount),'budget_date':i.budget_date.isoformat(),'budget_date_locked':i.budget_date_locked,'memo':i.memo} for i in impacts]

@db_transaction.atomic
def pair_reimbursement(*,purchase,reimbursement,user):
    if purchase.pk==reimbursement.pk: raise ValueError('A transaction cannot reimburse itself.')
    if purchase.household_id!=reimbursement.household_id: raise ValueError('Transactions must belong to the same household.')
    if ReimbursementLink.objects.filter(purchase=purchase).exists(): raise ValueError('That purchase is already paired with a reimbursement.')
    if ReimbursementLink.objects.filter(reimbursement=reimbursement).exists(): raise ValueError('That reimbursement is already paired.')
    amount=q(reimbursement.base_amount)
    if amount<=0: raise ValueError('Reimbursement amount must be greater than zero.')
    # A reimbursement must bring money from outside the tracked household into a tracked asset.
    # This intentionally rejects internal transfers such as Wallet -> Bank, which must
    # remain ordinary transfers rather than being paired to a purchase.
    has_external_source=reimbursement.entries.filter(amount__lt=0).filter(Q(account__purpose=Account.Purpose.EXTERNAL)|Q(account__account_type=Account.Type.EXTERNAL)).exists()
    has_tracked_destination=reimbursement.entries.filter(amount__gt=0,account__purpose__in=[Account.Purpose.AVAILABLE,Account.Purpose.SAVINGS]).exists()
    if not (has_external_source and has_tracked_destination):
        raise ValueError('Choose the incoming payment from your friend, not a transfer between your own accounts.')
    impacts=list(purchase.budget_impacts.filter(kind=BudgetImpact.Kind.EXPENSE).order_by('id'))
    total=sum((i.amount for i in impacts),ZERO)
    if not impacts: raise ValueError('The original purchase has no expense budget impact to reduce.')
    if amount>total: raise ValueError(f'Reimbursement ({amount:.2f}) is larger than the purchase expense still assigned to the budget ({total:.2f}).')
    purchase_before=_impact_snapshot(impacts)
    reimbursement_impacts=list(reimbursement.budget_impacts.select_related('category').order_by('id'))
    reimbursement_before=_impact_snapshot(reimbursement_impacts)
    remaining=amount
    for impact in impacts:
        if remaining<=0: break
        deduction=min(impact.amount,remaining)
        impact.amount=q(impact.amount-deduction); impact.save(update_fields=['amount'])
        remaining=q(remaining-deduction)
    reimbursement.budget_impacts.all().delete()
    kind_before=reimbursement.kind
    reimbursement.kind=Transaction.Kind.OTHER
    reimbursement.save(update_fields=['kind','updated_at'])
    link=ReimbursementLink.objects.create(
        household=purchase.household,purchase=purchase,reimbursement=reimbursement,amount=amount,
        purchase_impacts_before=purchase_before,reimbursement_impacts_before=reimbursement_before,
        reimbursement_kind_before=kind_before,created_by=user,
    )
    recalculate_budget_cycle(purchase.household)
    log_action(purchase.household,user,'pair','reimbursement',link,f'Paired reimbursement {reimbursement.description} with {purchase.description} ({amount})')
    return link

@db_transaction.atomic
def unpair_reimbursement(link, *, user):
    purchase=link.purchase; reimbursement=link.reimbursement
    current={i.id:i for i in purchase.budget_impacts.all()}
    for snap in link.purchase_impacts_before or []:
        impact=current.get(int(snap['id']))
        if not impact: raise ValueError('The purchase was edited after pairing. Restore it manually before unpairing.')
        impact.amount=q(snap['amount']); impact.save(update_fields=['amount'])
    reimbursement.budget_impacts.all().delete()
    for snap in link.reimbursement_impacts_before or []:
        BudgetImpact.objects.create(
            transaction=reimbursement,kind=snap['kind'],category_id=int(snap['category_id']),amount=q(snap['amount']),
            budget_date=date.fromisoformat(snap['budget_date']),budget_date_locked=bool(snap.get('budget_date_locked')),memo=snap.get('memo',''),
        )
    reimbursement.kind=link.reimbursement_kind_before or Transaction.Kind.INCOME
    reimbursement.save(update_fields=['kind','updated_at'])
    household=link.household
    log_action(household,user,'unpair','reimbursement',link,f'Unpaired reimbursement {reimbursement.description} from {purchase.description}')
    link.delete(); recalculate_budget_cycle(household)
    return reimbursement

def account_balance_at(account, through_date=None):
    qs=account.entries.all()
    if through_date: qs=qs.filter(transaction__date__lte=through_date)
    opening=account.opening_balance
    if through_date and account.opening_date and through_date < account.opening_date: opening=ZERO
    return opening+(qs.aggregate(total=Sum('amount'))['total'] or ZERO)

def account_base_balance_at(account, through_date=None):
    qs=account.entries.all()
    if through_date: qs=qs.filter(transaction__date__lte=through_date)
    opening=account.opening_base_balance if account.opening_base_balance is not None else account.opening_balance
    if through_date and account.opening_date and through_date < account.opening_date: opening=ZERO
    return opening+(qs.aggregate(total=Sum('base_amount'))['total'] or ZERO)

def net_worth_at(household, through_date=None):
    total=ZERO
    for a in household.accounts.filter(include_in_net_worth=True): total += account_base_balance_at(a,through_date)
    return q(total)

def savings_goal_progress(goal):
    if goal.account_id:
        bal=goal.account.base_balance
        return q(-bal if goal.account.is_liability else bal)
    total=goal.starting_amount
    if goal.category_id:
        qs=BudgetImpact.objects.filter(transaction__household=goal.household,kind=BudgetImpact.Kind.SAVINGS,category=goal.category)
        if goal.start_date: qs=qs.filter(budget_date__gte=goal.start_date.replace(day=1))
        total += qs.aggregate(total=Sum('amount'))['total'] or ZERO
    return q(total)

def loan_schedule(loan, max_months=600):
    principal=max(loan.account.display_balance, ZERO)
    payment=Decimal(loan.regular_payment); monthly=(Decimal(loan.annual_interest_rate)/Decimal('100'))/Decimal('12')
    rows=[]; today_month=date.today().replace(day=1); start_month=loan.start_date.replace(day=1); month=max(today_month,start_month); balance=principal
    if principal<=0 or payment<=0: return rows
    for i in range(max_months):
        interest=q(balance*monthly); principal_paid=q(payment-interest)
        if principal_paid<=0:
            rows.append({'month':month,'payment':payment,'interest':interest,'principal':ZERO,'balance':balance,'negative_amortization':True}); break
        if principal_paid>balance:
            principal_paid=balance; actual=q(principal_paid+interest)
        else: actual=payment
        balance=q(balance-principal_paid)
        rows.append({'month':month,'payment':actual,'interest':interest,'principal':principal_paid,'balance':balance,'negative_amortization':False})
        if balance<=0: break
        month=add_months(month,1)
    return rows

def rule_matches(rule, description, amount):
    direction='in' if amount>0 else 'out'
    if rule.direction not in ('any',direction): return False
    text=description or ''; pattern=rule.pattern or ''
    if rule.match_type=='contains': return pattern.casefold() in text.casefold()
    if rule.match_type=='starts': return text.casefold().startswith(pattern.casefold())
    if rule.match_type=='exact': return text.casefold()==pattern.casefold()
    try: return re.search(pattern,text,re.I) is not None
    except re.error: return False

def classify_import_row(household, description, amount, *, counterparty='', raw_data=None, bank_account=None, exclude_row_id=None, history_rows=None):
    # Kept as the compatibility tuple API used by CSV import and bank sync.
    # The detailed, explainable suggestion engine lives in categorization.py.
    from .categorization import categorize_import_row
    suggestion=categorize_import_row(
        household,description,amount,counterparty=counterparty,raw_data=raw_data,
        bank_account=bank_account,exclude_row_id=exclude_row_id,history_rows=history_rows,
    )
    return suggestion.transaction_kind,suggestion.impact_kind,suggestion.category

def import_fingerprint(household_id, account_id, txn_date, amount, description):
    raw=f'{household_id}|{account_id}|{txn_date.isoformat()}|{q(amount)}|{description.strip().casefold()}'
    return hashlib.sha256(raw.encode()).hexdigest()

def recurring_impacts(rule, posted_date):
    impacts=[]
    for spec in rule.budget_impacts or []:
        try: category=Category.objects.get(pk=int(spec['category_id']),household=rule.household)
        except Exception: continue
        mode=spec.get('amount_mode','same')
        amt=rule.amount if mode=='same' else Decimal(str(spec.get('amount','0')))
        shift=int(spec.get('month_shift',0)); impact={'kind':spec['kind'],'category':category,'amount':amt}
        if shift: impact['budget_date']=add_months(posted_date.replace(day=1),shift)
        impacts.append(impact)
    return impacts

@db_transaction.atomic
def process_recurring_rule(rule, user=None, today=None):
    today=today or date.today(); generated=[]
    guard=0
    while rule.is_active and rule.next_run<=today and guard<100:
        if rule.end_date and rule.next_run>rule.end_date:
            rule.is_active=False; break
        if rule.auto_post:
            tx=create_transaction(household=rule.household,user=user,posted_date=rule.next_run,kind=rule.transaction_kind,description=rule.description,from_account=rule.from_account,to_account=rule.to_account,amount=rule.amount,impacts=recurring_impacts(rule,rule.next_run),notes=f'Generated by recurring rule: {rule.name}')
            generated.append(tx)
        rule.last_generated=rule.next_run
        rule.next_run=advance_date(rule.next_run,rule.frequency,rule.interval); guard+=1
    rule.save(update_fields=['last_generated','next_run','is_active'])
    return generated
