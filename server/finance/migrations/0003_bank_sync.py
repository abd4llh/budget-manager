from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[('finance','0002_account_ownership_and_purpose')]
    operations=[
        migrations.CreateModel(
            name='BankConnection',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('provider',models.CharField(choices=[('enable_banking','Enable Banking')],default='enable_banking',max_length=24)),
                ('aspsp_name',models.CharField(max_length=160)),('country',models.CharField(default='DE',max_length=2)),('psu_type',models.CharField(default='personal',max_length=16)),
                ('session_id',models.CharField(blank=True,max_length=64)),('psu_id_hash',models.CharField(blank=True,max_length=128)),
                ('status',models.CharField(choices=[('authorized','Authorized'),('expired','Expired'),('revoked','Revoked'),('closed','Closed'),('error','Error')],default='authorized',max_length=16)),
                ('valid_until',models.DateTimeField(blank=True,null=True)),('authorized_at',models.DateTimeField(blank=True,null=True)),('last_synced_at',models.DateTimeField(blank=True,null=True)),('last_error',models.TextField(blank=True)),
                ('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),
                ('created_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='budget_bank_connections',to=settings.AUTH_USER_MODEL)),
                ('household',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='bank_connections',to='finance.household')),
            ],
            options={'ordering':['aspsp_name','id']},
        ),
        migrations.CreateModel(
            name='BankLinkedAccount',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('provider_account_uid',models.CharField(max_length=64)),('identification_hash',models.CharField(max_length=512)),('identification_hashes',models.JSONField(blank=True,default=list)),
                ('name',models.CharField(blank=True,max_length=180)),('currency',models.CharField(blank=True,max_length=3)),('masked_iban',models.CharField(blank=True,max_length=40)),('product',models.CharField(blank=True,max_length=160)),('cash_account_type',models.CharField(blank=True,max_length=32)),
                ('last_balance',models.DecimalField(blank=True,decimal_places=2,max_digits=16,null=True)),('last_balance_type',models.CharField(blank=True,max_length=32)),('last_balance_at',models.DateTimeField(blank=True,null=True)),('last_transaction_sync',models.DateTimeField(blank=True,null=True)),('is_active',models.BooleanField(default=True)),
                ('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),
                ('account',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='bank_links',to='finance.account')),
                ('connection',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='linked_accounts',to='finance.bankconnection')),
            ],
            options={'ordering':['connection__aspsp_name','name','id']},
        ),
        migrations.AddConstraint(model_name='banklinkedaccount',constraint=models.UniqueConstraint(fields=('connection','identification_hash'),name='unique_bank_link_ident_hash')),
        migrations.CreateModel(
            name='BankAuthorizationAttempt',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('state',models.CharField(max_length=80,unique=True)),('aspsp_name',models.CharField(max_length=160)),('country',models.CharField(default='DE',max_length=2)),('authorization_id',models.CharField(blank=True,max_length=80)),('psu_id_hash',models.CharField(blank=True,max_length=128)),('redirect_url',models.URLField(max_length=500)),('created_at',models.DateTimeField(auto_now_add=True)),('completed_at',models.DateTimeField(blank=True,null=True)),('error',models.TextField(blank=True)),
                ('connection',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.CASCADE,related_name='authorization_attempts',to='finance.bankconnection')),
                ('household',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='bank_authorization_attempts',to='finance.household')),
                ('user',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='budget_bank_authorizations',to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering':['-created_at']},
        ),
        migrations.CreateModel(
            name='BankSyncTransaction',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('provider_tx_key',models.CharField(max_length=64)),('entry_reference',models.CharField(blank=True,max_length=240)),('booking_date',models.DateField()),('value_date',models.DateField(blank=True,null=True)),('amount',models.DecimalField(decimal_places=2,help_text='Signed amount: positive into the linked account.',max_digits=16)),('currency',models.CharField(max_length=3)),('description',models.CharField(max_length=500)),('counterparty',models.CharField(blank=True,max_length=240)),('provider_status',models.CharField(blank=True,max_length=16)),('raw_data',models.JSONField(blank=True,default=dict)),('suggested_kind',models.CharField(blank=True,choices=[('income','Income'),('expense','Expense'),('transfer','Transfer'),('savings','Savings'),('release','Restricted-funds release'),('debt','Debt payment'),('adjustment','Balance adjustment'),('other','Other')],max_length=12)),('suggested_impact_kind',models.CharField(blank=True,choices=[('funding','Funding available'),('expense','Expense'),('savings','Savings allocation')],max_length=10)),('status',models.CharField(choices=[('pending','Needs review'),('imported','Imported'),('ignored','Ignored')],default='pending',max_length=10)),('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),
                ('bank_account',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='sync_transactions',to='finance.banklinkedaccount')),
                ('suggested_category',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='bank_sync_suggestions',to='finance.category')),
                ('transaction',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='bank_sync_rows',to='finance.transaction')),
            ],
            options={'ordering':['-booking_date','-id']},
        ),
        migrations.AddConstraint(model_name='banksynctransaction',constraint=models.UniqueConstraint(fields=('bank_account','provider_tx_key'),name='unique_bank_sync_tx_key')),
    ]
