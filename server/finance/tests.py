from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from unittest.mock import patch
from types import SimpleNamespace
from .models import (Account, AccountOwner, AuditLog, BankConnection, BankLinkedAccount, BankSyncTransaction, BudgetImpact, BudgetPlan, Category, Household, HouseholdSettings, Membership, MobileApiToken, ReimbursementLink, RecurringRule, SavingsGoal)
from .services import account_base_balance_at, active_budget_month, classify_import_row, create_transaction, net_worth_at, pair_reimbursement, unpair_reimbursement
from .seed import seed_household
from .bank_sync import (background_sync_due, describe_sync_issue, import_bank_row, normalise_bank_transaction,
                        psu_headers_from_request, store_authorized_session)
from .categorization import refresh_bank_row_suggestion
class LedgerTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('u',password='testpass123')
        self.h=Household.objects.create(name='H'); Membership.objects.create(household=self.h,user=self.user,role='owner'); HouseholdSettings.objects.create(household=self.h)
        self.main=Account.objects.create(household=self.h,name='Main'); self.out=Account.objects.create(household=self.h,name='Outside',account_type=Account.Type.EXTERNAL,purpose=Account.Purpose.EXTERNAL,include_in_net_worth=False)
        self.food=Category.objects.create(household=self.h,name='Food',kind='expense')
    def test_expense_reduces_net_worth_and_budget(self):
        tx=create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,1),kind='expense',description='Food',from_account=self.main,to_account=self.out,amount=Decimal('20'),impacts=[{'kind':'expense','category':self.food,'amount':Decimal('20')}])
        self.assertEqual(self.main.balance,Decimal('-20.00')); self.assertEqual(net_worth_at(self.h),Decimal('-20.00')); self.assertEqual(tx.budget_impacts.first().amount,Decimal('20.00'))
    def test_late_funding_shifts_month(self):
        income=Category.objects.create(household=self.h,name='Salary',kind='funding')
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,28),kind='income',description='Salary',from_account=self.out,to_account=self.main,amount=100,impacts=[{'kind':'funding','category':income,'amount':100}])
        self.assertEqual(BudgetImpact.objects.get(kind='funding').budget_date,date(2026,9,1))
    def test_pay_cycle_moves_same_day_and_following_spending_to_next_budget_month(self):
        income=Category.objects.create(household=self.h,name='Employment (Net)',kind='funding')
        settings=self.h.settings; settings.budget_cycle_mode='pay_cycle'; settings.cycle_anchor_category=income; settings.save()
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,28),kind='income',description='Salary',from_account=self.out,to_account=self.main,amount=1000,impacts=[{'kind':'funding','category':income,'amount':1000}])
        same_day=create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,28),kind='expense',description='Groceries',from_account=self.main,to_account=self.out,amount=10,impacts=[{'kind':'expense','category':self.food,'amount':10}])
        september=create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,20),kind='expense',description='More groceries',from_account=self.main,to_account=self.out,amount=20,impacts=[{'kind':'expense','category':self.food,'amount':20}])
        self.assertEqual(same_day.budget_impacts.get().budget_date,date(2026,9,1))
        self.assertEqual(september.budget_impacts.get().budget_date,date(2026,9,1))
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,28),kind='income',description='Salary',from_account=self.out,to_account=self.main,amount=1000,impacts=[{'kind':'funding','category':income,'amount':1000}])
        october=create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,29),kind='expense',description='October groceries',from_account=self.main,to_account=self.out,amount=30,impacts=[{'kind':'expense','category':self.food,'amount':30}])
        self.assertEqual(october.budget_impacts.get().budget_date,date(2026,10,1))

    def test_pay_cycle_recalculates_rows_imported_before_early_salary(self):
        income=Category.objects.create(household=self.h,name='Employment (Net)',kind='funding')
        settings=self.h.settings; settings.budget_cycle_mode='pay_cycle'; settings.cycle_anchor_category=income; settings.shift_day=27; settings.save()
        expense=create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,26),kind='expense',description='Groceries before salary import',from_account=self.main,to_account=self.out,amount=10,impacts=[{'kind':'expense','category':self.food,'amount':10}])
        self.assertEqual(expense.budget_impacts.get().budget_date,date(2026,8,1))
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,26),kind='income',description='Early salary',from_account=self.out,to_account=self.main,amount=1000,impacts=[{'kind':'funding','category':income,'amount':1000}])
        impact=expense.budget_impacts.get(); impact.refresh_from_db()
        self.assertEqual(impact.budget_date,date(2026,9,1))

    def test_manual_budget_date_override_survives_pay_cycle_recalculation(self):
        income=Category.objects.create(household=self.h,name='Employment (Net)',kind='funding')
        settings=self.h.settings; settings.budget_cycle_mode='pay_cycle'; settings.cycle_anchor_category=income; settings.save()
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,28),kind='income',description='Salary',from_account=self.out,to_account=self.main,amount=1000,impacts=[{'kind':'funding','category':income,'amount':1000}])
        expense=create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,29),kind='expense',description='Explicit August item',from_account=self.main,to_account=self.out,amount=10,impacts=[{'kind':'expense','category':self.food,'amount':10,'budget_date':date(2026,8,1)}])
        impact=expense.budget_impacts.get()
        self.assertTrue(impact.budget_date_locked)
        self.assertEqual(impact.budget_date,date(2026,8,1))

    def test_internal_transfer_preserves_net_worth(self):
        savings=Account.objects.create(household=self.h,name='Savings',account_type='savings')
        self.main.opening_balance=Decimal('1000.00'); self.main.save(update_fields=['opening_balance'])
        before=net_worth_at(self.h)
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,10),kind='transfer',description='Move to savings',from_account=self.main,to_account=savings,amount=Decimal('350.00'))
        self.assertEqual(net_worth_at(self.h),before)
        self.assertEqual(self.main.balance,Decimal('650.00'))
        self.assertEqual(savings.balance,Decimal('350.00'))

    def test_blocked_release_can_fund_and_save_without_changing_net_worth(self):
        blocked=Account.objects.create(household=self.h,name='Blocked',account_type='restricted',opening_balance=Decimal('750.00'))
        savings=Account.objects.create(household=self.h,name='Savings',account_type='savings')
        funding=Category.objects.create(household=self.h,name='Blocked account',kind='funding')
        saving_cat=Category.objects.create(household=self.h,name='Emergency funds',kind='savings')
        before=net_worth_at(self.h)
        tx=create_transaction(
            household=self.h,user=self.user,posted_date=date(2026,10,1),kind='release',description='Blocked account release',
            from_account=blocked,to_account=savings,amount=Decimal('750.00'),
            impacts=[
                {'kind':'funding','category':funding,'amount':Decimal('750.00')},
                {'kind':'savings','category':saving_cat,'amount':Decimal('750.00')},
            ],
        )
        self.assertEqual(net_worth_at(self.h),before)
        self.assertEqual(tx.budget_impacts.count(),2)
        self.assertEqual(tx.budget_impacts.get(kind='funding').amount,Decimal('750.00'))
        self.assertEqual(tx.budget_impacts.get(kind='savings').amount,Decimal('750.00'))



    def test_budget_page_can_quick_add_category_and_month_amount(self):
        self.client.force_login(self.user)
        response=self.client.post(reverse('budget_category_add'),{
            'kind':'expense','name':'Car insurance','amount':'72.50','year':'2026','month':'8'
        })
        self.assertEqual(response.status_code,302)
        category=Category.objects.get(household=self.h,kind='expense',name='Car insurance')
        plan=BudgetPlan.objects.get(household=self.h,category=category,month=date(2026,8,1))
        self.assertEqual(plan.amount,Decimal('72.50'))

    def test_foreign_currency_net_worth_uses_base_values(self):
        usd=Account.objects.create(household=self.h,name='USD account',currency='USD',opening_balance=Decimal('1000.00'),opening_base_balance=Decimal('850.00'))
        before=net_worth_at(self.h)
        self.assertEqual(account_base_balance_at(usd),Decimal('850.00'))
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,15),kind='transfer',description='EUR to USD',from_account=self.main,to_account=usd,amount=Decimal('100.00'),destination_amount=Decimal('118.00'),base_value=Decimal('100.00'))
        self.assertEqual(account_base_balance_at(usd),Decimal('950.00'))
        self.assertEqual(net_worth_at(self.h),before)
    def test_seed_creates_neutral_public_starter_setup(self):
        other_user=get_user_model().objects.create_user('owner2',password='testpass123')
        household=Household.objects.create(name='Ownership household')
        Membership.objects.create(household=household,user=other_user,role=Membership.Role.OWNER)
        HouseholdSettings.objects.create(household=household)
        seed_household(household,load_plan=True)
        main=Account.objects.get(household=household,name='Main account')
        savings=Account.objects.get(household=household,name='Savings')
        outside=Account.objects.get(household=household,name='Outside world')
        self.assertEqual(main.owner.linked_user,other_user)
        self.assertEqual(savings.owner.linked_user,other_user)
        self.assertEqual(outside.purpose,Account.Purpose.EXTERNAL)
        self.assertFalse(outside.include_in_net_worth)
        self.assertTrue(Category.objects.filter(household=household,kind='expense',name='Groceries').exists())
        self.assertFalse(BudgetPlan.objects.filter(household=household).exists())

    def test_account_ownership_does_not_change_household_net_worth_math(self):
        mine=AccountOwner.objects.create(household=self.h,name='Mine',kind=AccountOwner.Kind.PERSON,linked_user=self.user)
        joint=AccountOwner.objects.create(household=self.h,name='Joint',kind=AccountOwner.Kind.JOINT)
        self.main.owner=mine; self.main.opening_balance=Decimal('100.00'); self.main.save(update_fields=['owner','opening_balance'])
        shared=Account.objects.create(household=self.h,name='Joint savings',owner=joint,account_type=Account.Type.SAVINGS,purpose=Account.Purpose.SAVINGS,opening_balance=Decimal('250.00'))
        self.assertEqual(net_worth_at(self.h),Decimal('350.00'))
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,20),kind='transfer',description='Move money jointly',from_account=self.main,to_account=shared,amount=Decimal('50.00'))
        self.assertEqual(net_worth_at(self.h),Decimal('350.00'))


    def test_bank_transaction_normalisation_uses_bank_direction(self):
        debit=normalise_bank_transaction({'transaction_amount':{'amount':'42.15','currency':'EUR'},'credit_debit_indicator':'DBIT','booking_date':'2026-08-28','creditor':{'name':'REWE'},'remittance_information':['CARD 1234'],'status':'BOOK'})
        credit=normalise_bank_transaction({'transaction_amount':{'amount':'2454.00','currency':'EUR'},'credit_debit_indicator':'CRDT','booking_date':'2026-08-27','debtor':{'name':'Example Employer'},'status':'BOOK'})
        self.assertEqual(debit['amount'],Decimal('-42.15'))
        self.assertIn('REWE',debit['description'])
        self.assertEqual(credit['amount'],Decimal('2454.00'))
        self.assertEqual(credit['counterparty'],'Example Employer')

    def test_authorized_bank_session_stores_only_masked_iban(self):
        payload={'session_id':'sess-1','psu_id_hash':'hash','aspsp':{'name':'Example Bank','country':'DE'},'psu_type':'personal','access':{'valid_until':'2026-12-01T12:00:00+00:00'},'accounts':[{'uid':'acc-1','identification_hash':'idhash','identification_hashes':['idhash'],'name':'Girokonto','currency':'EUR','account_id':{'iban':'DE00123456789012345678'}}]}
        connection=store_authorized_session(household=self.h,user=self.user,payload=payload)
        link=connection.linked_accounts.get()
        self.assertEqual(connection.aspsp_name,'Example Bank')
        self.assertEqual(link.masked_iban,'•••• 5678')
        self.assertNotIn('DE00123456789012345678',link.masked_iban)

    def test_bank_inbox_import_and_internal_transfer_pair(self):
        conn=BankConnection.objects.create(household=self.h,aspsp_name='Test Bank',country='DE',session_id='s',created_by=self.user)
        l1=BankLinkedAccount.objects.create(connection=conn,account=self.main,provider_account_uid='a1',identification_hash='h1',name='Main',currency='EUR')
        row=BankSyncTransaction.objects.create(bank_account=l1,provider_tx_key='k1',booking_date=date(2026,8,20),amount=Decimal('-20.00'),currency='EUR',description='REWE')
        tx=import_bank_row(row,user=self.user,category=self.food)
        row.refresh_from_db()
        self.assertEqual(row.status,BankSyncTransaction.Status.IMPORTED)
        self.assertEqual(tx.budget_impacts.get().amount,Decimal('20.00'))

        second=Account.objects.create(household=self.h,name='Second')
        l2=BankLinkedAccount.objects.create(connection=conn,account=second,provider_account_uid='a2',identification_hash='h2',name='Second',currency='EUR')
        outrow=BankSyncTransaction.objects.create(bank_account=l1,provider_tx_key='k2',booking_date=date(2026,8,21),amount=Decimal('-100.00'),currency='EUR',description='Transfer')
        inrow=BankSyncTransaction.objects.create(bank_account=l2,provider_tx_key='k3',booking_date=date(2026,8,21),amount=Decimal('100.00'),currency='EUR',description='Transfer')
        transfer=import_bank_row(outrow,user=self.user,match=inrow)
        outrow.refresh_from_db(); inrow.refresh_from_db()
        self.assertEqual(transfer.kind,'transfer')
        self.assertEqual(outrow.transaction_id,inrow.transaction_id)
        self.assertEqual(transfer.budget_impacts.count(),0)


    def test_active_budget_month_uses_pay_cycle_anchor_and_fallback(self):
        income=Category.objects.create(household=self.h,name='Employment (Net)',kind='funding')
        settings=self.h.settings; settings.budget_cycle_mode='pay_cycle'; settings.cycle_anchor_category=income; settings.shift_day=27; settings.save()
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,7,28),kind='income',description='July salary',from_account=self.out,to_account=self.main,amount=1000,impacts=[{'kind':'funding','category':income,'amount':1000}])
        self.assertEqual(active_budget_month(self.h,date(2026,8,20)),date(2026,8,1))
        # Even before the next salary is imported, the fallback day opens September.
        self.assertEqual(active_budget_month(self.h,date(2026,8,29)),date(2026,9,1))
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,26),kind='income',description='Early August salary',from_account=self.out,to_account=self.main,amount=1000,impacts=[{'kind':'funding','category':income,'amount':1000}])
        self.assertEqual(active_budget_month(self.h,date(2026,8,26)),date(2026,9,1))

    def test_dashboard_defaults_to_active_budget_month(self):
        income=Category.objects.create(household=self.h,name='Employment (Net)',kind='funding')
        settings=self.h.settings; settings.budget_cycle_mode='pay_cycle'; settings.cycle_anchor_category=income; settings.shift_day=27; settings.save()
        self.client.force_login(self.user)
        with patch('finance.services.date') as mocked_date:
            mocked_date.today.return_value=date(2026,8,29)
            response=self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['month'],date(2026,9,1))

    def test_unknown_import_uses_safe_other_fallback_not_first_category(self):
        rent=Category.objects.create(household=self.h,name='Rent',kind=Category.Kind.EXPENSE,sort_order=1)
        other=Category.objects.create(household=self.h,name='Other',kind=Category.Kind.EXPENSE,sort_order=900)
        employment=Category.objects.create(household=self.h,name='Employment',kind=Category.Kind.FUNDING,sort_order=1)
        other_income=Category.objects.create(household=self.h,name='Other income',kind=Category.Kind.FUNDING,sort_order=900)
        _,_,expense_category=classify_import_row(self.h,'UNRECOGNISED MERCHANT 82491',Decimal('-19.20'))
        _,_,income_category=classify_import_row(self.h,'UNRECOGNISED CREDIT 555',Decimal('100.00'))
        self.assertEqual(expense_category,other)
        self.assertEqual(income_category,other_income)
        self.assertNotEqual(expense_category,rent)
        self.assertNotEqual(income_category,employment)

    def test_builtin_categorisation_uses_general_european_rules(self):
        groceries=Category.objects.create(household=self.h,name='Groceries',kind=Category.Kind.EXPENSE)
        transport=Category.objects.create(household=self.h,name='Transport',kind=Category.Kind.EXPENSE)
        subscriptions=Category.objects.create(household=self.h,name='Subscriptions',kind=Category.Kind.EXPENSE)
        employment=Category.objects.create(household=self.h,name='Employment',kind=Category.Kind.FUNDING)
        self.assertEqual(classify_import_row(self.h,'REWE Markt 1234',Decimal('-42.10'))[2],groceries)
        self.assertEqual(classify_import_row(self.h,'DB Vertrieb Fahrkarte',Decimal('-19.90'))[2],transport)
        self.assertEqual(classify_import_row(self.h,'NETFLIX.COM',Decimal('-12.99'))[2],subscriptions)
        self.assertEqual(classify_import_row(self.h,'Monthly payroll ACME Europe',Decimal('2500.00'))[2],employment)

    def test_bank_review_learns_from_confirmed_counterparty_category(self):
        conn=BankConnection.objects.create(household=self.h,aspsp_name='Learning Bank',country='DE',session_id='learn',created_by=self.user)
        link=BankLinkedAccount.objects.create(connection=conn,account=self.main,provider_account_uid='learn-1',identification_hash='learn-hash',name='Main feed',currency='EUR')
        previous=BankSyncTransaction.objects.create(bank_account=link,provider_tx_key='learn-old',booking_date=date(2026,8,1),amount=Decimal('-12.00'),currency='EUR',description='CARD 88371',counterparty='Corner Shop GmbH')
        import_bank_row(previous,user=self.user,category=self.food)
        current=BankSyncTransaction.objects.create(bank_account=link,provider_tx_key='learn-new',booking_date=date(2026,9,1),amount=Decimal('-18.00'),currency='EUR',description='PURCHASE 11902',counterparty='Corner Shop GmbH')
        suggestion=refresh_bank_row_suggestion(current,persist=False)
        self.assertEqual(suggestion.category,self.food)
        self.assertEqual(suggestion.source,'learned_counterparty')
        self.assertEqual(suggestion.confidence,'high')

    def test_bank_review_refresh_replaces_old_first_category_suggestion(self):
        self.client.force_login(self.user)
        rent=Category.objects.create(household=self.h,name='Rent',kind=Category.Kind.EXPENSE,sort_order=1)
        other=Category.objects.create(household=self.h,name='Other',kind=Category.Kind.EXPENSE,sort_order=900)
        conn=BankConnection.objects.create(household=self.h,aspsp_name='Refresh Bank',country='DE',session_id='refresh',created_by=self.user)
        link=BankLinkedAccount.objects.create(connection=conn,account=self.main,provider_account_uid='refresh-1',identification_hash='refresh-hash',name='Main feed',currency='EUR')
        row=BankSyncTransaction.objects.create(bank_account=link,provider_tx_key='refresh-row',booking_date=date(2026,9,1),amount=Decimal('-7.25'),currency='EUR',description='UNKNOWN 9977',suggested_category=rent,suggested_impact_kind=Category.Kind.EXPENSE,suggested_kind='expense')
        response=self.client.get(reverse('bank_inbox'))
        self.assertEqual(response.status_code,200)
        row.refresh_from_db()
        self.assertEqual(row.suggested_category,other)
        self.assertContains(response,'Fallback')

    def test_bank_inbox_advanced_filters(self):
        self.client.force_login(self.user)
        conn=BankConnection.objects.create(household=self.h,aspsp_name='Filter Bank',country='DE',session_id='filter',created_by=self.user)
        link=BankLinkedAccount.objects.create(connection=conn,account=self.main,provider_account_uid='f1',identification_hash='fh1',name='Main feed',currency='EUR')
        wanted=BankSyncTransaction.objects.create(bank_account=link,provider_tx_key='f-k1',booking_date=date(2026,8,28),amount=Decimal('-15.00'),currency='EUR',description='Coffee shop',counterparty='Cafe')
        BankSyncTransaction.objects.create(bank_account=link,provider_tx_key='f-k2',booking_date=date(2026,8,28),amount=Decimal('-50.00'),currency='EUR',description='Groceries')
        BankSyncTransaction.objects.create(bank_account=link,provider_tx_key='f-k3',booking_date=date(2026,8,28),amount=Decimal('15.00'),currency='EUR',description='Coffee refund')
        response=self.client.get(reverse('bank_inbox'),{
            'q':'coffee shop','date_from':'2026-08-20','date_to':'2026-08-31',
            'amount_min':'10','amount_max':'20','direction':'out','bank':str(conn.pk),
        })
        self.assertEqual(response.status_code,200)
        self.assertEqual([r.pk for r in response.context['rows']],[wanted.pk])

    def test_bank_inbox_bulk_ignore_only_changes_selected_pending_rows(self):
        self.client.force_login(self.user)
        conn=BankConnection.objects.create(household=self.h,aspsp_name='Bulk Bank',country='DE',session_id='bulk',created_by=self.user)
        link=BankLinkedAccount.objects.create(connection=conn,account=self.main,provider_account_uid='b1',identification_hash='bh1',name='Main feed',currency='EUR')
        first=BankSyncTransaction.objects.create(bank_account=link,provider_tx_key='b-k1',booking_date=date(2026,8,28),amount=Decimal('-5.00'),currency='EUR',description='One')
        second=BankSyncTransaction.objects.create(bank_account=link,provider_tx_key='b-k2',booking_date=date(2026,8,28),amount=Decimal('-6.00'),currency='EUR',description='Two')
        untouched=BankSyncTransaction.objects.create(bank_account=link,provider_tx_key='b-k3',booking_date=date(2026,8,28),amount=Decimal('-7.00'),currency='EUR',description='Three')
        response=self.client.post(reverse('bank_inbox_bulk_ignore'),{'selected':[str(first.pk),str(second.pk)]})
        self.assertEqual(response.status_code,302)
        first.refresh_from_db(); second.refresh_from_db(); untouched.refresh_from_db()
        self.assertEqual(first.status,BankSyncTransaction.Status.IGNORED)
        self.assertEqual(second.status,BankSyncTransaction.Status.IGNORED)
        self.assertEqual(untouched.status,BankSyncTransaction.Status.PENDING)

    def test_mobile_info_is_public_and_identifies_budget_manager(self):
        self.client.logout()
        response=self.client.get('/mobile/info/')
        self.assertEqual(response.status_code,200)
        payload=response.json()
        self.assertEqual(payload['product'],'budget-manager')
        self.assertEqual(payload['protocol'],2)
        self.assertEqual(payload['mobile_api_version'],1)
        self.assertEqual(payload['auth'],'token')
        self.assertEqual(payload['version'],Path(__file__).resolve().parent.parent.joinpath('VERSION').read_text().strip())

    def test_help_and_health_version_are_available(self):
        self.client.force_login(self.user)
        help_response=self.client.get(reverse('help'))
        self.assertEqual(help_response.status_code,200)
        self.assertContains(help_response,'Budget month vs bank date')
        health=self.client.get(reverse('health')).json()
        self.assertEqual(health['status'],'ok')
        self.assertEqual(health['version'], Path(__file__).resolve().parent.parent.joinpath('VERSION').read_text().strip())

    def test_transaction_page_advanced_filters_amount_words_and_budget_month(self):
        self.client.force_login(self.user)
        tx1=create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,28),kind='expense',description='Market groceries',from_account=self.main,to_account=self.out,amount=Decimal('10.00'),impacts=[{'kind':'expense','category':self.food,'amount':Decimal('10.00'),'budget_date':date(2026,9,1)}])
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,8,20),kind='expense',description='Other purchase',from_account=self.main,to_account=self.out,amount=Decimal('20.00'),impacts=[{'kind':'expense','category':self.food,'amount':Decimal('20.00'),'budget_date':date(2026,8,1)}])
        response=self.client.get(reverse('transactions'),{'q':'Market groceries','amount_exact':'10','budget_month':'2026-09'})
        self.assertEqual(response.status_code,200)
        self.assertEqual([x.pk for x in response.context['transactions']],[tx1.pk])

    def test_accounts_page_filters_owner_purpose_and_balance(self):
        self.client.force_login(self.user)
        mine=AccountOwner.objects.create(household=self.h,name='Mine',kind=AccountOwner.Kind.PERSON,linked_user=self.user)
        self.main.owner=mine; self.main.purpose=Account.Purpose.AVAILABLE; self.main.opening_balance=Decimal('100.00'); self.main.save()
        Account.objects.create(household=self.h,name='Tiny savings',owner=mine,account_type=Account.Type.SAVINGS,purpose=Account.Purpose.SAVINGS,opening_balance=Decimal('5.00'))
        response=self.client.get(reverse('accounts'),{'owner':str(mine.pk),'purpose':'available','balance_min':'50'})
        self.assertEqual(response.status_code,200)
        grouped=[a for g in response.context['account_groups'] for a in g['accounts']]
        self.assertEqual([a.pk for a in grouped],[self.main.pk])

    def test_recurring_page_advanced_filters(self):
        self.client.force_login(self.user)
        rule=RecurringRule.objects.create(household=self.h,name='Salary',transaction_kind='income',description='Monthly salary',amount=Decimal('1000'),from_account=self.out,to_account=self.main,frequency='monthly',interval=1,start_date=date(2026,1,1),next_run=date(2026,9,28),auto_post=True)
        RecurringRule.objects.create(household=self.h,name='Small bill',transaction_kind='expense',description='Bill',amount=Decimal('5'),from_account=self.main,to_account=self.out,frequency='monthly',interval=1,start_date=date(2026,1,1),next_run=date(2026,9,1),auto_post=False)
        response=self.client.get(reverse('recurring'),{'q':'salary','mode':'auto','amount_min':'500','date_from':'2026-09-20'})
        self.assertEqual(response.status_code,200)
        self.assertEqual([r.pk for r in response.context['rules']],[rule.pk])

    def test_audit_page_filters_words_action_and_date(self):
        self.client.force_login(self.user)
        wanted=AuditLog.objects.create(household=self.h,user=self.user,action='update',entity='account',entity_id='1',summary='Renamed current account')
        AuditLog.objects.create(household=self.h,user=self.user,action='create',entity='category',entity_id='2',summary='Created groceries')
        response=self.client.get(reverse('audit'),{'q':'current account','action':'update','date_from':wanted.created_at.date().isoformat()})
        self.assertEqual(response.status_code,200)
        self.assertEqual([x.pk for x in response.context['logs']],[wanted.pk])

    def test_review_inbox_is_a_direct_authenticated_page(self):
        self.client.force_login(self.user)
        response=self.client.get(reverse('bank_inbox'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Bank inbox')

    def test_background_bank_sync_waits_six_hours_after_success(self):
        conn=BankConnection.objects.create(household=self.h,aspsp_name='Timing Bank',country='DE',session_id='timing',created_by=self.user,status=BankConnection.Status.AUTHORIZED)
        now=timezone.now()
        conn.last_synced_at=now-timedelta(hours=2); conn.save(update_fields=['last_synced_at'])
        self.assertFalse(background_sync_due(conn,now=now))
        conn.last_synced_at=now-timedelta(hours=7); conn.save(update_fields=['last_synced_at'])
        self.assertTrue(background_sync_due(conn,now=now))

    def test_rate_limit_issue_is_friendly_and_defers_background_retry(self):
        conn=BankConnection.objects.create(household=self.h,aspsp_name='Limited Bank',country='DE',session_id='limited',created_by=self.user,status=BankConnection.Status.AUTHORIZED,last_error='HTTP 429 ASPSP_RATE_LIMIT_EXCEEDED')
        now=timezone.now()
        BankConnection.objects.filter(pk=conn.pk).update(updated_at=now)
        conn.refresh_from_db()
        issue=describe_sync_issue(conn)
        self.assertEqual(issue['kind'],'rate_limit')
        self.assertIn('temporarily limited',issue['message'])
        self.assertFalse(background_sync_due(conn,now=now+timedelta(hours=5)))
        self.assertTrue(background_sync_due(conn,now=now+timedelta(hours=7)))

    def test_manual_sync_browser_context_builds_psu_headers(self):
        request=SimpleNamespace(META={
            'HTTP_X_FORWARDED_FOR':'100.108.70.15, 127.0.0.1',
            'REMOTE_ADDR':'127.0.0.1',
            'HTTP_USER_AGENT':'Budget Browser/1.0',
            'HTTP_ACCEPT':'text/html',
            'HTTP_ACCEPT_LANGUAGE':'en-US,en;q=0.9',
        })
        headers=psu_headers_from_request(request)
        self.assertEqual(headers['Psu-Ip-Address'],'100.108.70.15')
        self.assertEqual(headers['Psu-User-Agent'],'Budget Browser/1.0')
        self.assertEqual(headers['Psu-Accept-Language'],'en-US,en;q=0.9')

    def test_budget_month_note_is_saved_per_month(self):
        self.client.force_login(self.user)
        response=self.client.post(reverse('budget_month')+'?year=2026&month=9',{
            f'amount_{self.food.id}':'200.00',f'note_{self.food.id}':'Birthday dinner and groceries',
        })
        self.assertEqual(response.status_code,302)
        sep=BudgetPlan.objects.get(household=self.h,category=self.food,month=date(2026,9,1))
        self.assertEqual(sep.note,'Birthday dinner and groceries')
        self.assertFalse(BudgetPlan.objects.filter(household=self.h,category=self.food,month=date(2026,10,1)).exists())

    def test_report_preset_ignores_stale_manual_dates_and_custom_uses_them(self):
        self.client.force_login(self.user)
        with patch('finance.services.date') as mocked_date:
            mocked_date.today.return_value=date(2026,9,4)
            mocked_date.side_effect=lambda *a,**k: date(*a,**k)
            response=self.client.get(reverse('reports'),{'preset':'3m','start':'2025-01','end':'2025-02'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['start'],date(2026,7,1))
        self.assertEqual(response.context['end'],date(2026,9,1))
        response=self.client.get(reverse('reports'),{'preset':'custom','start':'2026-02','end':'2026-04'})
        self.assertEqual(response.context['start'],date(2026,2,1))
        self.assertEqual(response.context['end'],date(2026,4,1))

    def test_account_can_have_multiple_household_owners(self):
        self.client.force_login(self.user)
        mine=AccountOwner.objects.create(household=self.h,name='Alex',kind=AccountOwner.Kind.PERSON,linked_user=self.user,sort_order=10)
        coowner=AccountOwner.objects.create(household=self.h,name='Sam',kind=AccountOwner.Kind.PERSON,sort_order=20)
        joint=AccountOwner.objects.create(household=self.h,name='Joint',kind=AccountOwner.Kind.JOINT,sort_order=30)
        response=self.client.post(reverse('account_add'),{
            'name':'Joint current','owners':[str(mine.pk),str(coowner.pk)],'account_type':'checking','purpose':'available','institution':'','currency':'EUR',
            'include_in_net_worth':'on','is_active':'on','opening_balance':'0','opening_base_balance':'0','opening_date':'','monthly_release_amount':'','release_day':'','release_destination':'','notes':'',
        })
        self.assertEqual(response.status_code,302)
        account=Account.objects.get(household=self.h,name='Joint current')
        self.assertEqual(set(account.owners.values_list('pk',flat=True)),{mine.pk,coowner.pk})
        self.assertEqual(account.owner,joint)
        response=self.client.get(reverse('accounts'),{'owner':coowner.pk})
        grouped=[a for g in response.context['account_groups'] for a in g['accounts']]
        self.assertIn(account,grouped)

    def test_reimbursement_pair_reduces_purchase_expense_and_is_reversible(self):
        purchase=create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,1),kind='expense',description='Shared purchase',from_account=self.main,to_account=self.out,amount=Decimal('100.00'),impacts=[{'kind':'expense','category':self.food,'amount':Decimal('100.00')}])
        repayment=create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,2),kind='income',description='Friend repayment',from_account=self.out,to_account=self.main,amount=Decimal('40.00'),impacts=[])
        link=pair_reimbursement(purchase=purchase,reimbursement=repayment,user=self.user)
        purchase.refresh_from_db(); repayment.refresh_from_db()
        self.assertEqual(link.amount,Decimal('40.00'))
        self.assertEqual(purchase.budget_impacts.get(kind='expense').amount,Decimal('60.00'))
        self.assertEqual(repayment.kind,'other')
        self.assertEqual(repayment.budget_impacts.count(),0)
        unpair_reimbursement(link,user=self.user)
        purchase.refresh_from_db(); repayment.refresh_from_db()
        self.assertEqual(purchase.budget_impacts.get(kind='expense').amount,Decimal('100.00'))
        self.assertEqual(repayment.kind,'income')
        self.assertFalse(ReimbursementLink.objects.filter(purchase=purchase).exists())

    def test_pairing_income_with_existing_funding_impact_removes_and_restores_it(self):
        funding=Category.objects.create(household=self.h,name='Other income',kind='funding')
        purchase=create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,1),kind='expense',description='Shared purchase',from_account=self.main,to_account=self.out,amount=Decimal('80.00'),impacts=[{'kind':'expense','category':self.food,'amount':Decimal('80.00')}])
        repayment=create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,2),kind='income',description='Repayment misclassified',from_account=self.out,to_account=self.main,amount=Decimal('30.00'),impacts=[{'kind':'funding','category':funding,'amount':Decimal('30.00')}])
        link=pair_reimbursement(purchase=purchase,reimbursement=repayment,user=self.user)
        self.assertEqual(repayment.budget_impacts.count(),0)
        unpair_reimbursement(link,user=self.user)
        restored=repayment.budget_impacts.get()
        self.assertEqual(restored.kind,'funding')
        self.assertEqual(restored.amount,Decimal('30.00'))

    def test_internal_transfer_cannot_be_used_as_reimbursement(self):
        paypal=Account.objects.create(household=self.h,name='PayPal',purpose=Account.Purpose.AVAILABLE)
        purchase=create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,1),kind='expense',description='Shared purchase',from_account=self.main,to_account=self.out,amount=Decimal('100.00'),impacts=[{'kind':'expense','category':self.food,'amount':Decimal('100.00')}])
        internal=create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,2),kind='transfer',description='PayPal to bank',from_account=paypal,to_account=self.main,amount=Decimal('40.00'),impacts=[])
        with self.assertRaisesMessage(ValueError,'incoming payment from your friend'):
            pair_reimbursement(purchase=purchase,reimbursement=internal,user=self.user)

    def test_reimbursement_pair_page_is_available_for_incoming_transaction(self):
        self.client.force_login(self.user)
        create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,1),kind='expense',description='Shared purchase',from_account=self.main,to_account=self.out,amount=Decimal('100.00'),impacts=[{'kind':'expense','category':self.food,'amount':Decimal('100.00')}])
        repayment=create_transaction(household=self.h,user=self.user,posted_date=date(2026,9,2),kind='income',description='Friend repayment',from_account=self.out,to_account=self.main,amount=Decimal('40.00'),impacts=[])
        response=self.client.get(reverse('reimbursement_pair',args=[repayment.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Pair reimbursement')
        self.assertContains(response,'Shared purchase')

    def test_help_pages_explain_recurring_and_loans(self):
        self.client.force_login(self.user)
        recurring=self.client.get(reverse('recurring'))
        loans=self.client.get(reverse('loans'))
        help_page=self.client.get(reverse('help'))
        self.assertContains(recurring,'Recurring is optional when bank sync is your source of truth')
        self.assertContains(loans,'The Loan page is a profile around a real loan account')
        self.assertContains(help_page,'Reimbursements without bookkeeping gymnastics')
        self.assertContains(help_page,'Auto-post creates ledger entries automatically')

    def test_calculator_is_non_modal_floating_panel(self):
        self.client.force_login(self.user)
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'data-calculator-panel')
        self.assertNotContains(response,'data-calculator-dialog')

    def _mobile_login(self):
        response=self.client.post(reverse('mobile_api_login'),data='{"username":"u","password":"testpass123","device_name":"Test phone"}',content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        return response.json()['token']

    def _mobile_headers(self,token):
        return {'HTTP_AUTHORIZATION':f'Bearer {token}'}

    def test_mobile_api_login_uses_hashed_revocable_token(self):
        token=self._mobile_login()
        self.assertTrue(token)
        row=MobileApiToken.objects.get(user=self.user)
        self.assertNotEqual(row.token_hash,token)
        response=self.client.get(reverse('mobile_api_bootstrap'),**self._mobile_headers(token))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['household']['name'],'H')
        self.assertEqual(response.json()['api_version'],1)
        self.client.post(reverse('mobile_api_logout'),**self._mobile_headers(token))
        self.assertEqual(self.client.get(reverse('mobile_api_bootstrap'),**self._mobile_headers(token)).status_code,401)

    def test_mobile_api_rejects_missing_token(self):
        response=self.client.get(reverse('mobile_api_dashboard'))
        self.assertEqual(response.status_code,401)
        self.assertEqual(response.json()['error'],'unauthorized')

    def test_mobile_budget_line_updates_month_specific_note(self):
        token=self._mobile_login()
        response=self.client.post(reverse('mobile_api_budget_line'),data='{"month":"2026-09","category_id":%d,"amount":"123.45","note":"Phone plan and subscriptions"}' % self.food.id,content_type='application/json',**self._mobile_headers(token))
        self.assertEqual(response.status_code,200,response.content)
        plan=BudgetPlan.objects.get(household=self.h,month=date(2026,9,1),category=self.food)
        self.assertEqual(plan.amount,Decimal('123.45'))
        self.assertEqual(plan.note,'Phone plan and subscriptions')

    def test_mobile_dashboard_and_accounts_are_native_json(self):
        token=self._mobile_login()
        self.main.opening_balance=Decimal('500.00'); self.main.save(update_fields=['opening_balance'])
        response=self.client.get(reverse('mobile_api_dashboard')+'?month=2026-09',**self._mobile_headers(token))
        self.assertEqual(response.status_code,200,response.content)
        payload=response.json()
        self.assertEqual(payload['month'],'2026-09')
        self.assertEqual(payload['net_worth'],'500.00')
        self.assertEqual(payload['accounts'][0]['name'],'Main')

    def test_mobile_review_can_import_bank_row(self):
        token=self._mobile_login()
        conn=BankConnection.objects.create(household=self.h,created_by=self.user,provider='enable_banking',aspsp_name='Test Bank',country='DE',status=BankConnection.Status.AUTHORIZED)
        linked=BankLinkedAccount.objects.create(connection=conn,provider_account_uid='uid-1',identification_hash='hash-1',name='Current',currency='EUR',account=self.main)
        row=BankSyncTransaction.objects.create(bank_account=linked,provider_tx_key='tx-1',booking_date=date(2026,9,2),amount=Decimal('-12.34'),currency='EUR',description='Coffee shop')
        listing=self.client.get(reverse('mobile_api_review'),**self._mobile_headers(token))
        self.assertEqual(listing.status_code,200)
        self.assertEqual(listing.json()['rows'][0]['description'],'Coffee shop')
        self.assertEqual(listing.json()['rows'][0]['allowed_category_kind'],'expense')
        self.assertIn('suggestion_source',listing.json()['rows'][0])
        response=self.client.post(reverse('mobile_api_review_import',args=[row.pk]),data='{"category_id":%d}' % self.food.id,content_type='application/json',**self._mobile_headers(token))
        self.assertEqual(response.status_code,200,response.content)
        row.refresh_from_db()
        self.assertEqual(row.status,BankSyncTransaction.Status.IMPORTED)
        self.assertEqual(row.transaction.budget_impacts.get().category,self.food)

    def test_mobile_reports_period_preset_changes_range(self):
        token=self._mobile_login()
        with patch('finance.mobile_api.active_budget_month',return_value=date(2026,9,1)):
            response=self.client.get(reverse('mobile_api_reports')+'?period=3m',**self._mobile_headers(token))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['from_month'],'2026-07')
        self.assertEqual(response.json()['to_month'],'2026-09')

