package net.budgetmanager.mobile

import android.content.Context
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

object Discovery {
    suspend fun scan(context:Context,timeoutMs:Int=2200):List<ServerInfo> = withContext(Dispatchers.IO) {
        val found=linkedMapOf<String,ServerInfo>()
        val wifi=context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val lock=wifi.createMulticastLock("budget-manager-discovery").apply { setReferenceCounted(false); acquire() }
        try {
            DatagramSocket().use { socket ->
                socket.broadcast=true
                socket.soTimeout=300
                val payload="BUDGET_MANAGER_DISCOVER".toByteArray(Charsets.UTF_8)
                val packet=DatagramPacket(payload,payload.size,InetAddress.getByName("255.255.255.255"),7358)
                socket.send(packet)
                val end=System.currentTimeMillis()+timeoutMs
                while (System.currentTimeMillis()<end) {
                    try {
                        val buf=ByteArray(4096)
                        val response=DatagramPacket(buf,buf.size)
                        socket.receive(response)
                        val text=String(response.data,0,response.length,Charsets.UTF_8)
                        val obj=JSONObject(text)
                        if (obj.optString("product")!="budget-manager") continue
                        val address=obj.optString("address")
                        if (address.isNotBlank()) found[address]=ServerInfo(obj.optString("name","Budget Manager"),address,obj.optString("version"))
                    } catch (_:java.net.SocketTimeoutException) { }
                    catch (_:Exception) { }
                }
            }
        } finally { if (lock.isHeld) lock.release() }
        found.values.toList()
    }

    fun normalizeAddress(raw:String):String {
        var s=raw.trim().trimEnd('/')
        if (s.isBlank()) return ""
        if (!s.startsWith("http://",true) && !s.startsWith("https://",true)) s="http://$s"
        return try {
            val u=java.net.URI.create(s)
            if (u.port<0 && u.scheme.equals("http",true)) "http://${u.host}:8015" else s
        } catch (_:Exception) { s }
    }
}
