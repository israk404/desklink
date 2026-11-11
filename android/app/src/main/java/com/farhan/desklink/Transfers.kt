package com.farhan.desklink

import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.security.MessageDigest

/** A file being streamed to the PC. [input] is opened right away so picker permissions can't expire. */
class Outgoing(val id: String, val offerJson: String, private val input: InputStream, val size: Long) {
    @Volatile var cancelled = false
    @Volatile var sent = 0L
        private set
    var onProgress: ((Outgoing) -> Unit)? = null
    var onFinished: ((Outgoing, Boolean) -> Unit)? = null
    private val sha = MessageDigest.getInstance("SHA-256")
    private val buf = ByteArray(Proto.CHUNK_SIZE)
    private val idBytes = id.toByteArray(Charsets.US_ASCII)

    /** Next chunk frame (tag + id + data), or null when the file is finished. */
    fun nextChunk(): ByteArray? {
        var n = 0
        while (n < buf.size) {
            val r = input.read(buf, n, buf.size - n)
            if (r < 0) break
            n += r
        }
        if (n == 0) return null
        sha.update(buf, 0, n)
        sent += n
        val frame = ByteArray(1 + Proto.ID_LEN + n)
        frame[0] = Proto.CHUNK_TAG
        System.arraycopy(idBytes, 0, frame, 1, Proto.ID_LEN)
        System.arraycopy(buf, 0, frame, 1 + Proto.ID_LEN, n)
        return frame
    }

    fun doneJson(): String = "{\"t\":\"file_done\",\"id\":\"$id\",\"sha256\":\"${Proto.hex(sha.digest())}\",\"size\":$sent}"

    fun close() {
        try { input.close() } catch (_: Exception) {}
    }
}

/** A file arriving from the PC, written to [file] and checked against the sender's SHA-256. */
class Incoming(val id: String, val file: File, val size: Long, val offer: Any?) {
    private val out = BufferedOutputStream(FileOutputStream(file), 256 * 1024)
    private val sha = MessageDigest.getInstance("SHA-256")
    @Volatile var received = 0L
        private set

    fun write(data: ByteArray, offset: Int, length: Int) {
        out.write(data, offset, length)
        sha.update(data, offset, length)
        received += length
        if (received > Proto.MAX_FILE) throw IllegalStateException("too large")
    }

    fun finish(sha256: String): Boolean {
        out.close()
        val ok = Proto.hex(sha.digest()) == sha256
        if (!ok) file.delete()
        return ok
    }

    fun abort() {
        try { out.close() } catch (_: Exception) {}
        file.delete()
    }
}
