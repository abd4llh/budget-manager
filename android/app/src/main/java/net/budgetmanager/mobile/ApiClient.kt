package net.budgetmanager.mobile

import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder

class ApiException(val status:Int,val code:String,message:String):Exception(message)

class ApiClient(private var baseUrl:String, private var token:String="") {
    fun setServer(url:String){ baseUrl=url.trimEnd('/') }
    fun setToken(value:String){ token=value }
    fun server():String=baseUrl

    fun mobileInfo():JSONObject=request("GET","/mobile/info/",auth=false)

    fun login(username:String,password:String):SessionInfo {
        val body=JSONObject().put("username",username).put("password",password).put("device_name","${Build.MANUFACTURER} ${Build.MODEL}")
        val o=request("POST","/api/mobile/v1/auth/login/",body,auth=false)
        token=o.getString("token")
        return SessionInfo(token,o.getJSONObject("user").optString("name"),o.getJSONObject("household").optString("name"),o.getJSONObject("household").optString("role"))
    }

    fun logout(){ request("POST","/api/mobile/v1/auth/logout/",JSONObject()); token="" }
    fun bootstrap():Pair<String,List<Category>> {
        val o=request("GET","/api/mobile/v1/bootstrap/")
        val cats=o.getJSONArray("categories").mapObjects { Category(it.getInt("id"),it.getString("name"),it.getString("kind")) }
        return o.getString("active_month") to cats
    }

    fun dashboard(month:String?=null):DashboardData {
        val q=month?.let{"?month=${enc(it)}"}?:""
        val o=request("GET","/api/mobile/v1/dashboard/$q")
        val actual=o.getJSONObject("actual"); val planned=o.getJSONObject("planned")
        return DashboardData(o.getString("month"),o.getString("net_worth"),o.getInt("review_count"),
            actual.getString("funding"),actual.getString("expenses"),actual.getString("savings"),actual.getString("remaining"),
            planned.getString("funding"),planned.getString("expenses"),planned.getString("savings"),planned.getString("remaining"),
            o.getJSONArray("accounts").mapObjects(::parseAccount),o.getJSONArray("recent").mapObjects(::parseTransaction))
    }

    fun accounts():Pair<String,List<AccountItem>> {
        val o=request("GET","/api/mobile/v1/accounts/")
        return o.getString("net_worth") to o.getJSONArray("accounts").mapObjects(::parseAccount)
    }

    fun budget(month:String):List<BudgetLine> {
        val o=request("GET","/api/mobile/v1/budget/?month=${enc(month)}")
        return o.getJSONArray("lines").mapObjects { BudgetLine(it.getInt("category_id"),it.getString("category"),it.getString("kind"),it.getString("planned"),it.getString("actual"),it.optString("note")) }
    }

    fun saveBudgetLine(month:String,line:BudgetLine,amount:String,note:String) {
        request("POST","/api/mobile/v1/budget/line/",JSONObject().put("month",month).put("category_id",line.categoryId).put("amount",amount).put("note",note))
    }

    fun review(q:String="",direction:String="",status:String="pending"):List<ReviewItem> {
        val path="/api/mobile/v1/review/?status=${enc(status)}&q=${enc(q)}&direction=${enc(direction)}&page_size=100"
        return request("GET",path).getJSONArray("rows").mapObjects(::parseReview)
    }
    fun ignoreReview(id:Int){ request("POST","/api/mobile/v1/review/$id/ignore/",JSONObject()) }
    fun restoreReview(id:Int){ request("POST","/api/mobile/v1/review/$id/restore/",JSONObject()) }
    fun importReview(id:Int,categoryId:Int?,matchId:Int?=null):TransactionItem? {
        val body=JSONObject(); if(categoryId!=null) body.put("category_id",categoryId); if(matchId!=null) body.put("match_id",matchId)
        val o=request("POST","/api/mobile/v1/review/$id/import/",body)
        return if(o.isNull("transaction")) null else parseTransaction(o.getJSONObject("transaction"))
    }

    fun transactions(q:String=""):List<TransactionItem> = request("GET","/api/mobile/v1/transactions/?q=${enc(q)}&page_size=100").getJSONArray("rows").mapObjects(::parseTransaction)
    fun reimbursementCandidates(transactionId:Int):List<ReimbursementCandidate> = request("GET","/api/mobile/v1/reimbursements/$transactionId/candidates/").getJSONArray("rows").mapObjects {
        ReimbursementCandidate(it.getInt("id"),it.getString("date"),it.getString("description"),it.getString("amount"),it.getString("expense_impact"))
    }
    fun pairReimbursement(purchaseId:Int,reimbursementId:Int){ request("POST","/api/mobile/v1/reimbursements/pair/",JSONObject().put("purchase_id",purchaseId).put("reimbursement_id",reimbursementId)) }

    fun reports(period:String):ReportData {
        val o=request("GET","/api/mobile/v1/reports/?period=${enc(period)}"); val s=o.getJSONObject("summary")
        return ReportData(o.getString("period"),o.getString("from_month"),o.getString("to_month"),s.getString("funding"),s.getString("expenses"),s.getString("savings"),s.getString("surplus"),
            o.getJSONArray("months").mapObjects { ReportMonth(it.getString("month"),it.getString("funding"),it.getString("expenses"),it.getString("savings"),it.getString("surplus"),it.getString("net_worth")) },
            o.getJSONArray("categories").mapObjects { ReportCategory(it.getString("name"),it.getString("amount")) })
    }

    fun banks():List<BankItem> = request("GET","/api/mobile/v1/banks/").getJSONArray("connections").mapObjects {
        val issue=it.optJSONObject("last_sync_issue")
        BankItem(it.getInt("id"),it.getString("name"),it.getString("status"),it.optNullable("consent_valid_until"),it.optNullable("last_sync_at"),it.optNullable("next_background_sync_at"),issue?.optString("title"),issue?.optString("message"))
    }
    fun syncBank(id:Int):Int = request("POST","/api/mobile/v1/banks/$id/sync/",JSONObject()).optInt("new_review_items",0)

    private fun request(method:String,path:String,body:JSONObject?=null,auth:Boolean=true):JSONObject {
        if(baseUrl.isBlank()) throw ApiException(0,"no_server","No server is selected.")
        val url=java.net.URL(baseUrl.trimEnd('/')+path)
        val c=(url.openConnection() as HttpURLConnection).apply {
            requestMethod=method; connectTimeout=8000; readTimeout=20000; setRequestProperty("Accept","application/json")
            setRequestProperty("User-Agent","BudgetManager-Android/0.9.0-beta (${Build.MODEL})")
            if(auth && token.isNotBlank()) setRequestProperty("Authorization","Bearer $token")
            if(body!=null){ doOutput=true; setRequestProperty("Content-Type","application/json; charset=utf-8") }
        }
        try {
            if(body!=null) c.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val status=c.responseCode
            val stream=if(status in 200..299)c.inputStream else c.errorStream
            val text=stream?.let { BufferedReader(InputStreamReader(it)).use(BufferedReader::readText) } ?: "{}"
            val obj=try{JSONObject(text)}catch(_:Exception){JSONObject()}
            if(status !in 200..299 || !obj.optBoolean("ok",status in 200..299)) throw ApiException(status,obj.optString("error","http_error"),obj.optString("message","Server returned HTTP $status"))
            return obj
        } finally { c.disconnect() }
    }

    private fun parseAccount(o:JSONObject):AccountItem {
        val owners=o.optJSONArray("owners")?.mapObjects { it.optString("name") } ?: emptyList()
        return AccountItem(o.getInt("id"),o.getString("name"),o.optString("institution"),o.getString("currency"),o.getString("purpose"),o.optString("purpose_label"),o.getString("balance"),owners)
    }
    private fun parseTransaction(o:JSONObject):TransactionItem {
        val impacts=o.optJSONArray("budget_impacts")
        val cat=if(impacts!=null && impacts.length()>0) impacts.getJSONObject(0).optString("category") else null
        val from=o.optJSONObject("from_account")?.optString("name"); val to=o.optJSONObject("to_account")?.optString("name")
        val reimbursement=o.optJSONObject("reimbursement")
        return TransactionItem(o.getInt("id"),o.getString("date"),o.getString("kind"),o.getString("description"),o.optString("payee"),o.getString("amount"),from,to,cat,reimbursement?.optString("role"),reimbursement?.optInt("paired_transaction_id"))
    }
    private fun parseReview(o:JSONObject):ReviewItem {
        val candidates=o.optJSONArray("transfer_candidates")?.mapObjects { TransferCandidate(it.getInt("id"),it.getString("date"),it.getString("amount"),it.getString("description"),it.getString("bank"),it.optNullable("mapped_account")) } ?: emptyList()
        return ReviewItem(o.getInt("id"),o.getString("date"),o.getString("amount"),o.getString("direction"),o.getString("currency"),o.getString("description"),o.optString("counterparty"),o.getString("bank"),o.optString("feed"),o.optNullable("mapped_account"),if(o.isNull("suggested_category_id"))null else o.getInt("suggested_category_id"),o.optNullable("suggested_category"),o.optString("suggestion_source"),o.optString("suggestion_confidence"),o.optString("suggestion_reason"),o.optString("allowed_category_kind",if(o.optString("direction")=="in")"funding" else "expense"),candidates)
    }

    companion object {
        private fun enc(s:String)=URLEncoder.encode(s,"UTF-8")
    }
}

private fun <T> JSONArray.mapObjects(block:(JSONObject)->T):List<T>{ val out=ArrayList<T>(length()); for(i in 0 until length()) out.add(block(getJSONObject(i))); return out }
private fun JSONObject.optNullable(key:String):String? = if(isNull(key)) null else optString(key).takeIf{it.isNotBlank()}
