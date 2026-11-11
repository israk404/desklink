import com.farhan.desklink.*
import java.io.ByteArrayInputStream
import java.io.File
import java.net.InetAddress
import java.security.MessageDigest
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class TestListener(private val p: Pairing?) : Connection.Listener {
    val incoming = java.util.concurrent.ConcurrentHashMap<String, Incoming>()
    val finished = LinkedBlockingQueue<Pair<String, Boolean>>()
    val statuses = LinkedBlockingQueue<Connection.Status>()
    val messages = LinkedBlockingQueue<String>()
    var conn: Connection? = null
    override fun onBinary(frame: ByteArray) {
        val id = String(frame, 1, 12, Charsets.US_ASCII)
        incoming[id]?.write(frame, 13, frame.size - 13)
    }
    override fun pairing() = p
    override fun deviceName() = "Redmi \"Note\" 13"
    override fun lastGoodAddress(): Pair<String, Int>? = null
    override fun onGoodAddress(host: String, port: Int) {}
    override fun onStatus(status: Connection.Status) { statuses.add(status) }
    override fun onMessage(json: String) {
        val id = Regex("\"id\":\"([0-9a-f]{12})\"").find(json)?.groupValues?.get(1)
        if (json.startsWith("{\"t\":\"file_offer\"") && id != null) {
            incoming[id] = Incoming(id, File.createTempFile("incoming", ".bin"), -1, null)
        } else if (json.startsWith("{\"t\":\"file_done\"") && id != null) {
            val sha = Regex("\"sha256\":\"([0-9a-f]+)\"").find(json)!!.groupValues[1]
            val ok = incoming.remove(id)!!.finish(sha)
            finished.add(sha to ok)
            conn?.send("{\"t\":\"file_ack\",\"id\":\"$id\",\"ok\":$ok}")
        }
        messages.add(json)
    }
    fun waitStatus(s: Connection.Status, sec: Long = 15): Boolean {
        val end = System.currentTimeMillis() + sec * 1000
        while (System.currentTimeMillis() < end) { if (statuses.poll(500, TimeUnit.MILLISECONDS) == s) return true }
        return false
    }
    fun waitMsg(type: String, sec: Long = 10): String? {
        val end = System.currentTimeMillis() + sec * 1000
        while (System.currentTimeMillis() < end) { val m = messages.poll(500, TimeUnit.MILLISECONDS) ?: continue; if (m.contains("\"t\":\"$type\"")) return m }
        return null
    }
}

var failures = 0
fun check(name: String, ok: Boolean, detail: Any? = "") { println((if (ok) "PASS " else "FAIL ") + name + " " + (detail ?: "")); if (!ok) failures++ }

fun main(args: Array<String>) {
    val uri = args[0]; val pcSha = args[1]; val captures = File(args[2])
    val local = listOf(InetAddress.getByName("127.0.0.1"))

    // "Find my PC" pairing with the 6-digit code
    val found = PairClient.discover(3000, local)
    check("pairing discovery finds the PC", found.any { it.port == Proto.DEFAULT_PORT }, found)
    val pc = found.first { it.port == Proto.DEFAULT_PORT }.copy(host = "127.0.0.1")
    val attempt = PairClient.begin(pc, "Tester Allow")
    val answer = attempt.await()
    check("Allow returns the key", answer.contains("\"ok\": true") && answer.contains("\"k\""), answer.take(60))
    check("both sides show the same code", answer.contains("\"test_code\": \"${attempt.code}\""), attempt.code)
    val denied = PairClient.begin(pc, "Tester Deny").await()
    check("Deny is reported", denied.contains("\"ok\": false"), denied)

    val p = Pairing.parse(uri)!!
    check("pairing link parses", p.key.size == 32 && p.port == Proto.DEFAULT_PORT, p.hosts)
    check("a ClipBridge link is not accepted", Pairing.parse(uri.replace("desklink://", "clipbridge://")) == null)

    val l = TestListener(p)
    val c = Connection(l); l.conn = c; c.start()
    check("connects", l.waitStatus(Connection.Status.CONNECTED, 20))
    check("PC sends its name", l.waitMsg("state")?.contains("pc_name") == true)
    check("PC's 'Open phone camera' arrives", l.waitMsg("camera", 8) != null)
    c.send("{\"t\":\"camera_ack\",\"state\":\"opened\"}")

    // the photo goes back to the PC
    val photo = ByteArray(1_500_000).also { java.util.Random(7).nextBytes(it) }
    val id = Proto.newId()
    val offer = "{\"t\":\"file_offer\",\"id\":\"$id\",\"kind\":\"capture\",\"name\":\"capture.jpg\",\"mime\":\"image/jpeg\",\"size\":${photo.size}}"
    val result = LinkedBlockingQueue<Boolean>()
    val out = Outgoing(id, offer, ByteArrayInputStream(photo), photo.size.toLong())
    out.onFinished = { _, ok -> result.add(ok) }
    check("photo is accepted for sending", c.sendFile(out))
    check("photo is fully streamed", result.poll(20, TimeUnit.SECONDS) == true)
    check("PC confirms the photo arrived intact", l.waitMsg("file_ack", 10)?.replace(" ", "")?.contains("\"ok\":true") == true)
    val saved = captures.listFiles()?.firstOrNull { it.name.startsWith("capture-") }
    val sha = { b: ByteArray -> MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) } }
    check("PC saved exactly the bytes the phone sent", saved != null && sha(saved.readBytes()) == sha(photo), saved?.name)

    // a file dropped on the PC arrives on the phone
    val got = l.finished.poll(20, TimeUnit.SECONDS)
    check("PC's file arrives with the right SHA-256", got != null && got.second && got.first == pcSha)
    c.stop()

    // a phone with the wrong key gets nowhere
    val l2 = TestListener(Pairing(ByteArray(32) { 9 }, p.port, listOf("127.0.0.1"), p.pcName))
    val c2 = Connection(l2); c2.start()
    check("wrong key is rejected", l2.waitStatus(Connection.Status.REJECTED, 15))
    c2.stop()

    println(if (failures == 0) "ALL PASSED" else "$failures FAILED")
    System.exit(if (failures == 0) 0 else 1)
}
