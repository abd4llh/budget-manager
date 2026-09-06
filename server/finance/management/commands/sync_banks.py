from django.core.management.base import BaseCommand
from finance.bank_sync import sync_all_connections

class Command(BaseCommand):
    help='Fetch balances and booked transactions from all authorized read-only bank connections.'
    def handle(self,*args,**options):
        result=sync_all_connections()
        self.stdout.write(self.style.SUCCESS(f"Bank sync complete: {result['connections']} attempted, {result['staged']} new inbox row(s), {result['errors']} error(s), {result.get('skipped',0)} deferred until the next background window."))
