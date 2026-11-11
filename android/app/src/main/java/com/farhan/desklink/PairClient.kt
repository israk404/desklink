package com.farhan.desklink

import java.io.BufferedInputStream
import java.io.DataInputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.nio.ByteBuffer
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.spec.ECGenParameterSpec
import java.security.spec.X509EncodedKeySpec
import javax.crypto.Cipher
import javax.crypto.KeyAgreement
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

class PairException(message: String) : Exception(message)

/**
 * "Find my PC" pairing: finds PCs that have the Pair window open, then does an ECDH key exchange.
 * Both screens show the same 6-digit code; the PC's user clicks Allow and the PC sends the pairing key.
 * Plain JVM code (tested against the PC app on a desktop JVM). See protocol.py for the byte layout.
 */
object PairClient {
    private val PAIR_MAGIC = "DLP1".toByteArray(Charsets.US_ASCII)

    data class Found(val host: String, val port: Int, val name: String)

    /** Broadcast for [timeoutMs] and return every PC that answered (usually one). Blocking. */
    fun discover(timeoutMs: Long, extraTargets: List<InetAddress> = emptyList()): List<Found> {
        val targets = Connection.broadcastAddresses() + extraTargets
        val found = LinkedHashMap<String, Found>()
        DatagramSocket().use { udp ->
            udp.broadcast = true
            udp.soTimeout = 300
            val nonce = Proto.randomBytes(Proto.NONCE_LEN)
            val packet = Proto.DISC_MAGIC + byteArrayOf('P'.code.toByte()) + nonce
            val buf = ByteArray(256)
            val deadline = System.currentTimeMillis() + timeoutMs
            var nextSend = 0L
            var firstAnswerAt = 0L
            while (System.currentTimeMillis() < deadline) {
                if (firstAnswerAt > 0 && System.currentTimeMillis() - firstAnswerAt > 700) break
                if (System.currentTimeMillis() >= nextSend) {
                    for (t in targets) {
                        try { udp.send(DatagramPacket(packet, packet.size, t, Proto.DISCOVERY_PORT)) } catch (_: Exception) {}
                    }
                    nextSend = System.currentTimeMillis() + 700
                }
                val dp = DatagramPacket(buf, buf.size)
                try { udp.receive(dp) } catch (e: SocketTimeoutException) { continue }
                val d = buf.copyOf(dp.length)
                if (d.size < 23 || !d.copyOfRange(0, 4).contentEquals(Proto.DISC_MAGIC) || d[4] != 'A'.code.toByte()) continue
                if (!d.copyOfRange(5, 21).contentEquals(nonce)) continue
                val port = ((d[21].toInt() and 0xFF) shl 8) or (d[22].toInt() and 0xFF)
                val host = dp.address?.hostAddress ?: continue
                found[host] = Found(host, port, String(d, 23, d.size - 23, Charsets.UTF_8).ifBlank { "PC" })
                if (firstAnswerAt == 0L) firstAnswerAt = System.currentTimeMillis()
            }
        }
        return found.values.toList()
    }

    /** Opens the pairing connection and computes the code to show. Blocking; throws PairException. */
    fun begin(pc: Found, deviceName: String): Attempt {
        val sock = Socket()
        try {
            sock.connect(InetSocketAddress(pc.host, pc.port), 3_000)
            sock.soTimeout = 10_000
            val kpg = KeyPairGenerator.getInstance("EC")
            kpg.initialize(ECGenParameterSpec("secp256r1"))
            val kp = kpg.generateKeyPair()
            val myPub = kp.public.encoded
            val name = deviceName.take(40).toByteArray(Charsets.UTF_8)
            val out = sock.getOutputStream()
            out.write(PAIR_MAGIC + ByteBuffer.allocate(2).putShort(myPub.size.toShort()).array() + myPub +
                byteArrayOf(name.size.toByte()) + name)
            out.flush()
            val input = DataInputStream(BufferedInputStream(sock.getInputStream()))
            val magic = ByteArray(4)
            input.readFully(magic)
            if (!magic.contentEquals(PAIR_MAGIC)) throw PairException("That isn't a DeskLink PC")
            val len = input.readUnsignedShort()
            if (len == 0) throw PairException("Pairing isn't open on the PC. Click “Pair phone” there first.")
            val pcPub = ByteArray(len)
            input.readFully(pcPub)
            val pcKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(pcPub))
            val ka = KeyAgreement.getInstance("ECDH")
            ka.init(kp.private)
            ka.doPhase(pcKey, true)
            val kenc = Proto.hmac(ka.generateSecret(), "desklink-pair-v1".toByteArray(Charsets.US_ASCII), myPub, pcPub)
            val h = Proto.hmac(kenc, "code".toByteArray(Charsets.US_ASCII))
            val num = ByteBuffer.wrap(h, 0, 4).int.toLong() and 0xFFFFFFFFL
            val code = String.format("%06d", num % 1_000_000)
            return Attempt(sock, input, kenc, code, pc)
        } catch (e: PairException) {
            try { sock.close() } catch (_: Exception) {}
            throw e
        } catch (e: Exception) {
            try { sock.close() } catch (_: Exception) {}
            throw PairException("Couldn't reach ${pc.name} (${e.message ?: e.javaClass.simpleName})")
        }
    }

    class Attempt internal constructor(
        private val sock: Socket, private val input: DataInputStream,
        private val kenc: ByteArray, val code: String, val pc: Found,
    ) {
        /** Waits (up to 2 min) for Allow/Deny on the PC. Returns the decrypted JSON answer. */
        fun await(): String {
            try {
                sock.soTimeout = 120_000
                val len = input.readInt()
                if (len < 28 || len > 64 * 1024) throw PairException("Unexpected answer from the PC")
                val body = ByteArray(len)
                input.readFully(body)
                val c = Cipher.getInstance("AES/GCM/NoPadding")
                c.init(Cipher.DECRYPT_MODE, SecretKeySpec(kenc, "AES"), GCMParameterSpec(128, body, 0, 12))
                c.updateAAD("pair".toByteArray(Charsets.US_ASCII))
                return String(c.doFinal(body, 12, body.size - 12), Charsets.UTF_8)
            } catch (e: PairException) {
                throw e
            } catch (e: SocketTimeoutException) {
                throw PairException("Nobody clicked Allow on the PC in time")
            } catch (e: Exception) {
                throw PairException("The PC closed the pairing window")
            } finally {
                cancel()
            }
        }

        fun cancel() {
            try { sock.close() } catch (_: Exception) {}
        }
    }
}
