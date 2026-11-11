package com.farhan.desklink

import java.net.URLDecoder
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Wire protocol, phone side. Mirrors windows/desklink/protocol.py — keep the two in sync.
 * Plain JVM code (no Android imports) so it can be tested against the PC app on a desktop JVM.
 */
object Proto {
    const val VERSION = 2
    val MAGIC = "DLR1".toByteArray(Charsets.US_ASCII)
    val DISC_MAGIC = "DLD1".toByteArray(Charsets.US_ASCII)
    const val DEFAULT_PORT = 47831
    const val DISCOVERY_PORT = 47832
    const val NONCE_LEN = 16
    const val MAX_FRAME = 8 * 1024 * 1024
    const val MAX_TEXT = 1_000_000
    const val DIR_CLIENT = 1
    const val DIR_SERVER = 2

    // File transfer (see protocol.py): offer JSON, then chunk frames 0x01 + 12-char id + data, then done JSON.
    const val CHUNK_SIZE = 256 * 1024
    const val CHUNK_TAG: Byte = 0x01
    const val ID_LEN = 12
    const val MAX_FILE = 4L * 1024 * 1024 * 1024
    const val MAX_CLIP_IMAGE = 40L * 1024 * 1024

    fun newId(): String = randomBytes(6).joinToString("") { "%02x".format(it) }

    fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it) }

    private val rng = SecureRandom()

    fun randomBytes(n: Int): ByteArray = ByteArray(n).also { rng.nextBytes(it) }

    fun hmac(key: ByteArray, vararg parts: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        parts.forEach { mac.update(it) }
        return mac.doFinal()
    }

    fun sessionKey(pairKey: ByteArray, clientNonce: ByteArray, serverNonce: ByteArray): ByteArray =
        hmac(pairKey, "desklink-session-v1".toByteArray(Charsets.US_ASCII), clientNonce, serverNonce)

    private fun mac16(key: ByteArray, label: String, vararg data: ByteArray): ByteArray =
        hmac(key, label.toByteArray(Charsets.US_ASCII), *data).copyOf(16)

    /** Returns the UDP packet to broadcast and the nonce needed to check the answer. */
    fun discoveryRequest(pairKey: ByteArray): Pair<ByteArray, ByteArray> {
        val nonce = randomBytes(NONCE_LEN)
        return (DISC_MAGIC + byteArrayOf('Q'.code.toByte()) + nonce + mac16(pairKey, "disc-q", nonce)) to nonce
    }

    /** Returns (port, pcName) when the answer really comes from the PC we're paired with. */
    fun parseDiscoveryResponse(pairKey: ByteArray, nonce: ByteArray, data: ByteArray): Pair<Int, String>? {
        if (data.size < 23 || !data.copyOfRange(0, 4).contentEquals(DISC_MAGIC) || data[4] != 'R'.code.toByte()) return null
        val mac = data.copyOfRange(5, 21)
        val body = data.copyOfRange(21, data.size)
        if (!MessageDigest.isEqual(mac, mac16(pairKey, "disc-r", nonce, body))) return null
        val port = ((body[0].toInt() and 0xFF) shl 8) or (body[1].toInt() and 0xFF)
        return port to String(body, 2, body.size - 2, Charsets.UTF_8)
    }
}

/** AES-256-GCM per frame. The nonce is (direction, counter), so frames must arrive in order. */
class SecureChannel(key: ByteArray, private val sendDir: Int, private val recvDir: Int) {
    private val keySpec = SecretKeySpec(key, "AES")
    private var sendCounter = 0L
    private var recvCounter = 0L

    private fun nonce(dir: Int, counter: Long): ByteArray =
        ByteBuffer.allocate(12).putInt(dir).putLong(counter).array()

    @Synchronized
    fun seal(plain: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, keySpec, GCMParameterSpec(128, nonce(sendDir, sendCounter)))
        sendCounter++
        return c.doFinal(plain)
    }

    fun open(ciphertext: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, keySpec, GCMParameterSpec(128, nonce(recvDir, recvCounter)))
        val plain = c.doFinal(ciphertext)
        recvCounter++
        return plain
    }
}

/** What the PC's QR code contains: desklink://pair?v=1&k=KEY&p=PORT&h=IP1,IP2&n=NAME */
class Pairing(val key: ByteArray, val port: Int, val hosts: List<String>, val pcName: String) {
    fun keyText(): String = Base64.getUrlEncoder().withoutPadding().encodeToString(key)

    companion object {
        fun parse(raw: String): Pairing? {
            val text = raw.trim()
            val prefix = "desklink://pair?"
            if (!text.startsWith(prefix, ignoreCase = true)) return null
            val params = HashMap<String, String>()
            for (part in text.substring(prefix.length).split('&')) {
                val eq = part.indexOf('=')
                if (eq <= 0) continue
                params[part.substring(0, eq)] = URLDecoder.decode(part.substring(eq + 1), "UTF-8")
            }
            val key = try {
                Base64.getUrlDecoder().decode(params["k"] ?: return null)
            } catch (e: IllegalArgumentException) {
                return null
            }
            if (key.size != 32) return null
            val port = params["p"]?.toIntOrNull()?.takeIf { it in 1..65535 } ?: Proto.DEFAULT_PORT
            val hosts = params["h"].orEmpty().split(',').map { it.trim() }
                .filter { it.matches(Regex("""^\d{1,3}(\.\d{1,3}){3}$""")) }
            return Pairing(key, port, hosts, params["n"]?.takeIf { it.isNotBlank() } ?: "PC")
        }

        fun fromStored(key: String?, port: Int, hosts: String?, name: String?): Pairing? {
            if (key.isNullOrEmpty()) return null
            val bytes = try { Base64.getUrlDecoder().decode(key) } catch (e: IllegalArgumentException) { return null }
            if (bytes.size != 32) return null
            return Pairing(bytes, port, hosts.orEmpty().split(',').filter { it.isNotBlank() }, name ?: "PC")
        }
    }
}
