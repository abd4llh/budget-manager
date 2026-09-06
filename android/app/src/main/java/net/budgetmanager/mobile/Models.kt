package net.budgetmanager.mobile

data class ServerInfo(val name:String,val address:String,val version:String="")
data class SessionInfo(val token:String,val userName:String,val householdName:String,val role:String)
data class Category(val id:Int,val name:String,val kind:String)
data class AccountItem(val id:Int,val name:String,val institution:String,val currency:String,val purpose:String,val purposeLabel:String,val balance:String,val owners:List<String>)
data class TransactionItem(val id:Int,val date:String,val kind:String,val description:String,val payee:String,val amount:String,val fromAccount:String?,val toAccount:String?,val category:String?,val reimbursementRole:String?=null,val pairedId:Int?=null)
data class TransferCandidate(val id:Int,val date:String,val amount:String,val description:String,val bank:String,val mappedAccount:String?)
data class ReviewItem(val id:Int,val date:String,val amount:String,val direction:String,val currency:String,val description:String,val counterparty:String,val bank:String,val feed:String,val mappedAccount:String?,val suggestedCategoryId:Int?,val suggestedCategory:String?,val suggestionSource:String,val suggestionConfidence:String,val suggestionReason:String,val allowedCategoryKind:String,val transferCandidates:List<TransferCandidate>)
data class BudgetLine(val categoryId:Int,val category:String,val kind:String,val planned:String,val actual:String,val note:String)
data class DashboardData(val month:String,val netWorth:String,val reviewCount:Int,val actualFunding:String,val actualExpenses:String,val actualSavings:String,val actualRemaining:String,val plannedFunding:String,val plannedExpenses:String,val plannedSavings:String,val plannedRemaining:String,val accounts:List<AccountItem>,val recent:List<TransactionItem>)
data class ReportMonth(val month:String,val funding:String,val expenses:String,val savings:String,val surplus:String,val netWorth:String)
data class ReportCategory(val name:String,val amount:String)
data class ReportData(val period:String,val fromMonth:String,val toMonth:String,val funding:String,val expenses:String,val savings:String,val surplus:String,val months:List<ReportMonth>,val categories:List<ReportCategory>)
data class BankItem(val id:Int,val name:String,val status:String,val consentUntil:String?,val lastSync:String?,val nextSync:String?,val issueTitle:String?,val issueMessage:String?)
data class ReimbursementCandidate(val id:Int,val date:String,val description:String,val amount:String,val expenseImpact:String)

sealed class AppDestination(val label:String) {
    data object Dashboard:AppDestination("Dashboard")
    data object Review:AppDestination("Review")
    data object Budget:AppDestination("Budget")
    data object Accounts:AppDestination("Accounts")
    data object More:AppDestination("More")
}
