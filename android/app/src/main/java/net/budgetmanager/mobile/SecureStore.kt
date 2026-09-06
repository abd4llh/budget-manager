package net.budgetmanager.mobile

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class SecureStore(context: Context) {
    private val prefs=context.getSharedPreferences("budget_manager_mobile",Context.MODE_PRIVATE)
    private val alias="budget_manager_mobile_aes"

    var serverUrl:String
        get()=prefs.getString("server_url","") ?: ""
        set(value)=prefs.edit().putString("server_url",value).apply()

    var lastUsername:String
        get()=prefs.getString("last_username","") ?: ""
        set(value)=prefs.edit().putString("last_username",value).apply()

    fun saveToken(token:String) {
        val cipher=Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE,getOrCreateKey())
        val encrypted=cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        val value=Base64.encodeToString(cipher.iv,Base64.NO_WRAP)+":"+Base64.encodeToString(encrypted,Base64.NO_WRAP)
        prefs.edit().putString("token",value).apply()
    }

    fun loadToken():String {
        val stored=prefs.getString("token","") ?: return ""
        if (!stored.contains(':')) return ""
        return try {
            val parts=stored.split(':',limit=2)
            val iv=Base64.decode(parts[0],Base64.NO_WRAP)
            val data=Base64.decode(parts[1],Base64.NO_WRAP)
            val cipher=Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE,getOrCreateKey(),GCMParameterSpec(128,iv))
            String(cipher.doFinal(data),Charsets.UTF_8)
        } catch (_:Exception) { "" }
    }

    fun clearToken(){ prefs.edit().remove("token").apply() }
    fun clearAll(){ prefs.edit().clear().apply() }

    private fun getOrCreateKey():SecretKey {
        val ks=KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(alias,null) as? SecretKey)?.let { return it }
        val generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore")
        generator.init(KeyGenParameterSpec.Builder(alias,KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build())
        return generator.generateKey()
    }
}
