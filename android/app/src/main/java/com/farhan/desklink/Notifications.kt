package com.farhan.desklink

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

object Notifications {
    private const val CH_STATUS = "status"
    private const val CH_INCOMING = "incoming"
    private const val CH_TRANSFERS = "transfers"
    private const val CH_CAMERA = "camera"
    const val ID_STATUS = 1
    private const val ID_CAMERA = 2

    const val ACTION_CANCEL = "com.farhan.desklink.CANCEL"
    const val ACTION_PRINT = "com.farhan.desklink.PRINT"
    const val EXTRA_ID = "id"

    private const val FLAGS = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE

    fun createChannels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)!!
        nm.createNotificationChannel(
            NotificationChannel(CH_INCOMING, "Files from your PC", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Files your PC sends you, with Open and Print buttons"
            }
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_CAMERA, "Camera requests", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Shown when the PC asks for a photo and DeskLink can't open the camera by itself"
            }
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_TRANSFERS, "File transfer progress", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Progress while a file is arriving"
                setShowBadge(false)
            }
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_STATUS, "Connection (keeps DeskLink running)", NotificationManager.IMPORTANCE_MIN).apply {
                description = "Required by Android to stay connected in the background. Safe to minimise."
                setShowBadge(false)
            }
        )
    }

    private fun nm(ctx: Context) = ctx.getSystemService(NotificationManager::class.java)

    private fun post(ctx: Context, id: Int, n: Notification) {
        try { nm(ctx)?.notify(id, n) } catch (e: SecurityException) { /* notifications not allowed */ }
    }

    private fun openApp(ctx: Context): PendingIntent =
        PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP), FLAGS)

    private fun openCamera(ctx: Context): PendingIntent =
        PendingIntent.getActivity(ctx, 3, CaptureActivity.intent(ctx), FLAGS)

    private fun action(label: String, pi: PendingIntent) = Notification.Action.Builder(null, label, pi).build()

    fun status(ctx: Context): Notification {
        val b = Notification.Builder(ctx, CH_STATUS)
            .setSmallIcon(R.drawable.ic_notif)
            .setContentTitle(Hub.statusText())
            .setContentText("Ready for files and camera requests from your PC")
            .setContentIntent(openApp(ctx))
            .setOngoing(true)
            .setShowWhen(false)
            .setCategory(Notification.CATEGORY_SERVICE)
        if (Hub.status == Connection.Status.CONNECTED) b.addAction(action("Take photo for PC", openCamera(ctx)))
        if (Build.VERSION.SDK_INT >= 31) b.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
        return b.build()
    }

    fun updateStatus(ctx: Context) {
        if (SyncService.running) post(ctx, ID_STATUS, status(ctx))
    }

    /** The PC clicked "Open phone camera" but Android won't let us open it from the background. */
    fun showCameraRequest(ctx: Context, pcName: String) {
        val n = Notification.Builder(ctx, CH_CAMERA)
            .setSmallIcon(R.drawable.ic_notif)
            .setContentTitle("${pcName.ifEmpty { "Your PC" }} wants a photo")
            .setContentText("Tap to open the camera")
            .setContentIntent(openCamera(ctx))
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_REMINDER)
            .setTimeoutAfter(120_000)
            .build()
        post(ctx, ID_CAMERA, n)
    }

    fun cancelCameraRequest(ctx: Context) {
        nm(ctx)?.cancel(ID_CAMERA)
    }

    private fun fileNid(entry: FileEntry) = 2000 + (entry.id.hashCode() and 0x3FFF)
    private fun progressNid(entry: FileEntry) = 20000 + (entry.id.hashCode() and 0x3FFF)

    private fun printIntent(ctx: Context, entry: FileEntry, code: Int): PendingIntent =
        PendingIntent.getActivity(ctx, code, Intent(ctx, MainActivity::class.java).setAction(ACTION_PRINT)
            .putExtra(EXTRA_ID, entry.id)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP), FLAGS)

    /** A file that arrived from the PC: Open / Print / Share. */
    fun showFile(ctx: Context, entry: FileEntry) {
        val file = entry.file ?: return
        val nid = fileNid(entry)
        val b = Notification.Builder(ctx, CH_INCOMING)
            .setSmallIcon(R.drawable.ic_notif)
            .setContentTitle("From ${entry.device.ifEmpty { "PC" }}: ${entry.name}")
            .setContentText(Media.humanSize(entry.size))
            .setContentIntent(openApp(ctx))
            .setAutoCancel(true)
        val bmp = if (entry.isMedia) Media.thumbFor(file, entry.mime, 720) else null
        if (bmp != null) b.setLargeIcon(bmp).setStyle(Notification.BigPictureStyle().bigPicture(bmp))
        if (Printing.canPrint(entry.mime)) b.addAction(action("Print", printIntent(ctx, entry, nid + 1)))
        b.addAction(action("Open", PendingIntent.getActivity(ctx, nid + 2, Media.viewIntent(ctx, file, entry.mime), FLAGS)))
        b.addAction(action("Share", PendingIntent.getActivity(ctx, nid + 3, Media.shareIntent(ctx, file, entry.mime), FLAGS)))
        post(ctx, nid, b.build())
    }

    fun cancelFile(ctx: Context, entry: FileEntry) {
        nm(ctx)?.cancel(fileNid(entry))
    }

    fun progress(ctx: Context, entry: FileEntry) {
        if (!entry.active) return
        val permille = if (entry.size > 0) (entry.progress * 1000 / entry.size).toInt().coerceIn(0, 1000) else 0
        val n = Notification.Builder(ctx, CH_TRANSFERS)
            .setSmallIcon(R.drawable.ic_notif)
            .setContentTitle("Receiving ${entry.name}")
            .setContentText("${Media.humanSize(entry.progress)} of ${Media.humanSize(entry.size)}")
            .setProgress(1000, permille, entry.size <= 0)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setContentIntent(openApp(ctx))
            .addAction(action("Cancel", PendingIntent.getBroadcast(ctx, progressNid(entry),
                Intent(ctx, ActionReceiver::class.java).setAction(ACTION_CANCEL).putExtra(EXTRA_ID, entry.id), FLAGS)))
            .build()
        post(ctx, progressNid(entry), n)
    }

    fun cancelProgress(ctx: Context, entry: FileEntry) {
        nm(ctx)?.cancel(progressNid(entry))
    }
}

/** Handles the Cancel button on a transfer notification. */
class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        Hub.init(ctx)
        if (intent.action == Notifications.ACTION_CANCEL) {
            intent.getStringExtra(Notifications.EXTRA_ID)?.let { id -> Hub.findFile(id)?.let { Hub.cancelTransfer(it) } }
        }
    }
}
