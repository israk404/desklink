package com.farhan.desklink

import android.app.Application
import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArraySet

/** A file the PC sent to this phone. */
class FileEntry(
    val id: String, val name: String, val mime: String, val size: Long, val device: String,
    val ts: Long, @Volatile var status: String, @Volatile var progress: Long, val path: String?, var saved: Boolean,
) {
    val active: Boolean get() = status == "receiving"
    val isMedia: Boolean get() = mime.startsWith("image/") || mime.startsWith("video/")
    val file: File? get() = path?.let { File(it) }?.takeIf { it.exists() }

    fun toJson(): JSONObject = JSONObject().put("id", id).put("name", name).put("mime", mime).put("size", size)
        .put("device", device).put("ts", ts).put("status", status).put("progress", progress)
        .put("path", path ?: "").put("saved", saved)

    companion object {
        fun from(o: JSONObject): FileEntry = FileEntry(
            o.optString("id"), o.optString("name"), o.optString("mime"), o.optLong("size", -1),
            o.optString("device"), o.optLong("ts"),
            o.optString("status").let { if (it == "receiving") "failed" else it },
            o.optLong("progress"), o.optString("path").ifEmpty { null }, o.optBoolean("saved"),
        )
    }
}

enum class SendResult { SENT, QUEUED, EMPTY, NOT_PAIRED }

/**
 * App-wide state: pairing, received files and the live connection to the PC.
 * UI state changes happen on the main thread; file transfers are handled on the connection thread.
 */
object Hub : Connection.Listener {
    private const val TAG = "DeskLink"
    private const val FILES_LIMIT = 60
    lateinit var app: Context
        private set
    private lateinit var prefs: SharedPreferences
    private val main = Handler(Looper.getMainLooper())
    private val listeners = CopyOnWriteArraySet<() -> Unit>()
    private val progressListeners = CopyOnWriteArraySet<(FileEntry) -> Unit>()
    private val pendingFiles = ArrayList<Outgoing>()
    private val outgoing = ConcurrentHashMap<String, Outgoing>()
    private val incoming = ConcurrentHashMap<String, InTransfer>()
    private val lastProgressPost = ConcurrentHashMap<String, Long>()

    private class InTransfer(val inc: Incoming, val entry: FileEntry)

    val conn = Connection(this)

    @Volatile private var cachedPairing: Pairing? = null
    @Volatile var status = Connection.Status.NOT_PAIRED
        private set
    var pcName = ""
        private set
    val files = ArrayList<FileEntry>()
    var visibleScreens = 0

    val isPaired: Boolean get() = cachedPairing != null
    val isConnected: Boolean get() = status == Connection.Status.CONNECTED
    var keepAlive: Boolean
        get() = prefs.getBoolean("keepAlive", true)
        set(v) { prefs.edit().putBoolean("keepAlive", v).apply() }

    /** True when Android lets us open the camera even while DeskLink isn't on screen. */
    val canOpenCameraFromBackground: Boolean get() = Settings.canDrawOverlays(app)

    @Synchronized
    fun init(context: Context) {
        if (::app.isInitialized) return
        app = context.applicationContext
        prefs = app.getSharedPreferences("desklink", Context.MODE_PRIVATE)
        cachedPairing = Pairing.fromStored(
            prefs.getString("key", null), prefs.getInt("port", Proto.DEFAULT_PORT),
            prefs.getString("hosts", null), prefs.getString("pcName", null),
        )
        pcName = cachedPairing?.pcName ?: ""
        try {
            val arr = JSONArray(prefs.getString("files", "[]"))
            for (i in 0 until arr.length()) files.add(FileEntry.from(arr.getJSONObject(i)))
        } catch (e: Exception) {
            files.clear()
        }
        status = if (isPaired) Connection.Status.CONNECTING else Connection.Status.NOT_PAIRED
        Notifications.createChannels(app)
    }

    // ---------------- listeners (UI) ----------------
    fun addListener(l: () -> Unit) { listeners.add(l) }
    fun removeListener(l: () -> Unit) { listeners.remove(l) }
    fun addProgressListener(l: (FileEntry) -> Unit) { progressListeners.add(l) }
    fun removeProgressListener(l: (FileEntry) -> Unit) { progressListeners.remove(l) }

    private fun changed() {
        listeners.forEach { it() }
    }

    private fun toast(msg: String) = main.post { Toast.makeText(app, msg, Toast.LENGTH_SHORT).show() }

    // ---------------- Connection.Listener (called on the connection thread) ----------------
    override fun pairing(): Pairing? = cachedPairing

    override fun deviceName(): String {
        val name = try { Settings.Global.getString(app.contentResolver, Settings.Global.DEVICE_NAME) } catch (e: Exception) { null }
        return (name?.takeIf { it.isNotBlank() } ?: "${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} ${Build.MODEL}").take(40)
    }

    override fun lastGoodAddress(): Pair<String, Int>? {
        val host = prefs.getString("lastHost", null) ?: return null
        return host to prefs.getInt("lastPort", Proto.DEFAULT_PORT)
    }

    override fun onGoodAddress(host: String, port: Int) {
        prefs.edit().putString("lastHost", host).putInt("lastPort", port).apply()
    }

    override fun onStatus(status: Connection.Status) {
        if (status != Connection.Status.CONNECTED) failIncoming()
        main.post {
            if (this.status == status) return@post
            this.status = status
            Notifications.updateStatus(app)
            changed()
        }
    }

    override fun onMessage(json: String) {
        if (json.startsWith("{\"t\":\"file_")) {
            try {
                handleTransfer(JSONObject(json))  // stays on this thread so chunks and offers keep their order
            } catch (e: Exception) {
                Log.w(TAG, "transfer message failed", e)
            }
            return
        }
        main.post {
            try {
                handle(JSONObject(json))
            } catch (e: Exception) {
                Log.w(TAG, "bad message", e)
            }
        }
    }

    override fun onBinary(frame: ByteArray) {
        if (frame.size < 1 + Proto.ID_LEN) return
        val id = String(frame, 1, Proto.ID_LEN, Charsets.US_ASCII)
        val t = incoming[id] ?: return
        try {
            t.inc.write(frame, 1 + Proto.ID_LEN, frame.size - 1 - Proto.ID_LEN)
        } catch (e: Exception) {
            incoming.remove(id)
            t.inc.abort()
            conn.send(JSONObject().put("t", "file_cancel").put("id", id).toString())
            finishEntry(t.entry, "failed")
            return
        }
        progressTick(t.entry, t.inc.received)
    }

    override fun log(msg: String) { Log.d(TAG, msg) }

    private fun handle(o: JSONObject) {
        when (o.optString("t")) {
            "state" -> {
                pcName = o.optString("pc_name", pcName)
                prefs.edit().putString("pcName", pcName).apply()
                flushPending()
                Notifications.updateStatus(app)
            }
            "camera" -> onCameraRequest()
            else -> return  // pong etc.
        }
        changed()
    }

    // ---------------- camera: the PC asked for a photo ----------------
    private fun onCameraRequest() {
        var opened = false
        if (visibleScreens > 0 || canOpenCameraFromBackground) {
            opened = try {
                app.startActivity(CaptureActivity.intent(app))
                true
            } catch (e: Exception) {
                false
            }
        }
        if (!opened) Notifications.showCameraRequest(app, pcName)
        cameraAck(if (opened) "opened" else "notify")
    }

    fun cameraAck(state: String) {
        conn.send(JSONObject().put("t", "camera_ack").put("state", state).toString())
    }

    /** Send the photo that was just taken to the PC's Text Capture mode. */
    fun sendCapture(file: File): SendResult {
        if (!isPaired) return SendResult.NOT_PAIRED
        val size = file.length()
        if (size <= 0) return SendResult.EMPTY
        val stream = try {
            FileInputStream(file)
        } catch (e: Exception) {
            return SendResult.EMPTY
        }
        val id = Proto.newId()
        val offer = JSONObject().put("t", "file_offer").put("id", id).put("kind", "capture")
            .put("name", "capture.jpg").put("mime", "image/jpeg").put("size", size).toString()
        val out = Outgoing(id, offer, stream, size)
        out.onFinished = { _, ok ->
            outgoing.remove(id)
            if (!ok) toast("Couldn't send the photo to the PC")
            trimCaptures(file)
        }
        outgoing[id] = out
        if (conn.sendFile(out)) return SendResult.SENT
        pendingFiles.add(out)
        SyncService.start(app)
        conn.kick()
        return SendResult.QUEUED
    }

    private fun trimCaptures(justSent: File) {
        val old = Media.captureDir(app).listFiles()?.sortedByDescending { it.lastModified() } ?: return
        old.drop(3).forEach { if (it != justSent) it.delete() }
    }

    private fun flushPending() {
        val waiting = ArrayList(pendingFiles)
        pendingFiles.clear()
        for (f in waiting) if (!f.cancelled && !conn.sendFile(f)) pendingFiles.add(f)
    }

    // ---------------- files from the PC (connection thread) ----------------
    private fun handleTransfer(o: JSONObject) {
        val id = o.optString("id")
        when (o.optString("t")) {
            "file_offer" -> {
                if (id.length != Proto.ID_LEN || incoming.containsKey(id)) return
                val size = o.optLong("size", -1)
                val dir = Media.receivedDir(app)
                if (size > Proto.MAX_FILE || (size > 0 && dir.usableSpace < size + 50L * 1024 * 1024)) {
                    conn.send(JSONObject().put("t", "file_cancel").put("id", id).put("reason", "no space").toString())
                    toast("Not enough space on the phone for ${o.optString("name")}")
                    return
                }
                val name = Media.safeName(o.optString("name"))
                val file = File(dir, "${id}_$name")
                val entry = FileEntry(id, name, o.optString("mime").ifEmpty { Media.mimeFor(name) }, size,
                    pcName, System.currentTimeMillis(), "receiving", 0, file.path, false)
                val inc = try {
                    Incoming(id, file, size, o)
                } catch (e: Exception) {     // couldn't create the file: tell the PC instead of leaving it waiting
                    conn.send(JSONObject().put("t", "file_cancel").put("id", id).put("reason", "storage").toString())
                    toast("Couldn't save ${name} on the phone")
                    return
                }
                incoming[id] = InTransfer(inc, entry)
                main.post {
                    files.add(0, entry)
                    trimFiles()
                    changed()
                    Notifications.progress(app, entry)
                }
            }
            "file_done" -> {
                val t = incoming.remove(id) ?: return
                val ok = t.inc.finish(o.optString("sha256"))
                conn.send(JSONObject().put("t", "file_ack").put("id", id).put("ok", ok).toString())
                main.post { onFileReceived(t.entry, ok) }
            }
            "file_cancel" -> {
                incoming.remove(id)?.let { t -> t.inc.abort(); finishEntry(t.entry, "cancelled") }
                outgoing[id]?.cancelled = true
            }
        }
    }

    private fun failIncoming() {
        for ((id, t) in incoming) {
            incoming.remove(id)
            t.inc.abort()
            finishEntry(t.entry, "failed")
        }
    }

    private fun progressTick(entry: FileEntry, bytes: Long) {
        entry.progress = bytes
        val now = System.currentTimeMillis()
        if (now - (lastProgressPost[entry.id] ?: 0L) < 250) return
        lastProgressPost[entry.id] = now
        main.post {
            progressListeners.forEach { it(entry) }
            Notifications.progress(app, entry)
        }
    }

    private fun finishEntry(entry: FileEntry, status: String) {
        main.post {
            if (entry.active) {
                entry.status = status
                Notifications.cancelProgress(app, entry)
                saveFiles()
                changed()
            }
        }
    }

    private fun onFileReceived(entry: FileEntry, ok: Boolean) {
        Notifications.cancelProgress(app, entry)
        entry.status = if (ok) "done" else "failed"
        if (ok) {
            entry.progress = entry.file?.length() ?: entry.progress
            Notifications.showFile(app, entry)
        } else {
            toast("${entry.name} arrived damaged — send it again from the PC")
        }
        saveFiles()
        changed()
    }

    private fun trimFiles() {
        while (files.size > FILES_LIMIT) {
            val old = files.removeAt(files.size - 1)
            if (!old.active) old.file?.delete()
        }
        saveFiles()
    }

    private fun saveFiles() {
        val arr = JSONArray()
        files.take(FILES_LIMIT).forEach { arr.put(it.toJson()) }
        prefs.edit().putString("files", arr.toString()).apply()
    }

    fun cancelTransfer(entry: FileEntry) {
        if (!entry.active) return
        incoming.remove(entry.id)?.inc?.abort()
        conn.send(JSONObject().put("t", "file_cancel").put("id", entry.id).toString())
        entry.status = "cancelled"
        Notifications.cancelProgress(app, entry)
        saveFiles()
        changed()
    }

    fun deleteFile(entry: FileEntry) {
        cancelTransfer(entry)
        entry.file?.delete()
        files.remove(entry)
        Notifications.cancelFile(app, entry)
        saveFiles()
        changed()
    }

    /** Copies into the gallery/Downloads on a background thread; [done] runs on the main thread. */
    fun saveToDownloads(entry: FileEntry, done: (Boolean) -> Unit = {}) {
        val f = entry.file ?: return done(false)
        Thread {
            val ok = Media.saveToGallery(app, f, entry.name, entry.mime)
            main.post {
                if (ok) {
                    entry.saved = true
                    saveFiles()
                    changed()
                }
                done(ok)
            }
        }.start()
    }

    fun findFile(id: String): FileEntry? = files.firstOrNull { it.id == id }

    // ---------------- pairing ----------------
    fun pair(p: Pairing) {
        prefs.edit()
            .putString("key", p.keyText()).putInt("port", p.port)
            .putString("hosts", p.hosts.joinToString(",")).putString("pcName", p.pcName)
            .remove("lastHost").remove("lastPort")
            .apply()
        cachedPairing = p
        pcName = p.pcName
        status = Connection.Status.CONNECTING
        SyncService.start(app)
        conn.reconnect()
        changed()
    }

    fun unpair() {
        prefs.edit().remove("key").remove("hosts").remove("pcName").remove("lastHost").remove("lastPort").apply()
        cachedPairing = null
        pcName = ""
        status = Connection.Status.NOT_PAIRED
        conn.reconnect()
        SyncService.stop(app)
        changed()
    }

    fun statusText(): String = when (status) {
        Connection.Status.NOT_PAIRED -> "Not paired yet"
        Connection.Status.CONNECTING -> "Connecting to ${pcName.ifEmpty { "your PC" }}…"
        Connection.Status.SEARCHING -> "Looking for ${pcName.ifEmpty { "your PC" }} on this network…"
        Connection.Status.CONNECTED -> "Connected to $pcName"
        Connection.Status.OFFLINE -> "${pcName.ifEmpty { "PC" }} is offline — retrying"
        Connection.Status.REJECTED -> "$pcName didn't accept this phone — pair again"
    }
}

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Hub.init(this)
    }
}
