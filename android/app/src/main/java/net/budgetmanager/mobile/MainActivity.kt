package net.budgetmanager.mobile

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { BudgetManagerTheme { BudgetManagerApp() } }
    }
}

private val Navy = Color(0xFF0F172A)
private val Teal = Color(0xFF10B981)
private val TealLight = Color(0xFF6EE7B7)

@Composable
fun BudgetManagerTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val scheme = if (dark) {
        darkColorScheme(
            primary = TealLight,
            secondary = Teal,
            background = Navy,
            surface = Color(0xFF111C31),
            surfaceVariant = Color(0xFF18243B),
        )
    } else {
        lightColorScheme(
            primary = Color(0xFF047857),
            secondary = Teal,
            background = Color(0xFFF5F7FB),
            surface = Color.White,
            surfaceVariant = Color(0xFFE8EEF7),
        )
    }
    MaterialTheme(colorScheme = scheme, typography = Typography(), content = content)
}

enum class RootMode { SERVER, LOGIN, MAIN }

@Composable
fun BudgetManagerApp() {
    val context = androidx.compose.ui.platform.LocalContext.current
    val store = remember { SecureStore(context) }
    var server by remember { mutableStateOf(store.serverUrl) }
    var token by remember { mutableStateOf(store.loadToken()) }
    val api = remember { ApiClient(server, token) }
    var mode by remember {
        mutableStateOf(
            if (server.isBlank()) RootMode.SERVER
            else if (token.isBlank()) RootMode.LOGIN
            else RootMode.MAIN
        )
    }
    var activeMonth by remember { mutableStateOf("") }
    var categories by remember { mutableStateOf<List<Category>>(emptyList()) }
    var fatal by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    fun configureServer(url: String) {
        server = url
        store.serverUrl = url
        store.clearToken()
        token = ""
        api.setServer(url)
        api.setToken("")
        mode = RootMode.LOGIN
    }

    fun signOut() {
        scope.launch {
            ioResult { api.logout() }
            store.clearToken()
            token = ""
            api.setToken("")
            mode = RootMode.LOGIN
        }
    }

    LaunchedEffect(mode, token, server) {
        if (mode == RootMode.MAIN) {
            api.setServer(server)
            api.setToken(token)
            ioResult { api.bootstrap() }
                .onSuccess {
                    activeMonth = it.first
                    categories = it.second
                    fatal = null
                }
                .onFailure {
                    if (it is ApiException && it.status == 401) {
                        store.clearToken()
                        token = ""
                        mode = RootMode.LOGIN
                    } else {
                        fatal = it.message
                    }
                }
        }
    }

    when (mode) {
        RootMode.SERVER -> ServerSelectionScreen(current = server, onConnected = ::configureServer)
        RootMode.LOGIN -> LoginScreen(
            server = server,
            lastUsername = store.lastUsername,
            onBack = { mode = RootMode.SERVER },
            onLogin = { username, password ->
                scope.launch {
                    ioResult {
                        api.setServer(server)
                        api.login(username, password)
                    }.onSuccess {
                        store.lastUsername = username
                        store.saveToken(it.token)
                        token = it.token
                        api.setToken(token)
                        mode = RootMode.MAIN
                        fatal = null
                    }.onFailure {
                        fatal = it.message
                    }
                }
            },
        )
        RootMode.MAIN -> MainShell(
            api = api,
            server = server,
            activeMonth = activeMonth,
            categories = categories,
            onMonthChanged = { activeMonth = it },
            onChangeServer = {
                store.clearAll()
                server = ""
                token = ""
                mode = RootMode.SERVER
            },
            onLogout = ::signOut,
            onOpenWeb = { path ->
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(server.trimEnd('/') + path)))
            },
        )
    }

    if (fatal != null) {
        AlertDialog(
            onDismissRequest = { fatal = null },
            confirmButton = { TextButton(onClick = { fatal = null }) { Text("OK") } },
            title = { Text("Budget Manager") },
            text = { Text(fatal ?: "") },
        )
    }
}
