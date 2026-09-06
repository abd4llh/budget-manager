from decimal import Decimal
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


def configure_existing_accounts(apps, schema_editor):
    Household=apps.get_model('finance','Household')
    Membership=apps.get_model('finance','Membership')
    AccountOwner=apps.get_model('finance','AccountOwner')
    Account=apps.get_model('finance','Account')

    for household in Household.objects.all():
        membership=(Membership.objects.filter(household=household,role='owner').select_related('user').first()
                    or Membership.objects.filter(household=household).select_related('user').first())
        user=membership.user if membership else None
        if user:
            full_name=' '.join(x for x in [getattr(user,'first_name',''),getattr(user,'last_name','')] if x).strip()
            primary_name=full_name or getattr(user,'username','Primary owner')
        else:
            primary_name='Primary owner'
        primary,_=AccountOwner.objects.get_or_create(household=household,name=primary_name,defaults={'kind':'person','linked_user':user,'sort_order':10})
        if user and not primary.linked_user_id:
            primary.linked_user_id=user.id; primary.save(update_fields=['linked_user'])
        joint,_=AccountOwner.objects.get_or_create(household=household,name='Joint',defaults={'kind':'joint','sort_order':30})
        system,_=AccountOwner.objects.get_or_create(household=household,name='System / External',defaults={'kind':'system','sort_order':900})

        for account in Account.objects.filter(household=household):
            typ=account.account_type
            if typ=='external':
                account.owner=system; account.purpose='external'; account.include_in_net_worth=False
            elif typ=='restricted':
                account.owner=primary; account.purpose='restricted'
            elif typ=='savings':
                account.owner=primary; account.purpose='savings'
            elif typ in ('credit','loan'):
                account.owner=primary; account.purpose='liability'
            elif typ in ('investment','asset'):
                account.owner=primary; account.purpose='investment'
            else:
                account.owner=primary; account.purpose='available'
            account.save(update_fields=['owner','purpose','include_in_net_worth'])


def reverse_configuration(apps, schema_editor):
    # Ownership metadata can be safely left in place if this migration is rolled back.
    pass


class Migration(migrations.Migration):
    atomic = False
    dependencies=[
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('finance','0001_initial'),
    ]

    operations=[
        migrations.CreateModel(
            name='AccountOwner',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('name',models.CharField(max_length=120)),
                ('kind',models.CharField(choices=[('person','Person'),('joint','Joint'),('system','System / external')],default='person',max_length=12)),
                ('is_active',models.BooleanField(default=True)),
                ('sort_order',models.PositiveIntegerField(default=100)),
                ('household',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='account_owners',to='finance.household')),
                ('linked_user',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='budget_account_owners',to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering':['sort_order','name']},
        ),
        migrations.AddConstraint(
            model_name='accountowner',
            constraint=models.UniqueConstraint(fields=('household','name'),name='unique_account_owner_name_per_household'),
        ),
        migrations.AddField(
            model_name='account',name='owner',
            field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='accounts',to='finance.accountowner'),
        ),
        migrations.AddField(
            model_name='account',name='purpose',
            field=models.CharField(choices=[('available','Available / everyday money'),('savings','Savings'),('restricted','Restricted funds'),('investment','Investment / long term'),('liability','Debt / liability'),('external','External / bookkeeping')],default='available',max_length=12),
        ),
        migrations.AddField(
            model_name='account',name='monthly_release_amount',
            field=models.DecimalField(blank=True,decimal_places=2,help_text='Optional expected monthly release for restricted accounts.',max_digits=16,null=True,validators=[MinValueValidator(Decimal('0.01'))]),
        ),
        migrations.AddField(
            model_name='account',name='release_day',
            field=models.PositiveSmallIntegerField(blank=True,help_text='Optional expected day of month for the release.',null=True,validators=[MinValueValidator(1),MaxValueValidator(31)]),
        ),
        migrations.AddField(
            model_name='account',name='release_destination',
            field=models.ForeignKey(blank=True,help_text='Where a restricted-account release is expected to land.',null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='planned_incoming_releases',to='finance.account'),
        ),
        migrations.RunPython(configure_existing_accounts,reverse_configuration),
    ]
