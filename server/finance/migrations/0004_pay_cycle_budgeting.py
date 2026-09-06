from django.db import migrations, models
import django.db.models.deletion


def configure_existing_households(apps, schema_editor):
    Settings = apps.get_model('finance','HouseholdSettings')
    Category = apps.get_model('finance','Category')
    for settings in Settings.objects.all():
        anchor=(Category.objects.filter(household_id=settings.household_id,kind='funding',name__iexact='Employment (Net)').first()
                or Category.objects.filter(household_id=settings.household_id,kind='funding',name__icontains='employment').first()
                or Category.objects.filter(household_id=settings.household_id,kind='funding',name__icontains='salary').first())
        if anchor:
            settings.budget_cycle_mode='pay_cycle'
            settings.cycle_anchor_category_id=anchor.id
            settings.save(update_fields=['budget_cycle_mode','cycle_anchor_category'])


class Migration(migrations.Migration):
    atomic = False
    dependencies=[('finance','0003_bank_sync')]
    operations=[
        migrations.AddField(
            model_name='householdsettings',
            name='budget_cycle_mode',
            field=models.CharField(choices=[('calendar','Calendar month'),('pay_cycle','Salary / pay-cycle month')],default='calendar',max_length=12),
        ),
        migrations.AddField(
            model_name='householdsettings',
            name='cycle_anchor_category',
            field=models.ForeignKey(blank=True,help_text='Funding category whose transaction date opens the following budget month.',limit_choices_to={'kind':'funding'},null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to='finance.category'),
        ),
        migrations.AddField(
            model_name='budgetimpact',
            name='budget_date_locked',
            field=models.BooleanField(default=False,help_text='If enabled, this impact keeps its manually selected budget month instead of following household cycle rules.'),
        ),
        migrations.RunPython(configure_existing_households,migrations.RunPython.noop),
    ]
