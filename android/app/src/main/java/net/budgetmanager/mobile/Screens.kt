@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package net.budgetmanager.mobile

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBalance
import androidx.compose.material.icons.filled.AccountBalanceWallet
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Calculate
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ChevronLeft
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Logout
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.ReceiptLong
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Rule
import androidx.compose.material.icons.filled.Savings
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs

@Composable
fun ServerSelectionScreen(current: String, onConnected: (String) -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var address by remember { mutableStateOf(current) }
    var found by remember { mutableStateOf<List<ServerInfo>>(emptyList()) }
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }

    fun connect(raw: String) {
        val url = Discovery.normalizeAddress(raw)
        if (url.isBlank()) return
        busy = true
        status = "Checking $url…"
        scope.launch {
            ioResult { ApiClient(url).mobileInfo() }
                .onSuccess {
                    if (it.optString("product") == "budget-manager" && it.optInt("mobile_api_version", 0) >= 1) {
                        status = "Connected to Budget Manager ${it.optString("version")}"
                        onConnected(url)
                    } else {
                        status = "That server does not have the native mobile API. Install Budget Manager server 0.9.0-beta or later."
                    }
                }
                .onFailure { status = it.message ?: "Could not reach server" }
            busy = false
        }
    }

    Scaffold { pad ->
        Column(
            Modifier.fillMaxSize().padding(pad).padding(24.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Surface(
                shape = MaterialTheme.shapes.large,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(58.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text(
                        "B",
                        style = MaterialTheme.typography.headlineMedium,
                        color = MaterialTheme.colorScheme.onPrimary,
                        fontWeight = FontWeight.Bold,
                    )
                }
            }
            Text("Connect to Budget Manager", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
            Text(
                "Find your Budget Manager server on Wi‑Fi or enter its IP address, hostname, VPN name, or HTTPS domain.",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(
                enabled = !busy,
                onClick = {
                    busy = true
                    status = "Searching this network…"
                    scope.launch {
                        found = Discovery.scan(context)
                        status = if (found.isEmpty()) {
                            "No server found. You can still enter an address manually."
                        } else {
                            "${found.size} server(s) found"
                        }
                        busy = false
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Default.Search, null)
                Spacer(Modifier.width(8.dp))
                Text("Search this network")
            }
            if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (status.isNotBlank()) Text(status, color = MaterialTheme.colorScheme.onSurfaceVariant)
            found.forEach { srv ->
                Card(Modifier.fillMaxWidth().clickable { connect(srv.address) }) {
                    Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Dns, null)
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(srv.name, fontWeight = FontWeight.Bold)
                            Text(srv.address)
                            if (srv.version.isNotBlank()) Text("Server ${srv.version}", style = MaterialTheme.typography.bodySmall)
                        }
                        Icon(Icons.Default.ChevronRight, null)
                    }
                }
            }
            HorizontalDivider()
            Text("Manual address", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            OutlinedTextField(
                address,
                { address = it },
                label = { Text("Server address") },
                placeholder = { Text("192.168.1.50:8015") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Text(
                "For access away from home, use your HTTPS/Tailscale address. Plain HTTP should only be used on a trusted LAN.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(enabled = !busy, onClick = { connect(address) }, modifier = Modifier.fillMaxWidth()) { Text("Connect") }
        }
    }
}

@Composable
fun LoginScreen(server: String, lastUsername: String, onBack: () -> Unit, onLogin: (String, String) -> Unit) {
    var user by remember { mutableStateOf(lastUsername) }
    var pass by remember { mutableStateOf("") }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Sign in") },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, null) } },
            )
        },
    ) { pad ->
        Column(
            Modifier.fillMaxSize().padding(pad).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text("Budget Manager", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
            Text(server, color = MaterialTheme.colorScheme.onSurfaceVariant)
            OutlinedTextField(user, { user = it }, label = { Text("Username") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                pass,
                { pass = it },
                label = { Text("Password") },
                visualTransformation = PasswordVisualTransformation(),
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                onClick = { onLogin(user, pass) },
                enabled = user.isNotBlank() && pass.isNotBlank(),
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Sign in") }
            Text(
                "The app stores a revocable mobile token in Android's encrypted keystore. Your password is not saved.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
fun MainShell(
    api: ApiClient,
    server: String,
    activeMonth: String,
    categories: List<Category>,
    onMonthChanged: (String) -> Unit,
    onChangeServer: () -> Unit,
    onLogout: () -> Unit,
    onOpenWeb: (String) -> Unit,
) {
    var dest by remember { mutableStateOf<AppDestination>(AppDestination.Dashboard) }
    var sub by remember { mutableStateOf<String?>(null) }
    var calculator by remember { mutableStateOf(false) }
    val tabs = listOf(AppDestination.Dashboard, AppDestination.Review, AppDestination.Budget, AppDestination.Accounts, AppDestination.More)

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(sub ?: dest.label) },
                actions = {
                    IconButton(onClick = { calculator = !calculator }) {
                        Icon(Icons.Default.Calculate, "Calculator")
                    }
                },
            )
        },
        bottomBar = {
            if (sub == null) {
                NavigationBar {
                    tabs.forEach { tab ->
                        NavigationBarItem(
                            selected = dest == tab,
                            onClick = { dest = tab },
                            icon = {
                                Icon(
                                    when (tab) {
                                        AppDestination.Dashboard -> Icons.Default.Home
                                        AppDestination.Review -> Icons.Default.Rule
                                        AppDestination.Budget -> Icons.Default.AccountBalanceWallet
                                        AppDestination.Accounts -> Icons.Default.AccountBalance
                                        else -> Icons.Default.MoreHoriz
                                    },
                                    null,
                                )
                            },
                            label = { Text(tab.label) },
                        )
                    }
                }
            }
        },
    ) { pad ->
        Box(Modifier.fillMaxSize().padding(pad)) {
            if (sub != null) {
                when (sub) {
                    "Transactions" -> TransactionsScreen(api) { sub = null }
                    "Reports" -> ReportsScreen(api) { sub = null }
                    "Bank sync" -> BanksScreen(api, onOpenWeb) { sub = null }
                }
            } else {
                when (dest) {
                    AppDestination.Dashboard -> DashboardScreen(
                        api = api,
                        month = activeMonth,
                        onReview = { dest = AppDestination.Review },
                        onAccounts = { dest = AppDestination.Accounts },
                    )
                    AppDestination.Review -> ReviewScreen(api, categories)
                    AppDestination.Budget -> BudgetScreen(api, activeMonth, onMonthChanged)
                    AppDestination.Accounts -> AccountsScreen(api)
                    AppDestination.More -> MoreScreen(
                        server,
                        onTransactions = { sub = "Transactions" },
                        onReports = { sub = "Reports" },
                        onBanks = { sub = "Bank sync" },
                        onWeb = { onOpenWeb("/") },
                        onChangeServer = onChangeServer,
                        onLogout = onLogout,
                    )
                }
            }
            if (calculator) CalculatorOverlay(Modifier.align(Alignment.BottomEnd), onClose = { calculator = false })
        }
    }
}

@Composable
private fun DashboardScreen(api: ApiClient, month: String, onReview: () -> Unit, onAccounts: () -> Unit) {
    var data by remember { mutableStateOf<DashboardData?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    var refresh by remember { mutableIntStateOf(0) }

    LaunchedEffect(month, refresh) {
        ioResult { api.dashboard(month.takeIf { it.isNotBlank() }) }
            .onSuccess {
                data = it
                err = null
            }
            .onFailure { err = it.message ?: "Could not load dashboard" }
    }

    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 16.dp),
        contentPadding = PaddingValues(top = 12.dp, bottom = 20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Active budget", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(prettyMonth(data?.month ?: month), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                }
                IconButton(onClick = { refresh++ }) { Icon(Icons.Default.Refresh, "Refresh dashboard") }
            }
        }
        if (err != null) item { ErrorCard(err!!) }
        data?.let { d ->
            item { NetWorthCard(d.netWorth) }
            item { BudgetProgressCard(d) }
            item { ReviewSummaryCard(d.reviewCount, onReview) }
            item { SectionHeader("Accounts", "See all", onAccounts) }
            item { AccountsSummaryCard(d.accounts.take(4)) }
            if (d.recent.isNotEmpty()) {
                item { SectionTitle("Recent activity") }
                item { RecentActivityCard(d.recent.take(5)) }
            }
        } ?: item { LoadingBlock() }
    }
}

@Composable
private fun NetWorthCard(netWorth: String) {
    Card(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 18.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Surface(
                shape = MaterialTheme.shapes.medium,
                color = MaterialTheme.colorScheme.primaryContainer,
                modifier = Modifier.size(44.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(Icons.Default.Savings, null, tint = MaterialTheme.colorScheme.onPrimaryContainer)
                }
            }
            Spacer(Modifier.width(14.dp))
            Column {
                Text("Net worth", color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("€$netWorth", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
private fun BudgetProgressCard(d: DashboardData) {
    val actual = d.actualExpenses.toMoneyDouble()
    val planned = d.plannedExpenses.toMoneyDouble()
    val ratio = if (planned > 0.0) (actual / planned).toFloat() else 0f
    val percent = if (planned > 0.0) (actual / planned * 100.0).toInt() else 0

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Monthly spending", fontWeight = FontWeight.Bold)
                    Text(
                        "€${d.actualExpenses} of €${d.plannedExpenses} planned",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Text("$percent%", fontWeight = FontWeight.Bold)
            }
            LinearProgressIndicator(progress = { ratio.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MiniMetric("Funding", "€${d.actualFunding}", Modifier.weight(1f))
                MiniMetric("Expenses", "€${d.actualExpenses}", Modifier.weight(1f))
                MiniMetric("Remaining", "€${d.actualRemaining}", Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun ReviewSummaryCard(count: Int, onReview: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onReview)) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(if (count == 0) Icons.Default.CheckCircle else Icons.Default.Rule, null)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text("Review inbox", fontWeight = FontWeight.Bold)
                Text(
                    when (count) {
                        0 -> "All caught up"
                        1 -> "1 transaction to review"
                        else -> "$count transactions to review"
                    },
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Icon(Icons.Default.ChevronRight, null)
        }
    }
}

@Composable
private fun AccountsSummaryCard(accounts: List<AccountItem>) {
    Card(Modifier.fillMaxWidth()) {
        if (accounts.isEmpty()) {
            Text("No accounts to show", Modifier.padding(16.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            Column {
                accounts.forEachIndexed { index, account ->
                    CompactAccountRow(account)
                    if (index < accounts.lastIndex) HorizontalDivider(Modifier.padding(start = 16.dp))
                }
            }
        }
    }
}

@Composable
private fun CompactAccountRow(account: AccountItem) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(account.name, fontWeight = FontWeight.SemiBold)
            val details = listOf(account.institution, account.owners.joinToString(" + ")).filter { it.isNotBlank() }.joinToString(" · ")
            if (details.isNotBlank()) Text(details, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text("${account.balance} ${account.currency}", fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun RecentActivityCard(rows: List<TransactionItem>) {
    Card(Modifier.fillMaxWidth()) {
        Column {
            rows.forEachIndexed { index, row ->
                CompactTransactionRow(row)
                if (index < rows.lastIndex) HorizontalDivider(Modifier.padding(start = 16.dp))
            }
        }
    }
}

@Composable
private fun CompactTransactionRow(t: TransactionItem) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(t.description, fontWeight = FontWeight.SemiBold)
            Text(
                listOf(t.date, t.category ?: t.kind).joinToString(" · "),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text("€${t.amount}", fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun ReviewScreen(api: ApiClient, categories: List<Category>) {
    val scope = rememberCoroutineScope()
    var q by remember { mutableStateOf("") }
    var dir by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf<List<ReviewItem>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf<String?>(null) }
    var importRow by remember { mutableStateOf<ReviewItem?>(null) }
    var importedTx by remember { mutableStateOf<TransactionItem?>(null) }
    val selectedCategories = remember { mutableStateMapOf<Int, Int?>() }
    val categoryOverrides = remember { mutableStateMapOf<Int, Boolean>() }

    fun load() {
        scope.launch {
            busy = true
            ioResult { api.review(q, dir) }
                .onSuccess { loaded ->
                    rows = loaded
                    loaded.forEach { row ->
                        if (categoryOverrides[row.id] != true) {
                            selectedCategories[row.id] = row.suggestedCategoryId
                        }
                    }
                    err = null
                }
                .onFailure { err = it.message }
            busy = false
        }
    }

    LaunchedEffect(Unit) { load() }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(q, { q = it }, label = { Text("Search") }, singleLine = true, modifier = Modifier.weight(1f))
            IconButton(onClick = { load() }) { Icon(Icons.Default.Search, null) }
        }
        Row(Modifier.padding(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(dir == "", { dir = ""; load() }, { Text("All") })
            FilterChip(dir == "out", { dir = "out"; load() }, { Text("Money out") })
            FilterChip(dir == "in", { dir = "in"; load() }, { Text("Money in") })
        }
        if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        if (err != null) ErrorCard(err!!)
        LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (rows.isEmpty() && !busy) item { EmptyState("Nothing needs review") }
            items(rows, key = { it.id }) { row ->
                val allowed = categories.filter { it.kind == row.allowedCategoryKind }
                val selectedId = selectedCategories[row.id]
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                        Row {
                            Column(Modifier.weight(1f)) {
                                Text(row.description, fontWeight = FontWeight.Bold)
                                Text(
                                    listOf(row.counterparty, row.bank, row.date).filter { it.isNotBlank() }.joinToString(" · "),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            MoneyText(row.amount, row.direction)
                        }

                        SuggestionLine(row)

                        Text("Category", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        ReviewCategoryPicker(
                            categories = allowed,
                            selectedId = selectedId,
                            onSelected = { selectedCategories[row.id] = it; categoryOverrides[row.id] = true },
                        )

                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(
                                onClick = {
                                    scope.launch {
                                        ioResult { api.ignoreReview(row.id) }
                                            .onSuccess {
                                                rows = rows.filterNot { it.id == row.id }
                                                selectedCategories.remove(row.id)
                                                categoryOverrides.remove(row.id)
                                            }
                                            .onFailure { err = it.message }
                                    }
                                },
                            ) { Text("Ignore") }
                            Button(onClick = { importRow = row }) { Text("Import") }
                        }
                    }
                }
            }
        }
    }

    importRow?.let { row ->
        CategoryDialog(
            row = row,
            categories = categories,
            initialCategoryId = selectedCategories[row.id],
            onDismiss = { importRow = null },
            onCategoryChanged = { selectedCategories[row.id] = it; categoryOverrides[row.id] = true },
            onImport = { categoryId, matchId ->
                scope.launch {
                    ioResult { api.importReview(row.id, categoryId, matchId) }
                        .onSuccess { tx ->
                            rows = rows.filterNot { it.id == row.id }
                            selectedCategories.remove(row.id)
                            categoryOverrides.remove(row.id)
                            importRow = null
                            if (row.direction == "in" && tx != null && matchId == null) importedTx = tx
                        }
                        .onFailure { err = it.message }
                }
            },
        )
    }
    importedTx?.let { tx -> ReimbursementPrompt(tx, api, onDone = { importedTx = null }, onSkip = { importedTx = null }) }
}

@Composable
private fun SuggestionLine(row: ReviewItem) {
    val label = when (row.suggestionSource) {
        "user_rule" -> "Your rule"
        "learned_account", "learned_counterparty", "learned_merchant" -> "Learned"
        "builtin_rule" -> "Smart rule"
        "fallback" -> "Fallback"
        else -> "No confident suggestion"
    }
    val text = if (row.suggestedCategory.isNullOrBlank()) label else "$label: ${row.suggestedCategory}"
    AssistChip(
        onClick = {},
        label = { Text(text) },
    )
    if (row.suggestionReason.isNotBlank()) {
        Text(
            row.suggestionReason,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun ReviewCategoryPicker(categories: List<Category>, selectedId: Int?, onSelected: (Int?) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { expanded = true }, modifier = Modifier.fillMaxWidth()) {
            Text(categories.firstOrNull { it.id == selectedId }?.name ?: "No budget category")
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            DropdownMenuItem(
                text = { Text("No budget category") },
                onClick = { onSelected(null); expanded = false },
            )
            categories.forEach { category ->
                DropdownMenuItem(
                    text = { Text(category.name) },
                    onClick = { onSelected(category.id); expanded = false },
                )
            }
        }
    }
}

@Composable
private fun CategoryDialog(
    row: ReviewItem,
    categories: List<Category>,
    initialCategoryId: Int?,
    onDismiss: () -> Unit,
    onCategoryChanged: (Int?) -> Unit,
    onImport: (Int?, Int?) -> Unit,
) {
    var selected by remember(row.id, initialCategoryId) { mutableStateOf(initialCategoryId) }
    var matchId by remember(row.id) { mutableStateOf<Int?>(null) }
    val allowed = categories.filter { it.kind == row.allowedCategoryKind }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Import transaction") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("${row.description}\n€${row.amount}")
                if (row.transferCandidates.isNotEmpty()) {
                    Text("Possible internal transfer", fontWeight = FontWeight.Bold)
                    row.transferCandidates.forEach { candidate ->
                        FilterChip(
                            selected = matchId == candidate.id,
                            onClick = {
                                matchId = if (matchId == candidate.id) null else candidate.id
                                if (matchId != null) {
                                    selected = null
                                    onCategoryChanged(null)
                                }
                            },
                            label = { Text("${candidate.mappedAccount ?: candidate.bank} · €${candidate.amount.removePrefix("-")}") },
                        )
                    }
                    Text(
                        "Choose a match only when this is the other side of the same transfer.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                if (matchId == null) {
                    Text("Category", style = MaterialTheme.typography.labelMedium)
                    ReviewCategoryPicker(
                        categories = allowed,
                        selectedId = selected,
                        onSelected = {
                            selected = it
                            onCategoryChanged(it)
                        },
                    )
                }
            }
        },
        confirmButton = { Button(onClick = { onImport(selected, matchId) }) { Text(if (matchId == null) "Import" else "Import transfer") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun ReimbursementPrompt(tx: TransactionItem, api: ApiClient, onDone: () -> Unit, onSkip: () -> Unit) {
    val scope = rememberCoroutineScope()
    var candidates by remember { mutableStateOf<List<ReimbursementCandidate>?>(null) }
    var err by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(tx.id) {
        ioResult { api.reimbursementCandidates(tx.id) }
            .onSuccess { candidates = it }
            .onFailure { err = it.message }
    }

    AlertDialog(
        onDismissRequest = onSkip,
        title = { Text("Is this a reimbursement?") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("If this money is your friend's share of a purchase, pair it now. It will not count as income.")
                err?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                candidates?.let { list ->
                    if (list.isEmpty()) {
                        Text("No suitable earlier purchase was found.")
                    } else {
                        list.take(8).forEach { candidate ->
                            Card(
                                Modifier.fillMaxWidth().clickable {
                                    scope.launch {
                                        ioResult { api.pairReimbursement(candidate.id, tx.id) }
                                            .onSuccess { onDone() }
                                            .onFailure { err = it.message }
                                    }
                                },
                            ) {
                                Column(Modifier.padding(10.dp)) {
                                    Text(candidate.description, fontWeight = FontWeight.Bold)
                                    Text(
                                        "${candidate.date} · purchase €${candidate.amount} · budget expense €${candidate.expenseImpact}",
                                        style = MaterialTheme.typography.bodySmall,
                                    )
                                }
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = onSkip) { Text("Not a reimbursement") } },
    )
}

@Composable
private fun BudgetScreen(api: ApiClient, initialMonth: String, onMonthChanged: (String) -> Unit) {
    var month by remember(initialMonth) { mutableStateOf(initialMonth.ifBlank { YearMonth.now().toString() }) }
    var lines by remember { mutableStateOf<List<BudgetLine>>(emptyList()) }
    var edit by remember { mutableStateOf<BudgetLine?>(null) }
    var refresh by remember { mutableIntStateOf(0) }
    var err by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(month, refresh) {
        ioResult { api.budget(month) }
            .onSuccess {
                lines = it
                err = null
            }
            .onFailure { err = it.message }
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            IconButton(onClick = { month = shiftMonth(month, -1); onMonthChanged(month) }) { Icon(Icons.Default.ChevronLeft, null) }
            Text(prettyMonth(month), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            IconButton(onClick = { month = shiftMonth(month, 1); onMonthChanged(month) }) { Icon(Icons.Default.ChevronRight, null) }
        }
        if (err != null) ErrorCard(err!!)
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("funding", "expense", "savings").forEach { kind ->
                item { SectionTitle(kind.replaceFirstChar { it.uppercase() }) }
                items(lines.filter { it.kind == kind }) { line ->
                    Card(Modifier.fillMaxWidth().clickable { edit = line }) {
                        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(line.category, fontWeight = FontWeight.Bold)
                                if (line.note.isNotBlank()) Text(line.note, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Text("Actual €${line.actual}", style = MaterialTheme.typography.bodySmall)
                            }
                            Text("€${line.planned}", fontWeight = FontWeight.Bold)
                            Spacer(Modifier.width(8.dp))
                            Icon(Icons.Default.Edit, null)
                        }
                    }
                }
            }
        }
    }

    edit?.let { line ->
        BudgetEditDialog(
            line = line,
            onDismiss = { edit = null },
            save = { amount, note -> api.saveBudgetLine(month, line, amount, note) },
            onSaved = { edit = null; refresh++ },
        )
    }
}

@Composable
private fun BudgetEditDialog(
    line: BudgetLine,
    onDismiss: () -> Unit,
    save: (String, String) -> Unit,
    onSaved: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var amount by remember { mutableStateOf(line.planned) }
    var note by remember { mutableStateOf(line.note) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(line.category) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(amount, { amount = it }, label = { Text("Planned amount") })
                OutlinedTextField(note, { note = it }, label = { Text("Note for this month") }, minLines = 2)
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            Button(
                enabled = !busy,
                onClick = {
                    busy = true
                    scope.launch {
                        ioResult { save(amount, note) }
                            .onSuccess { onSaved() }
                            .onFailure { error = it.message }
                        busy = false
                    }
                },
            ) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun AccountsScreen(api: ApiClient) {
    var data by remember { mutableStateOf<Pair<String, List<AccountItem>>?>(null) }
    var refresh by remember { mutableIntStateOf(0) }
    var err by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(refresh) {
        ioResult { api.accounts() }
            .onSuccess { data = it; err = null }
            .onFailure { err = it.message }
    }

    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { RefreshHeader("Net worth €${data?.first ?: "…"}") { refresh++ } }
        if (err != null) item { ErrorCard(err!!) }
        data?.second?.let { list ->
            list.groupBy { it.purposeLabel }.forEach { (purpose, accounts) ->
                item { SectionTitle(purpose) }
                items(accounts) { AccountRow(it) }
            }
        } ?: item { LoadingBlock() }
    }
}

@Composable
private fun MoreScreen(
    server: String,
    onTransactions: () -> Unit,
    onReports: () -> Unit,
    onBanks: () -> Unit,
    onWeb: () -> Unit,
    onChangeServer: () -> Unit,
    onLogout: () -> Unit,
) {
    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item { Text(server, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        item { MenuRow(Icons.Default.ReceiptLong, "Transactions", "Search the complete ledger", onTransactions) }
        item { MenuRow(Icons.Default.BarChart, "Reports", "3M / 6M / 12M / YTD analysis", onReports) }
        item { MenuRow(Icons.Default.Sync, "Bank sync", "Connections and interactive refresh", onBanks) }
        item { MenuRow(Icons.Default.Language, "Web administration", "Advanced settings, recurring, loans, imports", onWeb) }
        item { MenuRow(Icons.Default.Dns, "Change server", "Discover or enter another server", onChangeServer) }
        item { MenuRow(Icons.Default.Logout, "Log out", "Revoke this phone's mobile token", onLogout) }
    }
}

@Composable
private fun TransactionsScreen(api: ApiClient, onBack: () -> Unit) {
    var q by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf<List<TransactionItem>>(emptyList()) }
    var refresh by remember { mutableIntStateOf(0) }
    var err by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(refresh) {
        ioResult { api.transactions(q) }
            .onSuccess { rows = it; err = null }
            .onFailure { err = it.message }
    }

    Column {
        BackHeader(onBack)
        Row(Modifier.padding(12.dp)) {
            OutlinedTextField(q, { q = it }, label = { Text("Search transactions") }, modifier = Modifier.weight(1f))
            IconButton(onClick = { refresh++ }) { Icon(Icons.Default.Search, null) }
        }
        if (err != null) ErrorCard(err!!)
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(rows) { TransactionRow(it) }
        }
    }
}

@Composable
private fun ReportsScreen(api: ApiClient, onBack: () -> Unit) {
    var period by remember { mutableStateOf("6m") }
    var data by remember { mutableStateOf<ReportData?>(null) }
    var err by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(period) {
        ioResult { api.reports(period) }
            .onSuccess { data = it; err = null }
            .onFailure { err = it.message }
    }

    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { BackHeader(onBack) }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("3m" to "3M", "6m" to "6M", "12m" to "12M", "ytd" to "YTD").forEach { (p, label) ->
                    FilterChip(period == p, { period = p }, { Text(label) })
                }
            }
        }
        if (err != null) item { ErrorCard(err!!) }
        data?.let { d ->
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    MiniMetric("Funding", "€${d.funding}", Modifier.weight(1f))
                    MiniMetric("Expenses", "€${d.expenses}", Modifier.weight(1f))
                }
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    MiniMetric("Savings", "€${d.savings}", Modifier.weight(1f))
                    MiniMetric("Surplus", "€${d.surplus}", Modifier.weight(1f))
                }
            }
            item { SectionTitle("Monthly") }
            items(d.months) { m ->
                Card(Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(12.dp)) {
                        Text(prettyMonth(m.month), Modifier.weight(1f))
                        Text("€${m.expenses} expenses")
                    }
                }
            }
            item { SectionTitle("Expense categories") }
            items(d.categories) { c ->
                Row(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                    Text(c.name, Modifier.weight(1f))
                    Text("€${c.amount}", fontWeight = FontWeight.Bold)
                }
            }
        } ?: item { LoadingBlock() }
    }
}

@Composable
private fun BanksScreen(api: ApiClient, onOpenWeb: (String) -> Unit, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var rows by remember { mutableStateOf<List<BankItem>>(emptyList()) }
    var refresh by remember { mutableIntStateOf(0) }
    var msg by remember { mutableStateOf<String?>(null) }
    var err by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(refresh) {
        ioResult { api.banks() }
            .onSuccess { rows = it; err = null }
            .onFailure { err = it.message }
    }

    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { BackHeader(onBack) }
        if (err != null) item { ErrorCard(err!!) }
        items(rows) { bank ->
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    Text(bank.name, fontWeight = FontWeight.Bold)
                    Text("Status: ${bank.status}")
                    bank.issueMessage?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            onClick = {
                                scope.launch {
                                    ioResult { api.syncBank(bank.id) }
                                        .onSuccess {
                                            msg = when (it) {
                                                0 -> "No new items"
                                                1 -> "1 new item added to Review"
                                                else -> "$it new items added to Review"
                                            }
                                            refresh++
                                        }
                                        .onFailure { msg = it.message }
                                }
                            },
                        ) { Text("Sync now") }
                        OutlinedButton(onClick = { onOpenWeb("/banking/") }) { Text("Manage") }
                    }
                }
            }
        }
        msg?.let { item { Text(it) } }
    }
}

@Composable
private fun CalculatorOverlay(modifier: Modifier = Modifier, onClose: () -> Unit) {
    var expr by remember { mutableStateOf("") }
    var result by remember { mutableStateOf("0") }
    Surface(
        tonalElevation = 8.dp,
        shadowElevation = 12.dp,
        shape = MaterialTheme.shapes.large,
        modifier = modifier.width(300.dp).padding(12.dp).wrapContentHeight(),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Calculator", fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                IconButton(onClick = onClose) { Icon(Icons.Default.Close, null) }
            }
            OutlinedTextField(expr, { expr = it }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Text(result, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.align(Alignment.End))
            listOf(
                listOf("7", "8", "9", "/"),
                listOf("4", "5", "6", "*"),
                listOf("1", "2", "3", "-"),
                listOf("0", ".", "(", ")"),
                listOf("C", "%", "+", "="),
            ).forEach { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    row.forEach { key ->
                        Button(
                            onClick = {
                                when (key) {
                                    "C" -> { expr = ""; result = "0" }
                                    "=" -> { result = runCatching { SimpleCalculator.eval(expr).toString() }.getOrElse { "Error" } }
                                    else -> expr += key
                                }
                            },
                            contentPadding = PaddingValues(0.dp),
                            modifier = Modifier.weight(1f),
                        ) { Text(key) }
                    }
                }
            }
        }
    }
}

@Composable
private fun RefreshHeader(text: String, refresh: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(text, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
        IconButton(onClick = refresh) { Icon(Icons.Default.Refresh, null) }
    }
}

@Composable
private fun BackHeader(onBack: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, null) }
        Text("Back", fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun MiniMetric(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(modifier, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceVariant) {
        Column(Modifier.padding(horizontal = 10.dp, vertical = 9.dp)) {
            Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun AccountRow(account: AccountItem) {
    Card(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(account.name, fontWeight = FontWeight.Bold)
                Text(
                    listOf(account.institution, account.owners.joinToString(" + ")).filter { it.isNotBlank() }.joinToString(" · "),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text("${account.balance} ${account.currency}", fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun TransactionRow(t: TransactionItem) {
    Card(Modifier.fillMaxWidth()) {
        CompactTransactionRow(t)
    }
}

@Composable
private fun MenuRow(icon: ImageVector, title: String, subtitle: String, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Row(Modifier.padding(15.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, null)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(title, fontWeight = FontWeight.Bold)
                Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Icon(Icons.Default.ChevronRight, null)
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp))
}

@Composable
private fun SectionHeader(title: String, action: String, onAction: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
        TextButton(onClick = onAction) { Text(action) }
    }
}

@Composable
private fun MoneyText(amount: String, direction: String) {
    Text(
        (if (direction == "in") "+" else "−") + "€" + amount.removePrefix("-"),
        fontWeight = FontWeight.Bold,
        color = if (direction == "in") Color(0xFF34D399) else MaterialTheme.colorScheme.onSurface,
    )
}

@Composable
private fun LoadingBlock() {
    Box(Modifier.fillMaxWidth().height(100.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
}

@Composable
private fun ErrorCard(message: String) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(message, Modifier.padding(12.dp), color = MaterialTheme.colorScheme.onErrorContainer)
    }
}

@Composable
private fun EmptyState(message: String) {
    Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(message, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private fun prettyMonth(raw: String): String = runCatching {
    YearMonth.parse(raw).format(DateTimeFormatter.ofPattern("MMMM yyyy", Locale.getDefault()))
}.getOrDefault(raw)

private fun shiftMonth(raw: String, delta: Long): String = runCatching {
    YearMonth.parse(raw).plusMonths(delta).toString()
}.getOrDefault(raw)

private fun String.toMoneyDouble(): Double = replace(",", ".").filter { it.isDigit() || it == '.' || it == '-' }.toDoubleOrNull() ?: 0.0

object SimpleCalculator {
    fun eval(input: String): Double = Parser(input.replace("%", "/100").replace(" ", "")).parse()

    private class Parser(private val source: String) {
        private var pos = 0
        fun parse(): Double {
            val value = expression()
            if (pos != source.length) error("Unexpected input")
            return value
        }

        private fun expression(): Double {
            var x = term()
            while (true) {
                x = when {
                    eat('+') -> x + term()
                    eat('-') -> x - term()
                    else -> return x
                }
            }
        }

        private fun term(): Double {
            var x = factor()
            while (true) {
                x = when {
                    eat('*') -> x * factor()
                    eat('/') -> x / factor()
                    else -> return x
                }
            }
        }

        private fun factor(): Double {
            if (eat('+')) return factor()
            if (eat('-')) return -factor()
            if (eat('(')) {
                val x = expression()
                if (!eat(')')) error("Missing )")
                return x
            }
            val start = pos
            while (pos < source.length && (source[pos].isDigit() || source[pos] == '.')) pos++
            if (start == pos) error("Number expected")
            return source.substring(start, pos).toDouble()
        }

        private fun eat(char: Char): Boolean {
            if (pos < source.length && source[pos] == char) {
                pos++
                return true
            }
            return false
        }
    }
}
