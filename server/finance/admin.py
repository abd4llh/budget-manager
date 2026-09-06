from django.contrib import admin
from .models import *
for model in [Household,Membership,HouseholdSettings,AccountOwner,Account,Category,Tag,Transaction,TransactionEntry,BudgetImpact,ReimbursementLink,Attachment,BudgetPlan,SavingsGoal,LoanProfile,RecurringRule,ImportRule,ImportProfile,ImportBatch,ImportRow,Reconciliation,AuditLog,BankConnection,BankLinkedAccount,BankAuthorizationAttempt,BankSyncTransaction]:
    admin.site.register(model)
