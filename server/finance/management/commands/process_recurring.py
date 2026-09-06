from datetime import date
import os
from django.core.management.base import BaseCommand
from finance.models import RecurringRule
from finance.services import process_recurring_rule
from finance.bank_sync import sync_all_connections
class Command(BaseCommand):
    help='Process due recurring rules. Only rules with auto_post enabled create transactions.'
    def handle(self,*args,**opts):
        n=0
        for rule in RecurringRule.objects.filter(is_active=True,auto_post=True,next_run__lte=date.today()).select_related('household','from_account','to_account'):
            n+=len(process_recurring_rule(rule,today=date.today()))
        self.stdout.write(self.style.SUCCESS(f'Generated {n} transaction(s).'))
        result={'connections':0,'staged':0,'errors':0,'skipped':0} if os.getenv('BANK_SYNC_DISABLE','').lower() in {'1','true','yes'} else sync_all_connections()
        if result['connections'] or result.get('skipped'):
            self.stdout.write(self.style.SUCCESS(f"Bank sync: {result['connections']} attempted, {result['staged']} new row(s), {result['errors']} error(s), {result.get('skipped',0)} deferred until the next background window."))
