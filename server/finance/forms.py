from datetime import date, timedelta
from decimal import Decimal
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model
from django.db.models import Q
from .models import (Account, AccountOwner, BankLinkedAccount, BudgetImpact, Category, HouseholdSettings, ImportBatch, ImportProfile, ImportRule, LoanProfile,
                     Membership, RecurringRule, Reconciliation, SavingsGoal, Tag, Transaction)

class StyledFormMixin:
    def style(self):
        for f in self.fields.values():
            if isinstance(f.widget,(forms.CheckboxInput,forms.RadioSelect)): continue
            f.widget.attrs.setdefault('class','control')
    def __init__(self,*args,**kwargs): super().__init__(*args,**kwargs); self.style()

class HouseholdSetupForm(StyledFormMixin,forms.Form):
    household_name=forms.CharField(max_length=120,initial='Our Budget')
    load_existing_plan=forms.BooleanField(required=False,initial=True,label='Create starter categories',help_text='Adds a small generic set of funding, expense and savings categories. No example transactions or budget amounts are created.')

class AccountForm(StyledFormMixin,forms.ModelForm):
    owners=forms.ModelMultipleChoiceField(queryset=AccountOwner.objects.none(),required=True,widget=forms.CheckboxSelectMultiple,label='Owners',help_text='Select one person for a personal account, or multiple people for a joint account.')
    opening_balance=forms.DecimalField(max_digits=16,decimal_places=2,required=False,initial=0,help_text='Opening balance in this account currency. Enter assets as positive. Enter credit-card/loan balances as positive too; liabilities are stored with the correct sign.')
    opening_base_balance=forms.DecimalField(max_digits=16,decimal_places=2,required=False,help_text='For a foreign-currency account, enter the opening balance equivalent in the household base currency. Leave blank for a base-currency account.')
    monthly_release_amount=forms.DecimalField(max_digits=16,decimal_places=2,min_value=Decimal('0.01'),required=False,label='Expected monthly release',help_text='For restricted funds such as a blocked account. This is planning metadata; recurring posting is still controlled under Recurring.')
    class Meta:
        model=Account
        fields=['name','owners','account_type','purpose','institution','currency','include_in_net_worth','is_active','opening_balance','opening_base_balance','opening_date','monthly_release_amount','release_day','release_destination','notes']
        widgets={'opening_date':forms.DateInput(attrs={'type':'date'}),'notes':forms.Textarea(attrs={'rows':3})}
        labels={'purpose':'Financial purpose','release_day':'Expected release day','release_destination':'Expected release destination'}
    def __init__(self,*args,household=None,**kwargs):
        self.household=household
        super().__init__(*args,**kwargs)
        if household:
            owner_qs=AccountOwner.objects.filter(household=household).filter(Q(is_active=True)|Q(pk__in=(self.instance.owners.values_list('pk',flat=True) if self.instance and self.instance.pk else []))).order_by('sort_order','name')
            self.fields['owners'].queryset=owner_qs
            if self.instance and self.instance.pk:
                selected=list(self.instance.owners.values_list('pk',flat=True))
                if not selected and self.instance.owner_id: selected=[self.instance.owner_id]
                self.initial['owners']=selected
            dest_qs=Account.objects.filter(household=household,is_active=True)
            if self.instance and self.instance.pk: dest_qs=dest_qs.exclude(pk=self.instance.pk)
            self.fields['release_destination'].queryset=dest_qs.order_by('name')
        self.fields['purpose'].help_text='How this account behaves in your household finances. A checking account can still be marked as Savings if you use it only to save.'
        self.fields['owners'].help_text='Choose one household owner for a personal account. For a joint account, select both people. System/external accounts can keep their system owner.'
        if self.instance and self.instance.pk and self.instance.is_liability:
            self.initial['opening_balance']=abs(self.instance.opening_balance)
            if self.instance.opening_base_balance is not None:
                self.initial['opening_base_balance']=abs(self.instance.opening_base_balance)
    def clean(self):
        d=super().clean()
        v=d.get('opening_balance') or Decimal('0'); bv=d.get('opening_base_balance'); typ=d.get('account_type'); purpose=d.get('purpose')
        forced={Account.Type.SAVINGS:Account.Purpose.SAVINGS,Account.Type.RESTRICTED:Account.Purpose.RESTRICTED,Account.Type.CREDIT:Account.Purpose.LIABILITY,Account.Type.LOAN:Account.Purpose.LIABILITY,Account.Type.INVESTMENT:Account.Purpose.INVESTMENT,Account.Type.EXTERNAL:Account.Purpose.EXTERNAL}
        if typ in forced:
            purpose=forced[typ]; d['purpose']=purpose
        if purpose==Account.Purpose.EXTERNAL:
            d['account_type']=Account.Type.EXTERNAL
            typ=Account.Type.EXTERNAL
            d['include_in_net_worth']=False
        if typ in {Account.Type.CREDIT,Account.Type.LOAN}:
            if v>0: d['opening_balance']=-v
            if bv is not None and bv>0: d['opening_base_balance']=-bv
        if purpose!=Account.Purpose.RESTRICTED:
            d['monthly_release_amount']=None; d['release_day']=None; d['release_destination']=None
        elif d.get('release_destination') and self.instance and self.instance.pk and d['release_destination'].pk==self.instance.pk:
            self.add_error('release_destination','The release destination must be a different account.')
        if self.household:
            owners=d.get('owners')
            if owners is not None and any(o.household_id!=self.household.id for o in owners): self.add_error('owners','Choose owners from this household.')
            destination=d.get('release_destination')
            if destination and destination.household_id!=self.household.id: self.add_error('release_destination','Choose an account from this household.')
            base=self.household.settings.base_currency.upper()
            currency=(d.get('currency') or base).upper()
            d['currency']=currency
            if currency==base:
                d['opening_base_balance']=d.get('opening_balance') or Decimal('0')
            elif (d.get('opening_balance') or Decimal('0')) != 0 and d.get('opening_base_balance') is None:
                self.add_error('opening_base_balance',f'Enter the opening value in {base} so net worth can be calculated correctly.')
        return d

    def apply_owners(self,obj):
        selected=list(self.cleaned_data.get('owners') or [])
        if not selected:return
        if len(selected)==1:
            obj.owner=selected[0]
        else:
            joint=AccountOwner.objects.filter(household=self.household,kind=AccountOwner.Kind.JOINT,is_active=True).order_by('sort_order','id').first()
            obj.owner=joint or selected[0]
        obj.save(update_fields=['owner'])
        obj.owners.set(selected)

class AccountOwnerForm(StyledFormMixin,forms.ModelForm):
    class Meta:
        model=AccountOwner; fields=['name','kind','linked_user','is_active','sort_order']
        labels={'linked_user':'Linked household login','sort_order':'Display order'}
    def __init__(self,*args,household=None,**kwargs):
        self.household=household
        super().__init__(*args,**kwargs)
        if household:
            user_ids=household.memberships.values_list('user_id',flat=True)
            self.fields['linked_user'].queryset=get_user_model().objects.filter(pk__in=user_ids).order_by('username')
        self.fields['linked_user'].help_text='Optional. Link a person owner to their login so the UI can identify “You”.'
        self.fields['kind'].help_text='Use Person for an individual. Joint is retained for compatibility/summary grouping; a real joint account can select multiple Person owners. Use System / external for bookkeeping destinations.'
    def clean(self):
        d=super().clean(); kind=d.get('kind'); user=d.get('linked_user')
        if kind!=AccountOwner.Kind.PERSON: d['linked_user']=None
        if self.household and user and not self.household.memberships.filter(user=user).exists():
            self.add_error('linked_user','That user is not a member of this household.')
        if self.household and user:
            qs=AccountOwner.objects.filter(household=self.household,linked_user=user)
            if self.instance and self.instance.pk: qs=qs.exclude(pk=self.instance.pk)
            if qs.exists(): self.add_error('linked_user','That household login is already linked to another account owner.')
        return d

class CategoryForm(StyledFormMixin,forms.ModelForm):
    class Meta: model=Category; fields=['name','kind','parent','sort_order','is_active','notes']
    def __init__(self,*args,household=None,**kwargs):
        super().__init__(*args,**kwargs)
        if household: self.fields['parent'].queryset=Category.objects.filter(household=household)
        self.style()
    def clean(self):
        d=super().clean(); parent=d.get('parent'); kind=d.get('kind')
        if parent and parent.kind!=kind: self.add_error('parent','Parent and child categories must have the same type.')
        if parent and self.instance and self.instance.pk:
            if parent.pk==self.instance.pk:
                self.add_error('parent','A category cannot be its own parent.')
            seen=set(); node=parent
            while node and node.pk not in seen:
                if node.pk==self.instance.pk:
                    self.add_error('parent','This would create a category cycle.'); break
                seen.add(node.pk); node=node.parent
        return d

class SettingsForm(StyledFormMixin,forms.ModelForm):
    class Meta:
        model=HouseholdSettings
        fields=['base_currency','budget_cycle_mode','cycle_anchor_category','shift_day','shift_late_funding','savings_rate_basis','week_starts_monday']
        labels={
            'budget_cycle_mode':'Budget month timing',
            'cycle_anchor_category':'Salary / pay-cycle category',
            'shift_day':'Fallback salary day',
            'shift_late_funding':'Calendar mode: shift late funding',
        }
        help_texts={
            'budget_cycle_mode':'Salary / pay-cycle mode lets the real salary transaction open the following budget month.',
            'cycle_anchor_category':'Choose the funding category used for your main salary, for example Employment (Net).',
            'shift_day':'Used as a fallback before a salary transaction is known or imported.',
            'shift_late_funding':'Only used in Calendar month mode.',
        }
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        household=getattr(self.instance,'household',None)
        if household:
            self.fields['cycle_anchor_category'].queryset=Category.objects.filter(household=household,kind=Category.Kind.FUNDING,is_active=True).order_by('sort_order','name')
        self.style()
    def clean(self):
        d=super().clean()
        if d.get('budget_cycle_mode')==HouseholdSettings.BudgetCycleMode.PAY_CYCLE and not d.get('cycle_anchor_category'):
            self.add_error('cycle_anchor_category','Choose the funding category that represents the salary starting each budget cycle.')
        return d
    def clean_base_currency(self):
        value=(self.cleaned_data.get('base_currency') or 'EUR').upper()
        if self.instance and self.instance.pk and value!=self.instance.base_currency.upper():
            h=self.instance.household
            has_ledger=h.transactions.exists() or h.accounts.exclude(opening_balance=0).exists()
            if has_ledger:
                raise forms.ValidationError('The household base currency cannot be changed after financial balances or transactions exist, because historical base-currency values are already stored.')
        return value

class AccountChoiceField(forms.ModelChoiceField):
    def label_from_instance(self,obj):
        selected=list(obj.owners.all()) if getattr(obj,'pk',None) else []
        owner=' + '.join(o.name for o in selected) if selected else (obj.owner.name if getattr(obj,'owner_id',None) else 'Unassigned')
        return f'{obj.name} — {owner} · {obj.currency}'

class TransactionForm(StyledFormMixin,forms.Form):
    date=forms.DateField(initial=date.today,widget=forms.DateInput(attrs={'type':'date'}))
    kind=forms.ChoiceField(choices=Transaction.Kind.choices,initial=Transaction.Kind.EXPENSE)
    status=forms.ChoiceField(choices=Transaction.Status.choices,initial=Transaction.Status.CLEARED)
    description=forms.CharField(max_length=240)
    payee=forms.CharField(max_length=160,required=False)
    amount=forms.DecimalField(max_digits=16,decimal_places=2,min_value=Decimal('0.01'))
    from_account=AccountChoiceField(queryset=Account.objects.none())
    to_account=AccountChoiceField(queryset=Account.objects.none())
    destination_amount=forms.DecimalField(max_digits=16,decimal_places=2,min_value=Decimal('0.01'),required=False,help_text='Only needed if destination account uses another currency.')
    base_value=forms.DecimalField(max_digits=16,decimal_places=2,min_value=Decimal('0.01'),required=False,help_text='Household-base-currency value for cross-currency transfers.')
    tags=forms.ModelMultipleChoiceField(queryset=Tag.objects.none(),required=False,widget=forms.SelectMultiple(attrs={'size':4}))
    notes=forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':3}))
    attachment=forms.FileField(required=False)
    def __init__(self,*args,household=None,**kwargs):
        self.household=household
        super().__init__(*args,**kwargs)
        if household:
            qs=Account.objects.filter(household=household,is_active=True).select_related('owner')
            self.fields['from_account'].queryset=qs; self.fields['to_account'].queryset=qs
            self.fields['tags'].queryset=Tag.objects.filter(household=household)
        self.style()
    def clean(self):
        d=super().clean()
        if d.get('from_account') and d.get('to_account') and d['from_account']==d['to_account']:
            self.add_error('to_account','Choose a different destination account.')
        return d

class SavingsGoalForm(StyledFormMixin,forms.ModelForm):
    class Meta:
        model=SavingsGoal; fields=['name','target_amount','target_date','start_date','starting_amount','category','account','is_active','notes']
        widgets={'target_date':forms.DateInput(attrs={'type':'date'}),'start_date':forms.DateInput(attrs={'type':'date'}),'notes':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,household=None,**kwargs):
        super().__init__(*args,**kwargs)
        if household:
            self.fields['category'].queryset=Category.objects.filter(household=household,kind=Category.Kind.SAVINGS)
            self.fields['account'].queryset=Account.objects.filter(household=household,is_active=True)
        self.style()
    def clean(self):
        d=super().clean(); c=d.get('category'); a=d.get('account')
        if c and a: raise forms.ValidationError('Choose either a savings category or an account as the goal progress source, not both.')
        if not c and not a: raise forms.ValidationError('Choose a savings category or an account to measure progress.')
        return d

class LoanForm(StyledFormMixin,forms.ModelForm):
    class Meta:
        model=LoanProfile; fields=['account','lender','original_principal','annual_interest_rate','start_date','term_months','regular_payment','payment_day','notes']
        widgets={'start_date':forms.DateInput(attrs={'type':'date'}),'notes':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,household=None,**kwargs):
        super().__init__(*args,**kwargs)
        if household:
            qs=Account.objects.filter(household=household,account_type=Account.Type.LOAN,is_active=True)
            if not (self.instance and self.instance.pk): qs=qs.filter(loan_profile__isnull=True)
            self.fields['account'].queryset=qs
        self.style()

class RecurringRuleForm(StyledFormMixin,forms.ModelForm):
    impact_kind=forms.ChoiceField(choices=[('','No budget impact')]+list(BudgetImpact.Kind.choices),required=False,label='Budget impact 1')
    category=forms.ModelChoiceField(queryset=Category.objects.none(),required=False,label='Category 1')
    second_impact_kind=forms.ChoiceField(choices=[('','No second impact')]+list(BudgetImpact.Kind.choices),required=False,label='Budget impact 2')
    second_category=forms.ModelChoiceField(queryset=Category.objects.none(),required=False,label='Category 2')
    class Meta:
        model=RecurringRule; fields=['name','transaction_kind','description','amount','from_account','to_account','frequency','interval','start_date','end_date','next_run','auto_post','is_active','notes']
        widgets={'start_date':forms.DateInput(attrs={'type':'date'}),'end_date':forms.DateInput(attrs={'type':'date'}),'next_run':forms.DateInput(attrs={'type':'date'}),'notes':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,household=None,**kwargs):
        self.household=household
        super().__init__(*args,**kwargs)
        if household:
            qs=Account.objects.filter(household=household,is_active=True).select_related('owner')
            self.fields['from_account'].queryset=qs; self.fields['to_account'].queryset=qs
            cats=Category.objects.filter(household=household,is_active=True)
            self.fields['category'].queryset=cats; self.fields['second_category'].queryset=cats
        if self.instance and self.instance.pk and self.instance.budget_impacts:
            s=self.instance.budget_impacts[0]
            self.fields['impact_kind'].initial=s.get('kind','')
            self.fields['category'].initial=s.get('category_id')
            if len(self.instance.budget_impacts)>1:
                s2=self.instance.budget_impacts[1]; self.fields['second_impact_kind'].initial=s2.get('kind',''); self.fields['second_category'].initial=s2.get('category_id')
        self.style()
    def clean(self):
        d=super().clean(); k=d.get('impact_kind'); c=d.get('category')
        if d.get('from_account') and d.get('to_account') and d['from_account']==d['to_account']:
            self.add_error('to_account','Choose a different destination account.')
        if d.get('start_date') and d.get('end_date') and d['end_date']<d['start_date']:
            self.add_error('end_date','End date cannot be before the start date.')
        if d.get('start_date') and d.get('next_run') and d['next_run']<d['start_date']:
            self.add_error('next_run','Next run cannot be before the start date.')
        if d.get('end_date') and d.get('next_run') and d['next_run']>d['end_date']:
            self.add_error('next_run','Next run cannot be after the end date.')
        if self.household and d.get('from_account') and d.get('to_account'):
            base=self.household.settings.base_currency
            if d['from_account'].currency!=base or d['to_account'].currency!=base: raise forms.ValidationError('Recurring auto-post currently requires accounts in the household base currency. Create cross-currency transactions manually so the base value is explicit.')
        if k and not c: self.add_error('category','Choose a category.')
        if k and c and c.kind!=k: self.add_error('category','Category type must match the budget impact.')
        k2=d.get('second_impact_kind'); c2=d.get('second_category')
        if k2 and not c2: self.add_error('second_category','Choose a category.')
        if k2 and c2 and c2.kind!=k2: self.add_error('second_category','Category type must match the budget impact.')
        return d
    def save(self,commit=True):
        obj=super().save(commit=False)
        k=self.cleaned_data.get('impact_kind'); c=self.cleaned_data.get('category')
        obj.budget_impacts=[]
        if k and c: obj.budget_impacts.append({'kind':k,'category_id':c.id,'amount_mode':'same','month_shift':0})
        k2=self.cleaned_data.get('second_impact_kind'); c2=self.cleaned_data.get('second_category')
        if k2 and c2: obj.budget_impacts.append({'kind':k2,'category_id':c2.id,'amount_mode':'same','month_shift':0})
        if commit: obj.save()
        return obj

class ImportRuleForm(StyledFormMixin,forms.ModelForm):
    class Meta: model=ImportRule; fields=['name','priority','match_type','pattern','direction','transaction_kind','impact_kind','category','is_active']
    def clean(self):
        d=super().clean(); k=d.get('impact_kind'); c=d.get('category')
        if k and not c: self.add_error('category','Choose a category for this budget impact.')
        if k and c and c.kind!=k: self.add_error('category','Category type must match the impact type.')
        if d.get('match_type')==ImportRule.MatchType.REGEX and d.get('pattern'):
            import re
            try: re.compile(d['pattern'])
            except re.error as e: self.add_error('pattern',f'Invalid regular expression: {e}')
        return d
    def __init__(self,*args,household=None,**kwargs):
        super().__init__(*args,**kwargs)
        if household: self.fields['category'].queryset=Category.objects.filter(household=household,is_active=True)
        self.style()

class ImportUploadForm(StyledFormMixin,forms.ModelForm):
    class Meta: model=ImportBatch; fields=['account','profile','raw_file']
    def __init__(self,*args,household=None,**kwargs):
        self.household=household
        super().__init__(*args,**kwargs)
        if household:
            self.fields['account'].queryset=Account.objects.filter(household=household,is_active=True).exclude(account_type=Account.Type.EXTERNAL)
            self.fields['profile'].queryset=ImportProfile.objects.filter(household=household)
        self.style()
    def clean_account(self):
        a=self.cleaned_data['account']
        if self.household and a.currency!=self.household.settings.base_currency: raise forms.ValidationError('CSV import currently expects a base-currency bank account. Use a manual cross-currency transaction for foreign-currency accounts.')
        return a

class ImportMappingForm(StyledFormMixin,forms.Form):
    delimiter=forms.ChoiceField(choices=[(',', 'Comma'),(';','Semicolon'),('\t','Tab'),('|','Pipe')])
    encoding=forms.ChoiceField(choices=[('utf-8-sig','UTF-8'),('cp1252','Windows-1252'),('iso-8859-1','ISO-8859-1')])
    date_column=forms.ChoiceField(choices=[])
    description_column=forms.ChoiceField(choices=[])
    amount_column=forms.ChoiceField(choices=[],required=False)
    debit_column=forms.ChoiceField(choices=[],required=False)
    credit_column=forms.ChoiceField(choices=[],required=False)
    date_format=forms.CharField(initial='%d.%m.%Y')
    decimal_separator=forms.ChoiceField(choices=[(',','Comma'),('.','Dot')],initial=',')
    thousands_separator=forms.ChoiceField(choices=[('.','Dot'),(',','Comma'),('','None')],required=False,initial='.')
    invert_sign=forms.BooleanField(required=False)
    save_profile_as=forms.CharField(max_length=120,required=False)
    def __init__(self,*args,headers=None,initial=None,**kwargs):
        super().__init__(*args,initial=initial,**kwargs); choices=[(h,h) for h in (headers or [])]; blank=[('','—')]+choices
        for n in ['date_column','description_column']: self.fields[n].choices=choices
        for n in ['amount_column','debit_column','credit_column']: self.fields[n].choices=blank
        self.style()
    def clean(self):
        d=super().clean()
        if not d.get('amount_column') and not (d.get('debit_column') and d.get('credit_column')):
            raise forms.ValidationError('Choose one signed amount column, or both debit and credit columns.')
        return d

class ReconciliationForm(StyledFormMixin,forms.Form):
    statement_date=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    statement_balance=forms.DecimalField(max_digits=16,decimal_places=2,help_text='Statement balance in the account currency.')
    create_adjustment=forms.BooleanField(required=False,initial=False)
    adjustment_base_value=forms.DecimalField(max_digits=16,decimal_places=2,min_value=Decimal('0.01'),required=False,help_text='Only for a foreign-currency account when creating an adjustment: base-currency value of the adjustment.')
    note=forms.CharField(required=False,max_length=240)

class MemberCreateForm(StyledFormMixin,UserCreationForm):
    email=forms.EmailField(required=False); role=forms.ChoiceField(choices=Membership.Role.choices)
    class Meta(UserCreationForm.Meta): model=get_user_model(); fields=('username','email','role','password1','password2')

class MemberRoleForm(StyledFormMixin,forms.Form): role=forms.ChoiceField(choices=Membership.Role.choices)


class ReimbursementPairForm(StyledFormMixin,forms.Form):
    purchase=forms.ModelChoiceField(queryset=Transaction.objects.none(),label='Original purchase',empty_label='Choose the purchase…')
    def __init__(self,*args,household=None,reimbursement=None,**kwargs):
        super().__init__(*args,**kwargs)
        if household and reimbursement:
            cutoff=reimbursement.date-timedelta(days=180)
            self.fields['purchase'].queryset=(Transaction.objects.filter(
                household=household,date__gte=cutoff,date__lte=reimbursement.date,
                budget_impacts__kind=BudgetImpact.Kind.EXPENSE,
            ).exclude(pk=reimbursement.pk).exclude(purchase_reimbursement_link__isnull=False).distinct().order_by('-date','-id'))
            self.fields['purchase'].label_from_instance=lambda tx: f'{tx.date:%d %b %Y} — {tx.description} — {tx.base_amount:.2f} {household.settings.base_currency}'
        self.fields['purchase'].help_text='Choose the original purchase. The full incoming amount will be treated as the reimbursed share.'

class BankAccountMappingForm(StyledFormMixin,forms.ModelForm):
    class Meta:
        model=BankLinkedAccount
        fields=['account']
        labels={'account':'Budget Manager account'}
    def __init__(self,*args,household=None,**kwargs):
        super().__init__(*args,**kwargs)
        qs=Account.objects.none()
        if household:
            qs=Account.objects.filter(household=household,is_active=True).exclude(purpose=Account.Purpose.EXTERNAL)
            if self.instance and self.instance.currency:
                qs=qs.filter(currency__iexact=self.instance.currency)
        self.fields['account'].queryset=qs.select_related('owner').order_by('owner__sort_order','name')
        self.fields['account'].required=False
        self.fields['account'].help_text='Link this bank feed to the real account already used in Budget Manager. Leave blank to keep it disconnected from the ledger.'

class BankInboxImportForm(StyledFormMixin,forms.Form):
    category=forms.ModelChoiceField(queryset=Category.objects.none(),required=False,empty_label='No budget category')
    def __init__(self,*args,household=None,suggested_category=None,impact_kind=None,**kwargs):
        super().__init__(*args,**kwargs)
        if household:
            qs=Category.objects.filter(household=household,is_active=True)
            if impact_kind in (Category.Kind.FUNDING,Category.Kind.EXPENSE):
                qs=qs.filter(kind=impact_kind)
            self.fields['category'].queryset=qs.order_by('sort_order','name')
        if suggested_category:
            self.initial['category']=suggested_category.pk
        self.fields['category'].help_text='Optional. The suggestion is only a starting point; choose another category before importing whenever needed.'
