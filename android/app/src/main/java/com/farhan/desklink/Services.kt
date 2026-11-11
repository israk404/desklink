package com.farhan.desklink

import android.app.Activity
import android.app.Service
import android.content.ActivityNotFoundException
import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.MediaStore
import android.widget.Toast
import java.io.File

/** Foreground service that keeps the connection to the PC open so files and camera requests arrive instantly. */
class SyncService : Service() {
    companion object {
        @Volatile var running = false
            private set

        fun start(ctx: Context) {
            Hub.init(ctx)
            if (!Hub.isPaired) return
            try {
                ctx.startForegroundService(Intent(ctx, SyncService::class.java))
            } catch (e: Exception) {
                // Android refuses background starts in some states; the next app open retries.
            }
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, SyncService::class.java))
        }
    }

    private val handler = Handler(Looper.getMainLooper())
    private var netCallback: ConnectivityManager.NetworkCallback? = null
    private var lastNetwork: Network? = null
    private val idleCheck = object : Runnable {
        override fun run() {
            if (!Hub.keepAlive && Hub.visibleScreens == 0) stopSelf() else handler.postDelayed(this, 60_000)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Hub.init(this)
        running = true
        if (!goForeground()) return
        val cm = getSystemService(ConnectivityManager::class.java)!!
        netCallback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                val previous = lastNetwork
                lastNetwork = network
                if (previous != null && previous != network) Hub.conn.reconnect() else Hub.conn.kick()
            }
        }.also { cb -> try { cm.registerDefaultNetworkCallback(cb) } catch (e: Exception) { netCallback = null } }
        Hub.conn.start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!Hub.isPaired) {
            stopSelf()
            return START_NOT_STICKY
        }
        goForeground()
        Hub.conn.start()
        Hub.conn.kick()
        handler.removeCallbacks(idleCheck)
        handler.postDelayed(idleCheck, 60_000)
        return START_STICKY
    }

    private fun goForeground(): Boolean = try {
        val n = Notifications.status(this)
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(Notifications.ID_STATUS, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(Notifications.ID_STATUS, n)
        }
        true
    } catch (e: Exception) {
        stopSelf()
        false
    }

    override fun onDestroy() {
        running = false
        handler.removeCallbacks(idleCheck)
        netCallback?.let { try { getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(it) } catch (_: Exception) {} }
        Hub.conn.stop()
        if (Hub.isPaired) Hub.onStatus(Connection.Status.OFFLINE)
        super.onDestroy()
    }
}

/** Reconnects after a reboot or app update. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            Hub.init(ctx)
            if (Hub.isPaired && Hub.keepAlive) SyncService.start(ctx)
        }
    }
}

/**
 * Invisible screen that opens the phone's camera, waits for the photo and sends it to the PC.
 * Started when the PC clicks "Open phone camera", from the notification, or from the app's own button.
 */
class CaptureActivity : Activity() {
    companion object {
        private const val REQ_CAMERA = 31
        private const val STATE_PATH = "path"

        fun intent(ctx: Context): Intent =
            Intent(ctx, CaptureActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
    }

    private var photo: File? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Hub.init(this)
        Notifications.cancelCameraRequest(this)
        val restored = savedInstanceState?.getString(STATE_PATH)
        if (restored != null) {          // Android restarted us while the camera was open
            photo = File(restored)
            return
        }
        val file = File(Media.captureDir(this), "capture_${System.currentTimeMillis()}.jpg")
        try {
            file.createNewFile()
        } catch (e: Exception) {
            done("Couldn't prepare a file for the photo", cancelled = true)
            return
        }
        photo = file
        val uri = FilesProvider.uriFor(this, file)
        val camera = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            .putExtra(MediaStore.EXTRA_OUTPUT, uri)
            .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
        camera.clipData = ClipData.newRawUri("photo", uri)
        try {
            @Suppress("DEPRECATION")
            startActivityForResult(camera, REQ_CAMERA)
        } catch (e: ActivityNotFoundException) {
            done("No camera app found on this phone", cancelled = true)
        } catch (e: Exception) {
            done("Couldn't open the camera", cancelled = true)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        photo?.let { outState.putString(STATE_PATH, it.path) }
    }

    @Deprecated("Activity result API from the framework")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_CAMERA) return
        val file = photo
        if (resultCode != RESULT_OK || file == null || file.length() <= 0) {
            file?.delete()
            done(null, cancelled = true)
            return
        }
        val pc = Hub.pcName.ifEmpty { "PC" }
        when (Hub.sendCapture(file)) {
            SendResult.SENT -> done("Photo sent to $pc ✓", cancelled = false)
            SendResult.QUEUED -> done("$pc isn't connected — the photo goes when it's back", cancelled = false)
            SendResult.NOT_PAIRED -> done("Pair with your PC first", cancelled = false)
            SendResult.EMPTY -> done("The camera didn't save a photo", cancelled = true)
        }
    }

    private fun done(message: String?, cancelled: Boolean) {
        if (cancelled) Hub.cameraAck("cancelled")
        if (message != null) Toast.makeText(applicationContext, message, Toast.LENGTH_SHORT).show()
        finish()
    }
}
