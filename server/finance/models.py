from decimal import Decimal
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Sum

ZERO=Decimal('0.00')

class Household(models.Model):
    name=models.CharField(max_length=120)
    created_at=models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.name

class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER='owner','Owner'; MEMBER='member','Member'; VIEWER='viewer','Viewer'
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='memberships')
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='budget_memberships')
    role=models.CharField(max_length=10,choices=Role.choices,default=Role.MEMBER)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['household','user'],name='unique_household_user')]

class MobileApiToken(models.Model):
    """Revocable bearer token used only by first-party Budget Manager mobile clients.

    Only a SHA-256 digest is stored. The raw token is shown once to the client.
    """
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='budget_mobile_tokens')
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='mobile_api_tokens')
    name=models.CharField(max_length=120,default='Android')
    token_hash=models.CharField(max_length=64,unique=True,db_index=True)
    created_at=models.DateTimeField(auto_now_add=True)
    last_used_at=models.DateTimeField(null=True,blank=True)
    expires_at=models.DateTimeField(null=True,blank=True)
    is_active=models.BooleanField(default=True)
    class Meta:
        ordering=['-last_used_at','-created_at']
    def __str__(self): return f'{self.name} — {self.user}'

class HouseholdSettings(models.Model):
    class SavingsRateBasis(models.TextChoices):
        ALLOCATED='allocated','Savings allocated / funding'
        SURPLUS='surplus','(Funding - expenses) / funding'
    class BudgetCycleMode(models.TextChoices):
        CALENDAR='calendar','Calendar month'
        PAY_CYCLE='pay_cycle','Salary / pay-cycle month'
    household=models.OneToOneField(Household,on_delete=models.CASCADE,related_name='settings')
    base_currency=models.CharField(max_length=3,default='EUR',validators=[RegexValidator(r'^[A-Za-z]{3}$','Use a 3-letter currency code such as EUR.')])
    budget_cycle_mode=models.CharField(max_length=12,choices=BudgetCycleMode.choices,default=BudgetCycleMode.CALENDAR)
    cycle_anchor_category=models.ForeignKey('Category',on_delete=models.SET_NULL,null=True,blank=True,related_name='+',limit_choices_to={'kind':'funding'},help_text='Funding category whose transaction date opens the following budget month.')
    shift_late_funding=models.BooleanField(default=True)
    shift_day=models.PositiveSmallIntegerField(default=27,validators=[MinValueValidator(1),MaxValueValidator(31)])
    savings_rate_basis=models.CharField(max_length=12,choices=SavingsRateBasis.choices,default=SavingsRateBasis.ALLOCATED)
    week_starts_monday=models.BooleanField(default=True)


class AccountOwner(models.Model):
    class Kind(models.TextChoices):
        PERSON='person','Person'
        JOINT='joint','Joint'
        SYSTEM='system','System / external'
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='account_owners')
    name=models.CharField(max_length=120)
    kind=models.CharField(max_length=12,choices=Kind.choices,default=Kind.PERSON)
    linked_user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='budget_account_owners')
    is_active=models.BooleanField(default=True)
    sort_order=models.PositiveIntegerField(default=100)
    class Meta:
        ordering=['sort_order','name']
        constraints=[models.UniqueConstraint(fields=['household','name'],name='unique_account_owner_name_per_household')]
    def __str__(self): return self.name

class Account(models.Model):
    class Type(models.TextChoices):
        CHECKING='checking','Checking'; SAVINGS='savings','Savings'; CASH='cash','Cash'
        RESTRICTED='restricted','Restricted / blocked'; CREDIT='credit','Credit card'
        LOAN='loan','Loan'; INVESTMENT='investment','Investment'; ASSET='asset','Other asset'
        EXTERNAL='external','External / outside tracked finances'
    class Purpose(models.TextChoices):
        AVAILABLE='available','Available / everyday money'
        SAVINGS='savings','Savings'
        RESTRICTED='restricted','Restricted funds'
        INVESTMENT='investment','Investment / long term'
        LIABILITY='liability','Debt / liability'
        EXTERNAL='external','External / bookkeeping'
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='accounts')
    owner=models.ForeignKey(AccountOwner,on_delete=models.PROTECT,null=True,blank=True,related_name='accounts')
    owners=models.ManyToManyField(AccountOwner,blank=True,related_name='shared_accounts',help_text='One or more household owners. Select multiple people for a joint account.')
    name=models.CharField(max_length=120)
    account_type=models.CharField(max_length=16,choices=Type.choices,default=Type.CHECKING)
    purpose=models.CharField(max_length=12,choices=Purpose.choices,default=Purpose.AVAILABLE)
    institution=models.CharField(max_length=120,blank=True)
    currency=models.CharField(max_length=3,default='EUR',validators=[RegexValidator(r'^[A-Za-z]{3}$','Use a 3-letter currency code such as EUR.')])
    include_in_net_worth=models.BooleanField(default=True)
    is_active=models.BooleanField(default=True)
    opening_balance=models.DecimalField(max_digits=16,decimal_places=2,default=ZERO,help_text='Signed balance in the account currency; liabilities are negative.')
    opening_base_balance=models.DecimalField(max_digits=16,decimal_places=2,null=True,blank=True,help_text='Signed opening value in the household base currency. Required for non-base-currency accounts with a non-zero opening balance.')
    opening_date=models.DateField(null=True,blank=True)
    monthly_release_amount=models.DecimalField(max_digits=16,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(Decimal('0.01'))],help_text='Optional expected monthly release for restricted accounts.')
    release_day=models.PositiveSmallIntegerField(null=True,blank=True,validators=[MinValueValidator(1),MaxValueValidator(31)],help_text='Optional expected day of month for the release.')
    release_destination=models.ForeignKey('self',on_delete=models.SET_NULL,null=True,blank=True,related_name='planned_incoming_releases',help_text='Where a restricted-account release is expected to land.')
    notes=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering=['name']; constraints=[models.UniqueConstraint(fields=['household','name'],name='unique_account_name_per_household')]
    def __str__(self): return self.name
    @property
    def is_liability(self): return self.account_type in {self.Type.CREDIT,self.Type.LOAN}
    @property
    def owner_name(self): return self.owner.name if self.owner_id else 'Unassigned'
    @property
    def balance(self):
        posted=self.entries.aggregate(total=Sum('amount'))['total'] or ZERO
        return self.opening_balance+posted
    @property
    def display_balance(self): return -self.balance if self.is_liability else self.balance
    @property
    def base_balance(self):
        posted=self.entries.aggregate(total=Sum('base_amount'))['total'] or ZERO
        opening=self.opening_base_balance if self.opening_base_balance is not None else self.opening_balance
        return opening+posted
    @property
    def display_base_balance(self): return -self.base_balance if self.is_liability else self.base_balance

class Category(models.Model):
    class Kind(models.TextChoices):
        FUNDING='funding','Funding'; EXPENSE='expense','Expense'; SAVINGS='savings','Savings'
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='categories')
    name=models.CharField(max_length=120)
    kind=models.CharField(max_length=10,choices=Kind.choices)
    parent=models.ForeignKey('self',on_delete=models.SET_NULL,null=True,blank=True,related_name='children')
    is_active=models.BooleanField(default=True)
    sort_order=models.PositiveIntegerField(default=100)
    notes=models.CharField(max_length=240,blank=True)
    class Meta:
        ordering=['kind','sort_order','name']; constraints=[models.UniqueConstraint(fields=['household','kind','name'],name='unique_category_per_kind')]
    def __str__(self): return self.name

class Tag(models.Model):
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='tags')
    name=models.CharField(max_length=50)
    class Meta:
        ordering=['name']; constraints=[models.UniqueConstraint(fields=['household','name'],name='unique_tag_per_household')]
    def __str__(self): return self.name

class Transaction(models.Model):
    class Kind(models.TextChoices):
        INCOME='income','Income'; EXPENSE='expense','Expense'; TRANSFER='transfer','Transfer'
        SAVINGS='savings','Savings'; RELEASE='release','Restricted-funds release'; DEBT='debt','Debt payment'
        ADJUSTMENT='adjustment','Balance adjustment'; OTHER='other','Other'
    class Status(models.TextChoices):
        PENDING='pending','Pending'; CLEARED='cleared','Cleared'; RECONCILED='reconciled','Reconciled'
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='transactions')
    date=models.DateField(db_index=True)
    kind=models.CharField(max_length=12,choices=Kind.choices,default=Kind.OTHER)
    status=models.CharField(max_length=12,choices=Status.choices,default=Status.CLEARED)
    description=models.CharField(max_length=240)
    payee=models.CharField(max_length=160,blank=True)
    notes=models.TextField(blank=True)
    external_id=models.CharField(max_length=160,blank=True)
    import_fingerprint=models.CharField(max_length=64,blank=True,db_index=True)
    tags=models.ManyToManyField(Tag,blank=True,related_name='transactions')
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: ordering=['-date','-id']
    def __str__(self): return f'{self.date} — {self.description}'
    @property
    def base_amount(self):
        e=self.entries.order_by('id').first(); return abs(e.base_amount) if e else ZERO

class TransactionEntry(models.Model):
    transaction=models.ForeignKey(Transaction,on_delete=models.CASCADE,related_name='entries')
    account=models.ForeignKey(Account,on_delete=models.PROTECT,related_name='entries')
    amount=models.DecimalField(max_digits=16,decimal_places=2,help_text='Signed amount in account currency.')
    base_amount=models.DecimalField(max_digits=16,decimal_places=2,help_text='Signed amount in household base currency; transaction base entries must sum to zero.')
    memo=models.CharField(max_length=160,blank=True)
    class Meta: ordering=['id']

class BudgetImpact(models.Model):
    class Kind(models.TextChoices):
        FUNDING='funding','Funding available'; EXPENSE='expense','Expense'; SAVINGS='savings','Savings allocation'
    transaction=models.ForeignKey(Transaction,on_delete=models.CASCADE,related_name='budget_impacts')
    kind=models.CharField(max_length=10,choices=Kind.choices)
    category=models.ForeignKey(Category,on_delete=models.PROTECT,related_name='budget_impacts')
    amount=models.DecimalField(max_digits=16,decimal_places=2,validators=[MinValueValidator(ZERO)])
    budget_date=models.DateField(db_index=True)
    budget_date_locked=models.BooleanField(default=False,help_text='If enabled, this impact keeps its manually selected budget month instead of following household cycle rules.')
    memo=models.CharField(max_length=160,blank=True)
    class Meta: ordering=['budget_date','id']

class ReimbursementLink(models.Model):
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='reimbursement_links')
    purchase=models.OneToOneField(Transaction,on_delete=models.PROTECT,related_name='purchase_reimbursement_link')
    reimbursement=models.OneToOneField(Transaction,on_delete=models.PROTECT,related_name='reimbursement_link')
    amount=models.DecimalField(max_digits=16,decimal_places=2,validators=[MinValueValidator(Decimal('0.01'))])
    purchase_impacts_before=models.JSONField(default=list,blank=True)
    reimbursement_impacts_before=models.JSONField(default=list,blank=True)
    reimbursement_kind_before=models.CharField(max_length=16,blank=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']
    def __str__(self): return f'{self.reimbursement} reimburses {self.purchase}'

class Attachment(models.Model):
    transaction=models.ForeignKey(Transaction,on_delete=models.CASCADE,related_name='attachments')
    file=models.FileField(upload_to='receipts/%Y/%m/')
    label=models.CharField(max_length=120,blank=True)
    uploaded_at=models.DateTimeField(auto_now_add=True)

class BudgetPlan(models.Model):
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='budget_plans')
    month=models.DateField(db_index=True,help_text='First day of budget month.')
    category=models.ForeignKey(Category,on_delete=models.CASCADE,related_name='budget_plans')
    amount=models.DecimalField(max_digits=16,decimal_places=2,default=ZERO,validators=[MinValueValidator(ZERO)])
    note=models.CharField(max_length=160,blank=True)
    class Meta:
        ordering=['month','category__kind','category__sort_order','category__name']
        constraints=[models.UniqueConstraint(fields=['household','month','category'],name='unique_month_category_budget')]

class SavingsGoal(models.Model):
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='savings_goals')
    name=models.CharField(max_length=120)
    target_amount=models.DecimalField(max_digits=16,decimal_places=2,validators=[MinValueValidator(Decimal('0.01'))])
    target_date=models.DateField(null=True,blank=True)
    start_date=models.DateField(null=True,blank=True)
    starting_amount=models.DecimalField(max_digits=16,decimal_places=2,default=ZERO)
    category=models.ForeignKey(Category,on_delete=models.SET_NULL,null=True,blank=True,limit_choices_to={'kind':Category.Kind.SAVINGS})
    account=models.ForeignKey(Account,on_delete=models.SET_NULL,null=True,blank=True,related_name='savings_goals')
    is_active=models.BooleanField(default=True)
    notes=models.TextField(blank=True)
    def __str__(self): return self.name

class LoanProfile(models.Model):
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='loans')
    account=models.OneToOneField(Account,on_delete=models.CASCADE,related_name='loan_profile',limit_choices_to={'account_type':Account.Type.LOAN})
    lender=models.CharField(max_length=120,blank=True)
    original_principal=models.DecimalField(max_digits=16,decimal_places=2,validators=[MinValueValidator(Decimal('0.01'))])
    annual_interest_rate=models.DecimalField(max_digits=7,decimal_places=4,default=ZERO,validators=[MinValueValidator(ZERO)],help_text='APR percentage, e.g. 5.25')
    start_date=models.DateField()
    term_months=models.PositiveIntegerField(null=True,blank=True)
    regular_payment=models.DecimalField(max_digits=16,decimal_places=2,default=ZERO,validators=[MinValueValidator(ZERO)])
    payment_day=models.PositiveSmallIntegerField(default=1,validators=[MinValueValidator(1),MaxValueValidator(31)])
    notes=models.TextField(blank=True)
    def __str__(self): return self.account.name

class RecurringRule(models.Model):
    class Frequency(models.TextChoices):
        WEEKLY='weekly','Weekly'; MONTHLY='monthly','Monthly'; YEARLY='yearly','Yearly'
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='recurring_rules')
    name=models.CharField(max_length=120)
    transaction_kind=models.CharField(max_length=12,choices=Transaction.Kind.choices)
    description=models.CharField(max_length=240)
    amount=models.DecimalField(max_digits=16,decimal_places=2,validators=[MinValueValidator(Decimal('0.01'))])
    from_account=models.ForeignKey(Account,on_delete=models.PROTECT,related_name='recurring_outgoing')
    to_account=models.ForeignKey(Account,on_delete=models.PROTECT,related_name='recurring_incoming')
    frequency=models.CharField(max_length=10,choices=Frequency.choices,default=Frequency.MONTHLY)
    interval=models.PositiveSmallIntegerField(default=1,validators=[MinValueValidator(1),MaxValueValidator(24)])
    start_date=models.DateField(); end_date=models.DateField(null=True,blank=True); next_run=models.DateField()
    auto_post=models.BooleanField(default=False)
    budget_impacts=models.JSONField(default=list,blank=True,help_text='List of {kind, category_id, amount_mode, amount, month_shift}.')
    is_active=models.BooleanField(default=True)
    notes=models.TextField(blank=True)
    last_generated=models.DateField(null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.name

class ImportRule(models.Model):
    class MatchType(models.TextChoices):
        CONTAINS='contains','Contains'; STARTS='starts','Starts with'; EXACT='exact','Exact'; REGEX='regex','Regular expression'
    class Direction(models.TextChoices):
        ANY='any','Any'; IN='in','Money in'; OUT='out','Money out'
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='import_rules')
    name=models.CharField(max_length=120)
    priority=models.PositiveIntegerField(default=100)
    match_type=models.CharField(max_length=10,choices=MatchType.choices,default=MatchType.CONTAINS)
    pattern=models.CharField(max_length=240)
    direction=models.CharField(max_length=3,choices=Direction.choices,default=Direction.ANY)
    transaction_kind=models.CharField(max_length=12,choices=Transaction.Kind.choices,blank=True)
    impact_kind=models.CharField(max_length=10,choices=BudgetImpact.Kind.choices,blank=True)
    category=models.ForeignKey(Category,on_delete=models.SET_NULL,null=True,blank=True)
    is_active=models.BooleanField(default=True)
    class Meta: ordering=['priority','name']
    def __str__(self): return self.name

class ImportProfile(models.Model):
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='import_profiles')
    name=models.CharField(max_length=120)
    account=models.ForeignKey(Account,on_delete=models.CASCADE,related_name='import_profiles')
    delimiter=models.CharField(max_length=4,default=',')
    encoding=models.CharField(max_length=30,default='utf-8-sig')
    date_column=models.CharField(max_length=120)
    description_column=models.CharField(max_length=120)
    amount_column=models.CharField(max_length=120,blank=True)
    debit_column=models.CharField(max_length=120,blank=True)
    credit_column=models.CharField(max_length=120,blank=True)
    date_format=models.CharField(max_length=40,default='%d.%m.%Y')
    decimal_separator=models.CharField(max_length=1,default=',')
    thousands_separator=models.CharField(max_length=1,default='.')
    invert_sign=models.BooleanField(default=False)
    class Meta: constraints=[models.UniqueConstraint(fields=['household','name'],name='unique_import_profile_name')]
    def __str__(self): return self.name

class ImportBatch(models.Model):
    class Status(models.TextChoices):
        UPLOADED='uploaded','Uploaded'; PARSED='parsed','Parsed'; IMPORTED='imported','Imported'; FAILED='failed','Failed'
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='import_batches')
    account=models.ForeignKey(Account,on_delete=models.PROTECT,related_name='import_batches')
    profile=models.ForeignKey(ImportProfile,on_delete=models.SET_NULL,null=True,blank=True)
    raw_file=models.FileField(upload_to='imports/%Y/%m/')
    original_filename=models.CharField(max_length=240)
    status=models.CharField(max_length=10,choices=Status.choices,default=Status.UPLOADED)
    headers=models.JSONField(default=list,blank=True); mapping=models.JSONField(default=dict,blank=True)
    imported_count=models.PositiveIntegerField(default=0); duplicate_count=models.PositiveIntegerField(default=0); error_count=models.PositiveIntegerField(default=0)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    error_message=models.TextField(blank=True)
    class Meta: ordering=['-created_at']

class ImportRow(models.Model):
    class Status(models.TextChoices):
        PENDING='pending','Pending'; DUPLICATE='duplicate','Duplicate'; IMPORTED='imported','Imported'; ERROR='error','Error'; SKIPPED='skipped','Skipped'
    batch=models.ForeignKey(ImportBatch,on_delete=models.CASCADE,related_name='rows')
    row_number=models.PositiveIntegerField()
    transaction_date=models.DateField(null=True,blank=True)
    description=models.CharField(max_length=500,blank=True)
    amount=models.DecimalField(max_digits=16,decimal_places=2,null=True,blank=True,help_text='Signed: positive into selected bank account.')
    fingerprint=models.CharField(max_length=64,blank=True,db_index=True)
    raw_data=models.JSONField(default=dict)
    suggested_kind=models.CharField(max_length=12,choices=Transaction.Kind.choices,blank=True)
    suggested_category=models.ForeignKey(Category,on_delete=models.SET_NULL,null=True,blank=True)
    suggested_impact_kind=models.CharField(max_length=10,choices=BudgetImpact.Kind.choices,blank=True)
    status=models.CharField(max_length=10,choices=Status.choices,default=Status.PENDING)
    transaction=models.ForeignKey(Transaction,on_delete=models.SET_NULL,null=True,blank=True,related_name='import_rows')
    error=models.CharField(max_length=500,blank=True)
    class Meta:
        ordering=['row_number']; constraints=[models.UniqueConstraint(fields=['batch','row_number'],name='unique_import_batch_row')]

class Reconciliation(models.Model):
    account=models.ForeignKey(Account,on_delete=models.CASCADE,related_name='reconciliations')
    statement_date=models.DateField()
    statement_balance=models.DecimalField(max_digits=16,decimal_places=2)
    book_balance=models.DecimalField(max_digits=16,decimal_places=2)
    difference=models.DecimalField(max_digits=16,decimal_places=2)
    note=models.CharField(max_length=240,blank=True)
    reconciled_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-statement_date']

class AuditLog(models.Model):
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='audit_logs')
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True)
    action=models.CharField(max_length=30)
    entity=models.CharField(max_length=80)
    entity_id=models.CharField(max_length=80,blank=True)
    summary=models.CharField(max_length=300)
    metadata=models.JSONField(default=dict,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']

class BankConnection(models.Model):
    class Provider(models.TextChoices):
        ENABLE_BANKING='enable_banking','Enable Banking'
    class Status(models.TextChoices):
        AUTHORIZED='authorized','Authorized'
        EXPIRED='expired','Expired'
        REVOKED='revoked','Revoked'
        CLOSED='closed','Closed'
        ERROR='error','Error'
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='bank_connections')
    provider=models.CharField(max_length=24,choices=Provider.choices,default=Provider.ENABLE_BANKING)
    aspsp_name=models.CharField(max_length=160)
    country=models.CharField(max_length=2,default='DE')
    psu_type=models.CharField(max_length=16,default='personal')
    session_id=models.CharField(max_length=64,blank=True)
    psu_id_hash=models.CharField(max_length=128,blank=True)
    status=models.CharField(max_length=16,choices=Status.choices,default=Status.AUTHORIZED)
    valid_until=models.DateTimeField(null=True,blank=True)
    authorized_at=models.DateTimeField(null=True,blank=True)
    last_synced_at=models.DateTimeField(null=True,blank=True)
    last_error=models.TextField(blank=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True,blank=True,related_name='budget_bank_connections')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['aspsp_name','id']
    def __str__(self): return f'{self.aspsp_name} ({self.country})'

class BankLinkedAccount(models.Model):
    connection=models.ForeignKey(BankConnection,on_delete=models.CASCADE,related_name='linked_accounts')
    account=models.ForeignKey(Account,on_delete=models.SET_NULL,null=True,blank=True,related_name='bank_links')
    provider_account_uid=models.CharField(max_length=64)
    identification_hash=models.CharField(max_length=512)
    identification_hashes=models.JSONField(default=list,blank=True)
    name=models.CharField(max_length=180,blank=True)
    currency=models.CharField(max_length=3,blank=True)
    masked_iban=models.CharField(max_length=40,blank=True)
    product=models.CharField(max_length=160,blank=True)
    cash_account_type=models.CharField(max_length=32,blank=True)
    last_balance=models.DecimalField(max_digits=16,decimal_places=2,null=True,blank=True)
    last_balance_type=models.CharField(max_length=32,blank=True)
    last_balance_at=models.DateTimeField(null=True,blank=True)
    last_transaction_sync=models.DateTimeField(null=True,blank=True)
    is_active=models.BooleanField(default=True)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['connection__aspsp_name','name','id']
        constraints=[models.UniqueConstraint(fields=['connection','identification_hash'],name='unique_bank_link_ident_hash')]
    def __str__(self): return self.name or self.masked_iban or self.provider_account_uid

class BankAuthorizationAttempt(models.Model):
    household=models.ForeignKey(Household,on_delete=models.CASCADE,related_name='bank_authorization_attempts')
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='budget_bank_authorizations')
    connection=models.ForeignKey(BankConnection,on_delete=models.CASCADE,null=True,blank=True,related_name='authorization_attempts')
    state=models.CharField(max_length=80,unique=True)
    aspsp_name=models.CharField(max_length=160)
    country=models.CharField(max_length=2,default='DE')
    authorization_id=models.CharField(max_length=80,blank=True)
    psu_id_hash=models.CharField(max_length=128,blank=True)
    redirect_url=models.URLField(max_length=500)
    created_at=models.DateTimeField(auto_now_add=True)
    completed_at=models.DateTimeField(null=True,blank=True)
    error=models.TextField(blank=True)
    class Meta: ordering=['-created_at']

class BankSyncTransaction(models.Model):
    class Status(models.TextChoices):
        PENDING='pending','Needs review'
        IMPORTED='imported','Imported'
        IGNORED='ignored','Ignored'
    bank_account=models.ForeignKey(BankLinkedAccount,on_delete=models.CASCADE,related_name='sync_transactions')
    provider_tx_key=models.CharField(max_length=64)
    entry_reference=models.CharField(max_length=240,blank=True)
    booking_date=models.DateField()
    value_date=models.DateField(null=True,blank=True)
    amount=models.DecimalField(max_digits=16,decimal_places=2,help_text='Signed amount: positive into the linked account.')
    currency=models.CharField(max_length=3)
    description=models.CharField(max_length=500)
    counterparty=models.CharField(max_length=240,blank=True)
    provider_status=models.CharField(max_length=16,blank=True)
    raw_data=models.JSONField(default=dict,blank=True)
    suggested_kind=models.CharField(max_length=12,choices=Transaction.Kind.choices,blank=True)
    suggested_category=models.ForeignKey(Category,on_delete=models.SET_NULL,null=True,blank=True,related_name='bank_sync_suggestions')
    suggested_impact_kind=models.CharField(max_length=10,choices=BudgetImpact.Kind.choices,blank=True)
    status=models.CharField(max_length=10,choices=Status.choices,default=Status.PENDING)
    transaction=models.ForeignKey(Transaction,on_delete=models.SET_NULL,null=True,blank=True,related_name='bank_sync_rows')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['-booking_date','-id']
        constraints=[models.UniqueConstraint(fields=['bank_account','provider_tx_key'],name='unique_bank_sync_tx_key')]
    def __str__(self): return f'{self.booking_date} {self.amount} {self.currency} — {self.description}'
