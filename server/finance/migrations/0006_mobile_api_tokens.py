from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[('finance','0005_household_owners_reimbursements')]
    operations=[
        migrations.CreateModel(
            name='MobileApiToken',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('name',models.CharField(default='Android',max_length=120)),
                ('token_hash',models.CharField(db_index=True,max_length=64,unique=True)),
                ('created_at',models.DateTimeField(auto_now_add=True)),
                ('last_used_at',models.DateTimeField(blank=True,null=True)),
                ('expires_at',models.DateTimeField(blank=True,null=True)),
                ('is_active',models.BooleanField(default=True)),
                ('household',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='mobile_api_tokens',to='finance.household')),
                ('user',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='budget_mobile_tokens',to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering':['-last_used_at','-created_at']},
        ),
    ]
