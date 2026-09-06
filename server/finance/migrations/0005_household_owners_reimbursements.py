from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion
from decimal import Decimal


def populate_account_owners(apps, schema_editor):
    Account=apps.get_model('finance','Account')
    AccountOwner=apps.get_model('finance','AccountOwner')
    for account in Account.objects.all().iterator():
        if not account.owner_id:
            continue
        owner=AccountOwner.objects.filter(pk=account.owner_id).first()
        if owner and owner.kind=='joint':
            people=list(AccountOwner.objects.filter(household_id=account.household_id,kind='person',is_active=True).values_list('id',flat=True))
            if people:
                account.owners.add(*people)
                continue
        account.owners.add(account.owner_id)


class Migration(migrations.Migration):
    dependencies=[('finance','0004_pay_cycle_budgeting')]
    operations=[
        migrations.AddField(
            model_name='account',name='owners',
            field=models.ManyToManyField(blank=True,help_text='One or more household owners. Select multiple people for a joint account.',related_name='shared_accounts',to='finance.accountowner'),
        ),
        migrations.CreateModel(
            name='ReimbursementLink',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('amount',models.DecimalField(decimal_places=2,max_digits=16,validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('purchase_impacts_before',models.JSONField(blank=True,default=list)),
                ('reimbursement_impacts_before',models.JSONField(blank=True,default=list)),
                ('reimbursement_kind_before',models.CharField(blank=True,max_length=16)),
                ('created_at',models.DateTimeField(auto_now_add=True)),
                ('created_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,to=settings.AUTH_USER_MODEL)),
                ('household',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='reimbursement_links',to='finance.household')),
                ('purchase',models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,related_name='purchase_reimbursement_link',to='finance.transaction')),
                ('reimbursement',models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,related_name='reimbursement_link',to='finance.transaction')),
            ],
            options={'ordering':['-created_at']},
        ),
        migrations.RunPython(populate_account_owners,migrations.RunPython.noop),
    ]
