package com.farhan.desklink

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.TextUtils
import android.text.format.DateUtils
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject

class MainActivity : Activity() {
    companion object {
        private const val REQ_STORAGE = 13
    }

    private lateinit var ui: Ui
    private lateinit var scroll: ScrollView
    private lateinit var headerBox: LinearLayout
    private lateinit var dynamic: LinearLayout
    private val progressViews = HashMap<String, Pair<ProgressBar, TextView>>()
    private var pendingSave: FileEntry? = null
    private var pendingPrint: String? = null
    private val onHubChanged: () -> Unit = { render() }
    private val onProgress: (FileEntry) -> Unit = { e ->
        progressViews[e.id]?.let { (bar, meta) ->
            bar.progress = permille(e)
            meta.text = fileMeta(e)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Hub.init(this)
        ui = Ui(this, Palette(this))
        scroll = ScrollView(this).apply { setBackgroundColor(ui.p.bg); isFillViewport = true }
        val content = ui.vertical().apply { setPadding(ui.dp(16), ui.dp(12), ui.dp(16), ui.dp(24)) }
        headerBox = ui.vertical()
        dynamic = ui.vertical()
        content.addView(headerBox)
        content.addView(dynamic)
        scroll.addView(content)
        setContentView(scroll)
        styleSystemBars()
        handleIntent(intent)
        askForNotifications()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        Hub.visibleScreens++
        Hub.addListener(onHubChanged)
        Hub.addProgressListener(onProgress)
        SyncService.start(this)
        Hub.conn.kick()
        render()
        // "Print" on a notification lands here: the print screen can only open once we are showing
        pendingPrint?.let { id ->
            pendingPrint = null
            scroll.post { Hub.findFile(id)?.let { printEntry(it) } }
        }
    }

    override fun onPause() {
        Hub.visibleScreens--
        Hub.removeListener(onHubChanged)
        Hub.removeProgressListener(onProgress)
        super.onPause()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_STORAGE && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            pendingSave?.let { saveEntry(it) }
        }
        pendingSave = null
    }

    // ---------------------------------------------------------------- layout
    private fun styleSystemBars() {
        val p = ui.p
        @Suppress("DEPRECATION")
        run {
            window.statusBarColor = p.bg
            window.navigationBarColor = p.bg
        }
        if (Build.VERSION.SDK_INT >= 30) {
            val light = if (p.dark) 0 else WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or
                WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS
            window.insetsController?.setSystemBarsAppearance(light,
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS)
        } else if (!p.dark) {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        }
        // Android 15 draws apps edge-to-edge: keep content clear of the status and navigation bars.
        scroll.setOnApplyWindowInsetsListener { v, insets ->
            if (Build.VERSION.SDK_INT >= 30) {
                val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout() or WindowInsets.Type.ime())
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            } else {
                @Suppress("DEPRECATION")
                v.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
            }
            insets
        }
        scroll.clipToPadding = false
    }

    private fun render() {
        val y = scroll.scrollY
        headerBox.removeAllViews()
        header()
        dynamic.removeAllViews()
        progressViews.clear()
        if (!Hub.isPaired) {
            pairCard()
        } else {
            if (Hub.status == Connection.Status.REJECTED) rejectedCard()
            cameraCard()
            filesSection()
            settingsCard()
        }
        scroll.post { scroll.scrollTo(0, y) }
    }

    private fun header() {
        val p = ui.p
        headerBox.addView(ui.text("DeskLink", 28f, p.text, bold = true), ui.lp(top = 8, bottom = 6))
        val row = ui.horizontal()
        val color = when (Hub.status) {
            Connection.Status.CONNECTED -> p.ok
            Connection.Status.REJECTED -> p.warn
            else -> p.off
        }
        row.addView(ui.dot(color))
        row.addView(ui.text(Hub.statusText(), 14f, p.muted), ui.lp(0, weight = 1f))
        headerBox.addView(row, ui.lp(bottom = 16))
    }

    private fun pairCard() {
        val card = ui.card()
        card.addView(ui.text("Connect to your PC", 20f, bold = true), ui.lp(bottom = 8))
        card.addView(ui.text(
            "1. On your PC, open DeskLink and click “Pair phone”.\n" +
                "2. Make sure this phone is on the same Wi-Fi.\n" +
                "3. Tap “Find my PC” and check the code matches.", 15f, ui.p.muted), ui.lp(bottom = 16))
        card.addView(ui.primary("Find my PC") { findPc() }, ui.lp(bottom = 8))
        card.addView(ui.tonal("Paste pairing link") { pasteLinkDialog() })
        dynamic.addView(card)
    }

    private fun rejectedCard() {
        val card = ui.card().apply { background = ui.rounded(ui.p.warnBg, 20f) }
        card.addView(ui.text("Your PC didn't accept this phone", 16f, bold = true), ui.lp(bottom = 4))
        card.addView(ui.text("The pairing key changed on the PC (someone pressed “New key”). Pair again: " +
            "click “Pair phone” on the PC, then tap below.", 14f, ui.p.muted), ui.lp(bottom = 12))
        card.addView(ui.primary("Find my PC") { findPc() })
        dynamic.addView(card)
    }

    private fun cameraCard() {
        val card = ui.card()
        card.addView(ui.section("Text Capture"), ui.lp(bottom = 8))
        card.addView(ui.text("Take a photo of any text. It opens on your PC, where you click the words to copy them.",
            14f, ui.p.muted), ui.lp(bottom = 12))
        card.addView(ui.primary("Take photo for PC") {
            try { startActivity(CaptureActivity.intent(this)) } catch (_: Exception) {}
        })
        dynamic.addView(card)
    }

    private fun rowBackground(): android.graphics.drawable.Drawable {
        val p = ui.p
        return android.graphics.drawable.RippleDrawable(
            ColorStateList.valueOf(if (p.dark) 0x33FFFFFF else 0x22000000),
            ui.rounded(p.card, 16f, p.border), ui.rounded(android.graphics.Color.WHITE, 16f))
    }

    private fun permille(e: FileEntry): Int = if (e.size > 0) (e.progress * 1000 / e.size).toInt().coerceIn(0, 1000) else 0

    private fun fileMeta(e: FileEntry): String = when (e.status) {
        "receiving" -> {
            val pct = if (e.size > 0) "${permille(e) / 10}%" else Media.humanSize(e.progress)
            "Receiving $pct of ${Media.humanSize(e.size)}"
        }
        "done" -> Media.humanSize(e.size) + (if (e.saved) " · Saved to phone ✓" else "") + " · " + ago(e.ts)
        "cancelled" -> "Cancelled"
        else -> "Failed"
    }

    private fun glyph(mime: String) = when {
        mime.startsWith("image/") -> "🖼"
        mime.startsWith("video/") -> "🎬"
        mime.startsWith("audio/") -> "🎵"
        mime == "application/pdf" -> "📕"
        else -> "📄"
    }

    private fun filesSection() {
        val p = ui.p
        dynamic.addView(ui.section("Files from your PC"), ui.lp(top = 8, bottom = 6, start = 4))
        if (Hub.files.isEmpty()) {
            dynamic.addView(ui.text("On the PC, open DeskLink → Send to Phone and drop files there. " +
                "They show up here, ready to open or print.", 14f, p.muted), ui.lp(start = 4, bottom = 16))
            return
        }
        for (e in Hub.files.take(30)) {
            val ready = e.status == "done" && e.file != null
            val row = ui.horizontal().apply {
                setPadding(ui.dp(12), ui.dp(10), ui.dp(8), ui.dp(10))
                background = rowBackground()
                isClickable = true
                setOnClickListener { if (ready) openEntry(e) else fileMenu(e) }
                setOnLongClickListener { fileMenu(e); true }
            }
            val f = e.file
            val thumb = if (f != null && e.isMedia && e.status == "done") Media.thumbFor(f, e.mime, 160) else null
            if (thumb != null) {
                row.addView(ImageView(this).apply {
                    setImageBitmap(thumb)
                    scaleType = ImageView.ScaleType.CENTER_CROP
                    clipToOutline = true
                    background = ui.rounded(p.bg, 10f)
                }, LinearLayout.LayoutParams(ui.dp(48), ui.dp(48)).apply { marginEnd = ui.dp(12) })
            } else {
                row.addView(ui.text(glyph(e.mime), 26f).apply { gravity = Gravity.CENTER },
                    LinearLayout.LayoutParams(ui.dp(48), ui.dp(48)).apply { marginEnd = ui.dp(12) })
            }
            val col = ui.vertical()
            col.addView(ui.text(e.name, 15f, maxLines = 1).apply { ellipsize = TextUtils.TruncateAt.MIDDLE })
            val meta = ui.text(fileMeta(e), 12.5f, p.muted, maxLines = 1)
            col.addView(meta)
            if (e.active) {
                val bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
                    max = 1000
                    progress = permille(e)
                    isIndeterminate = e.size <= 0
                    progressTintList = ColorStateList.valueOf(p.accent)
                }
                col.addView(bar, ui.lp(top = 4))
                progressViews[e.id] = bar to meta
            }
            row.addView(col, ui.lp(0, weight = 1f))
            when {
                e.active -> row.addView(ui.ghost("Cancel") { Hub.cancelTransfer(e) })
                ready && Printing.canPrint(e.mime) -> {
                    row.addView(ui.tonal("Print") { printEntry(e) }, ui.lp(android.view.ViewGroup.LayoutParams.WRAP_CONTENT))
                    row.addView(ui.ghost("⋯") { fileMenu(e) })
                }
                else -> row.addView(ui.ghost("⋯") { fileMenu(e) })
            }
            dynamic.addView(row, ui.lp(bottom = 8))
        }
        dynamic.addView(ui.text("Tap a file to open it · long-press for more", 12.5f, p.muted),
            ui.lp(start = 4, top = 2, bottom = 16))
    }

    private fun settingsCard() {
        val card = ui.card()
        card.addView(ui.section("Settings"), ui.lp(bottom = 4))
        card.addView(ui.switchRow("Stay connected in background", "Needed to get files and camera requests while this app is closed",
            Hub.keepAlive) { Hub.keepAlive = it; if (it) SyncService.start(this) })
        card.addView(ui.divider())
        if (Hub.canOpenCameraFromBackground) {
            card.addView(ui.text("✓ The camera opens by itself when your PC asks, even if DeskLink is closed.",
                14f, ui.p.muted), ui.lp(top = 8, bottom = 8))
        } else {
            card.addView(ui.text("Right now the camera opens by itself only while DeskLink is on screen. " +
                "To open it any time the PC asks, allow “Display over other apps”.", 14f, ui.p.muted), ui.lp(top = 8, bottom = 8))
            card.addView(ui.tonal("Let the PC open the camera any time") { askOverlayPermission() }, ui.lp(bottom = 8))
        }
        val pm = getSystemService(PowerManager::class.java)!!
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            card.addView(ui.tonal("Allow running in background") { askBatteryExemption() }, ui.lp(bottom = 8))
        }
        val row = ui.horizontal()
        row.addView(ui.ghost("Pair again") { findPc() })
        row.addView(View(this), ui.lp(0, weight = 1f))
        row.addView(ui.ghost("Unpair") { confirmUnpair() })
        card.addView(row, ui.lp(top = 4))
        dynamic.addView(card)
    }

    // ---------------------------------------------------------------- actions
    private fun ago(ts: Long): String =
        if (ts <= 0) "" else if (System.currentTimeMillis() - ts < 45_000) "just now"
        else DateUtils.getRelativeTimeSpanString(ts, System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS).toString()

    private fun openEntry(e: FileEntry) {
        val f = e.file ?: return Toast.makeText(this, "That file is gone", Toast.LENGTH_SHORT).show()
        try {
            startActivity(Media.viewIntent(this, f, e.mime))
        } catch (ex: Exception) {
            Toast.makeText(this, "No app can open this file", Toast.LENGTH_SHORT).show()
        }
    }

    private fun printEntry(e: FileEntry) {
        Notifications.cancelFile(this, e)
        Printing.print(this, e)
    }

    private fun saveEntry(e: FileEntry) {
        if (Media.needsStoragePermission(this)) {
            pendingSave = e
            requestPermissions(arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), REQ_STORAGE)
            return
        }
        val where = if (e.isMedia) "gallery" else "Downloads"
        Toast.makeText(this, "Saving…", Toast.LENGTH_SHORT).show()
        Hub.saveToDownloads(e) { ok ->
            Toast.makeText(this, if (ok) "Saved to $where ✓" else "Couldn't save", Toast.LENGTH_SHORT).show()
        }
    }

    private fun fileMenu(e: FileEntry) {
        val options = ArrayList<Pair<String, () -> Unit>>()
        if (e.active) options.add("Cancel" to { Hub.cancelTransfer(e) })
        if (e.status == "done" && e.file != null) {
            options.add("Open" to { openEntry(e) })
            if (Printing.canPrint(e.mime)) options.add("Print" to { printEntry(e) })
            options.add("Share" to {
                try { startActivity(Media.shareIntent(this, e.file!!, e.mime)) } catch (_: Exception) {}
            })
            if (!e.saved) options.add((if (e.isMedia) "Save to gallery" else "Save to Downloads") to { saveEntry(e) })
        }
        options.add("Delete" to { Hub.deleteFile(e) })
        AlertDialog.Builder(this)
            .setTitle(e.name)
            .setItems(options.map { it.first }.toTypedArray()) { _, which -> options[which].second() }
            .show()
    }

    private fun confirmUnpair() {
        AlertDialog.Builder(this).setTitle("Unpair from ${Hub.pcName}?")
            .setMessage("You'll need to pair again to reconnect.")
            .setPositiveButton("Unpair") { _, _ -> Hub.unpair() }
            .setNegativeButton("Cancel", null).show()
    }

    // ---------------------------------------------------------------- pairing
    private fun handleIntent(intent: Intent?) {
        if (intent == null) return
        if (intent.action == Notifications.ACTION_PRINT) {
            pendingPrint = intent.getStringExtra(Notifications.EXTRA_ID)
            intent.action = null   // don't print again when the screen rotates
            return
        }
        val data = intent.data ?: return
        if (data.scheme == "desklink") applyPairing(data.toString())
    }

    private fun applyPairing(raw: String) {
        val p = Pairing.parse(raw)
        if (p == null) {
            Toast.makeText(this, "That isn't a DeskLink pairing code", Toast.LENGTH_LONG).show()
            return
        }
        Hub.pair(p)
        Toast.makeText(this, "Paired with ${p.pcName} — connecting…", Toast.LENGTH_SHORT).show()
        render()
    }

    // "Find my PC": discover → show code → wait for Allow on the PC.
    @Volatile private var pairing: PairClient.Attempt? = null
    @Volatile private var pairCancelled = false

    private fun findPc() {
        pairCancelled = false
        val dlg = AlertDialog.Builder(this)
            .setTitle("Looking for your PC…")
            .setMessage("On the PC, open DeskLink and click “Pair phone”. Both need to be on the same Wi-Fi.")
            .setNegativeButton("Cancel") { _, _ -> pairCancelled = true }
            .setCancelable(false)
            .show()
        Thread {
            var found: List<PairClient.Found> = emptyList()
            val deadline = System.currentTimeMillis() + 45_000
            while (!pairCancelled && found.isEmpty() && System.currentTimeMillis() < deadline) {
                found = try { PairClient.discover(2_500) } catch (e: Exception) { emptyList() }
            }
            runOnUiThread {
                if (isFinishing || pairCancelled) return@runOnUiThread
                dlg.dismiss()
                when {
                    found.isEmpty() -> AlertDialog.Builder(this).setTitle("No PC found")
                        .setMessage("Check that “Pair phone” is open on the PC and both are on the same Wi-Fi " +
                            "(set to Private on Windows). Guest Wi-Fi networks often block this.")
                        .setPositiveButton("Try again") { _, _ -> findPc() }
                        .setNeutralButton("Paste link") { _, _ -> pasteLinkDialog() }
                        .setNegativeButton("Cancel", null).show()
                    found.size == 1 -> beginPairing(found[0])
                    else -> AlertDialog.Builder(this).setTitle("Which PC?")
                        .setItems(found.map { "${it.name}  (${it.host})" }.toTypedArray()) { _, i -> beginPairing(found[i]) }
                        .setNegativeButton("Cancel", null).show()
                }
            }
        }.start()
    }

    private fun beginPairing(pc: PairClient.Found) {
        pairCancelled = false
        val waiting = AlertDialog.Builder(this).setTitle("Connecting to ${pc.name}…")
            .setNegativeButton("Cancel") { _, _ -> pairCancelled = true; pairing?.cancel() }
            .setCancelable(false).show()
        Thread {
            try {
                val attempt = PairClient.begin(pc, Hub.deviceName())
                pairing = attempt
                runOnUiThread {
                    if (pairCancelled) { attempt.cancel(); return@runOnUiThread }
                    waiting.setTitle(attempt.code.substring(0, 3) + " " + attempt.code.substring(3))
                    waiting.setMessage("Check that ${pc.name} shows this same code, then click Allow on the PC.")
                }
                val answer = JSONObject(attempt.await())
                runOnUiThread {
                    waiting.dismiss()
                    if (!answer.optBoolean("ok")) {
                        message("Not paired", "The PC said no. You can try again any time.")
                        return@runOnUiThread
                    }
                    val hosts = ArrayList<String>().apply {
                        add(pc.host)  // the address that just worked comes first
                        val arr = answer.optJSONArray("h")
                        if (arr != null) for (i in 0 until arr.length()) arr.optString(i).let { if (it.isNotBlank() && it != pc.host) add(it) }
                    }
                    val key = try { java.util.Base64.getUrlDecoder().decode(answer.optString("k")) } catch (e: Exception) { null }
                    if (key == null || key.size != 32) {
                        message("Pairing failed", "The PC sent an invalid key.")
                        return@runOnUiThread
                    }
                    val pairingInfo = Pairing(key, answer.optInt("p", pc.port), hosts, answer.optString("n", pc.name))
                    Hub.pair(pairingInfo)
                    Toast.makeText(this, "Paired with ${pairingInfo.pcName} ✓", Toast.LENGTH_SHORT).show()
                    render()
                }
            } catch (e: Exception) {
                runOnUiThread {
                    waiting.dismiss()
                    if (!pairCancelled && !isFinishing) message("Not paired", e.message ?: "Something went wrong")
                }
            } finally {
                pairing = null
            }
        }.start()
    }

    private fun message(title: String, text: String) {
        if (isFinishing) return
        AlertDialog.Builder(this).setTitle(title).setMessage(text).setPositiveButton("OK", null).show()
    }

    private fun clipboardText(): String? = try {
        val clip = getSystemService(ClipboardManager::class.java)?.primaryClip
        if (clip != null && clip.itemCount > 0) clip.getItemAt(0).text?.toString() else null
    } catch (e: Exception) {
        null
    }

    private fun pasteLinkDialog() {
        val input = EditText(this).apply {
            hint = "desklink://pair?…"
            setSingleLine(false)
            maxLines = 4
            val clip = clipboardText()
            if (clip != null && clip.trim().startsWith("desklink://")) setText(clip.trim())
        }
        val box = ui.vertical(20).apply { addView(input) }
        AlertDialog.Builder(this)
            .setTitle("Paste pairing link")
            .setMessage("On the PC: Pair phone → scan its small QR with Google Lens and copy the text, or use “Copy pairing link” and send it to yourself.")
            .setView(box)
            .setPositiveButton("Pair") { _, _ -> applyPairing(input.text.toString()) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ---------------------------------------------------------------- permissions & setup
    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    private fun askOverlayPermission() {
        try {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        } catch (e: Exception) {
            try { startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION)) } catch (_: Exception) {}
        }
    }

    private fun askBatteryExemption() {
        try {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        } catch (e: Exception) {
            try { startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) } catch (_: Exception) {}
        }
    }
}
