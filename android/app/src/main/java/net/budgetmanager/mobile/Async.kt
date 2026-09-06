package net.budgetmanager.mobile

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Executes blocking API work on Dispatchers.IO while preserving coroutine cancellation.
 * Compose routinely cancels LaunchedEffect work when its keys or composition change; that is
 * lifecycle control, not an error that should be shown to the user.
 */
suspend fun <T> ioResult(block: () -> T): Result<T> = try {
    Result.success(withContext(Dispatchers.IO) { block() })
} catch (cancelled: CancellationException) {
    throw cancelled
} catch (error: Throwable) {
    Result.failure(error)
}
