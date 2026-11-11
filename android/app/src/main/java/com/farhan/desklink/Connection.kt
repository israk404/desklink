package com.farhan.desklink

import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.OutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.Socket
import java.net.SocketTimeoutException
import java.nio.ByteBuffer
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Keeps one encrypted TCP connection to the PC alive: tries the last good address, then every
 * address from the QR code, then UDP discovery; reconnects with backoff. Plain JVM code.
 */
class Connection(private val listener: Listener) {

    enum class Status { NOT_PAIRED, CONNECTING, SEARCHING, CONNECTED, OFFLINE, REJECTED }

    interface Listener {
        fun pairing(): Pairing?
        fun deviceName(): String
        fun lastGoodAddress(): Pair<String, Int>?
        fun onGoodAddress(host: String, port: Int)
        fun onStatus(status: Status)
        fun onMessage(json: String)
        /** A file-chunk frame (0x01 + 12-char transfer id + data), on the connection thread. */
        fun onBinary(frame: ByteArray) {}
        fun log(msg: String) {}
    }

    @Volatile private var running = false
    @Volatile private var generation = 0
    @Volatile private var session: Session? = null
    private val sleeper = Object()
    private var kicked = false

    /** Extra UDP targets for discovery (used by tests; real phones use broadcast). */
    var extraDiscoveryTargets: List<InetAddress> = emptyList()

    val isConnected: Boolean get() = session?.ready == true

    @Synchronized
    fun start() {
        if (running) return
        running = true
        val gen = ++generation
        Thread({ loop(gen) }, "desklink-conn").apply { isDaemon = true; start() }
    }

    @Synchronized
    fun stop() {
        running = false
        generation++
        session?.close()
        kick()
    }

    /** Retry right now instead of waiting out the backoff (network changed, app opened, re-paired). */
    fun kick() {
        synchronized(sleeper) {
            kicked = true
            sleeper.notifyAll()
        }
    }

    /** Drop the current connection (if any) and connect again, e.g. after pairing with a new PC. */
    fun reconnect() {
        session?.close()
        kick()
    }

    /** Queue a JSON message. Returns false when not connected (caller keeps it for later). */
    fun send(json: String): Boolean = session?.takeIf { it.ready }?.enqueue(json) ?: false

    /** Queue a file. Returns false when not connected. Control messages always go ahead of file data. */
    fun sendFile(out: Outgoing): Boolean = session?.takeIf { it.ready }?.enqueueFile(out) ?: false

    private fun alive(gen: Int) = running && gen == generation

    private fun pause(ms: Long) {
        synchronized(sleeper) {
            if (!kicked) sleeper.wait(ms)
            kicked = false
        }
    }

    private fun loop(gen: Int) {
        var backoff = 2_000L
        while (alive(gen)) {
            val pair = listener.pairing()
            if (pair == null) {
                listener.onStatus(Status.NOT_PAIRED)
                pause(60_000)
                continue
            }
            listener.onStatus(Status.CONNECTING)
            val candidates = LinkedHashSet<Pair<String, Int>>()
            listener.lastGoodAddress()?.let { candidates.add(it) }
            pair.hosts.forEach { candidates.add(it to pair.port) }

            var sock: Socket? = null
            var where: Pair<String, Int>? = null
            for (c in candidates) {
                if (!alive(gen)) break
                sock = tryConnect(c.first, c.second)
                if (sock != null) { where = c; break }
            }
            if (sock == null && alive(gen)) {
                listener.onStatus(Status.SEARCHING)
                val found = discover(pair.key, 2_500)
                if (found != null) {
                    sock = tryConnect(found.first, found.second)
                    if (sock != null) where = found
                }
            }
            if (sock == null || where == null) {
                if (!alive(gen)) break
                listener.onStatus(Status.OFFLINE)
                pause(backoff)
                backoff = (backoff * 2).coerceAtMost(30_000)
                continue
            }
            when (runSession(sock, pair, where, gen)) {
                End.REJECTED -> {
                    listener.onStatus(Status.REJECTED)
                    pause(5 * 60_000)  // until re-paired (kick) or a while has passed
                }
                End.CLOSED -> {
                    backoff = 2_000L
                    if (alive(gen)) {
                        listener.onStatus(Status.CONNECTING)
                        pause(1_000)
                    }
                }
            }
        }
    }

    private fun tryConnect(host: String, port: Int): Socket? = try {
        Socket().apply { connect(InetSocketAddress(host, port), 2_500) }
    } catch (e: Exception) {
        listener.log("connect $host:$port failed: ${e.message}")
        null
    }

    private enum class End { CLOSED, REJECTED }

    private fun runSession(sock: Socket, pair: Pairing, where: Pair<String, Int>, gen: Int): End {
        val s = Session(sock)
        session = s
        var handshakeDone = false
        var gotFirst = false
        try {
            sock.tcpNoDelay = true
            sock.keepAlive = true
            sock.soTimeout = 10_000
            val out = BufferedOutputStream(sock.getOutputStream())
            val input = DataInputStream(BufferedInputStream(sock.getInputStream()))
            val clientNonce = Proto.randomBytes(Proto.NONCE_LEN)
            out.write(Proto.MAGIC)
            out.write(clientNonce)
            out.flush()
            val reply = ByteArray(4 + Proto.NONCE_LEN)
            input.readFully(reply)
            if (!reply.copyOfRange(0, 4).contentEquals(Proto.MAGIC)) return End.CLOSED
            handshakeDone = true
            val channel = SecureChannel(
                Proto.sessionKey(pair.key, clientNonce, reply.copyOfRange(4, reply.size)),
                Proto.DIR_CLIENT, Proto.DIR_SERVER,
            )
            val hello = "{\"t\":\"hello\",\"v\":${Proto.VERSION},\"device\":${jsonString(listener.deviceName())}}"
            s.startWriter(out, channel, hello)
            sock.soTimeout = 50_000  // the PC answers our 20 s pings, so silence means a dead link
            while (alive(gen) && s.alive) {
                val len = input.readInt()
                if (len <= 16 || len > Proto.MAX_FRAME) return End.CLOSED
                val frame = ByteArray(len)
                input.readFully(frame)
                val plain = channel.open(frame)
                if (plain.isNotEmpty() && plain[0] == Proto.CHUNK_TAG) {
                    if (gotFirst) listener.onBinary(plain)
                    continue
                }
                val json = String(plain, Charsets.UTF_8)
                if (!gotFirst) {
                    gotFirst = true
                    s.ready = true
                    listener.onGoodAddress(where.first, where.second)
                    listener.onStatus(Status.CONNECTED)
                }
                listener.onMessage(json)
            }
            return End.CLOSED
        } catch (e: SocketTimeoutException) {
            listener.log("timeout: ${e.message}")
            return End.CLOSED
        } catch (e: Exception) {
            listener.log("session ended: $e")
            // The PC hangs up right after our hello when the pairing key is wrong.
            return if (handshakeDone && !gotFirst && s.alive) End.REJECTED else End.CLOSED
        } finally {
            s.close()
            if (session === s) session = null
        }
    }

    private inner class Session(private val sock: Socket) {
        @Volatile var ready = false
        @Volatile var alive = true
        private val queue = LinkedBlockingQueue<String>()
        private val files = java.util.concurrent.ConcurrentLinkedQueue<Outgoing>()
        private var writer: Thread? = null

        fun enqueue(json: String): Boolean {
            if (!alive) return false
            queue.offer(json)
            return true
        }

        fun enqueueFile(out: Outgoing): Boolean {
            if (!alive) return false
            queue.offer(out.offerJson)  // the offer always goes out before the file's first chunk
            files.offer(out)
            queue.offer("")             // wake the writer if it is waiting
            return true
        }

        fun startWriter(out: OutputStream, channel: SecureChannel, first: String) {
            writer = Thread({
                var lastPing = System.currentTimeMillis()
                try {
                    writeFrame(out, channel, first.toByteArray(Charsets.UTF_8))
                    while (alive) {
                        var msg = queue.poll()
                        if (msg == null) {
                            val tr = files.peek()
                            if (tr != null) {
                                if (System.currentTimeMillis() - lastPing > 20_000) {
                                    // keep the PC answering (pong) so our reader doesn't time out mid-upload
                                    writeFrame(out, channel, PING)
                                    lastPing = System.currentTimeMillis()
                                }
                                streamStep(out, channel, tr)
                                continue
                            }
                            msg = queue.poll(20, TimeUnit.SECONDS)
                            if (msg == null) {
                                writeFrame(out, channel, PING)
                                lastPing = System.currentTimeMillis()
                                continue
                            }
                        }
                        if (msg.isNotEmpty()) writeFrame(out, channel, msg.toByteArray(Charsets.UTF_8))
                    }
                } catch (e: InterruptedException) {
                    // closing
                } catch (e: Exception) {
                    listener.log("write failed: $e")
                    close()
                }
            }, "desklink-writer").apply { isDaemon = true; start() }
        }

        /** Send one chunk of the current file (or finish / drop it). */
        private fun streamStep(out: OutputStream, channel: SecureChannel, tr: Outgoing) {
            if (tr.cancelled) {
                files.poll()
                tr.close()
                tr.onFinished?.invoke(tr, false)
                return
            }
            val frame = try {
                tr.nextChunk()
            } catch (e: Exception) {  // the source file went away: tell the PC, keep the connection
                files.poll()
                tr.close()
                writeFrame(out, channel, "{\"t\":\"file_cancel\",\"id\":\"${tr.id}\"}".toByteArray())
                tr.onFinished?.invoke(tr, false)
                return
            }
            if (frame != null) {
                writeFrame(out, channel, frame)
                tr.onProgress?.invoke(tr)
            } else {
                files.poll()
                writeFrame(out, channel, tr.doneJson().toByteArray(Charsets.UTF_8))
                tr.close()
                tr.onFinished?.invoke(tr, true)
            }
        }

        private fun writeFrame(out: OutputStream, channel: SecureChannel, plain: ByteArray) {
            val ct = channel.seal(plain)
            out.write(ByteBuffer.allocate(4).putInt(ct.size).array())
            out.write(ct)
            out.flush()
        }

        fun close() {
            alive = false
            ready = false
            try { sock.close() } catch (_: Exception) {}
            writer?.interrupt()
            while (true) {
                val tr = files.poll() ?: break
                tr.close()
                tr.onFinished?.invoke(tr, false)
            }
        }
    }

    /** Broadcast "who has this pairing key?" on every local network and wait for the PC to answer. */
    private fun discover(key: ByteArray, timeoutMs: Long): Pair<String, Int>? {
        val targets = broadcastAddresses() + extraDiscoveryTargets
        if (targets.isEmpty()) return null
        try {
            DatagramSocket().use { udp ->
                udp.broadcast = true
                udp.soTimeout = 400
                val (packet, nonce) = Proto.discoveryRequest(key)
                val buf = ByteArray(256)
                val deadline = System.currentTimeMillis() + timeoutMs
                var nextSend = 0L
                while (System.currentTimeMillis() < deadline) {
                    if (System.currentTimeMillis() >= nextSend) {
                        for (t in targets) {
                            try { udp.send(DatagramPacket(packet, packet.size, t, Proto.DISCOVERY_PORT)) } catch (_: Exception) {}
                        }
                        nextSend = System.currentTimeMillis() + 800
                    }
                    val dp = DatagramPacket(buf, buf.size)
                    try { udp.receive(dp) } catch (e: SocketTimeoutException) { continue }
                    val answer = Proto.parseDiscoveryResponse(key, nonce, buf.copyOf(dp.length)) ?: continue
                    val host = dp.address?.hostAddress ?: continue
                    listener.log("discovered PC at $host:${answer.first}")
                    return host to answer.first
                }
            }
        } catch (e: Exception) {
            listener.log("discovery failed: $e")
        }
        return null
    }

    companion object {
        private val PING = "{\"t\":\"ping\"}".toByteArray(Charsets.UTF_8)

        fun broadcastAddresses(): List<InetAddress> {
        val out = ArrayList<InetAddress>()
        try {
            NetworkInterface.getNetworkInterfaces()?.toList().orEmpty().forEach { nif ->
                try {
                    if (nif.isUp && !nif.isLoopback) nif.interfaceAddresses.mapNotNullTo(out) { it.broadcast }
                } catch (_: Exception) {}
            }
        } catch (_: Exception) {}
        try { out.add(InetAddress.getByName("255.255.255.255")) } catch (_: Exception) {}
        return out.distinct()
        }

        fun jsonString(s: String): String {
            val sb = StringBuilder("\"")
            for (ch in s) {
                when {
                    ch == '"' -> sb.append("\\\"")
                    ch == '\\' -> sb.append("\\\\")
                    ch < ' ' -> sb.append(String.format("\\u%04x", ch.code))
                    else -> sb.append(ch)
                }
            }
            return sb.append('"').toString()
        }
    }
}
