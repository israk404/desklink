package com.farhan.desklink

import android.Manifest
import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.database.Cursor
import android.database.MatrixCursor
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaScannerConnection
import android.media.ThumbnailUtils
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.ParcelFileDescriptor
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.util.Base64
import android.util.LruCache
import android.util.Size
import android.webkit.MimeTypeMap
import java.io.File
import java.io.FileInputStream

/**
 * Serves DeskLink's own files to other apps: open / share / print a received file, and let the camera
 * app write a new photo.   content://com.farhan.desklink.files/received/<name>  and  /capture/<name>
 */
class FilesProvider : ContentProvider() {
    companion object {
        fun authority(ctx: Context) = "${ctx.packageName}.files"

        fun uriFor(ctx: Context, file: File): Uri {
            val root = if (file.parentFile == Media.captureDir(ctx)) "capture" else "received"
            return Uri.Builder().scheme("content").authority(authority(ctx)).appendPath(root).appendPath(file.name).build()
        }
    }

    private fun fileFor(uri: Uri): File? {
        val ctx = context ?: return null
        val segs = uri.pathSegments
        if (segs.size != 2 || segs[1].contains("/") || segs[1].startsWith(".")) return null
        val dir = when (segs[0]) {
            "capture" -> Media.captureDir(ctx)
            "received" -> Media.receivedDir(ctx)
            else -> return null
        }
        val f = File(dir, segs[1])
        return if (f.canonicalPath.startsWith(dir.canonicalPath + File.separator) && f.exists()) f else null
    }

    override fun onCreate(): Boolean = true

    override fun getType(uri: Uri): String = Media.mimeFor(uri.lastPathSegment ?: "")

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?,
                       selectionArgs: Array<out String>?, sortOrder: String?): Cursor? {
        val f = fileFor(uri) ?: return null
        val cols = projection ?: arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)
        val row = cols.map {
            when (it) {
                OpenableColumns.DISPLAY_NAME -> Media.displayName(f)
                OpenableColumns.SIZE -> f.length()
                else -> null
            }
        }
        return MatrixCursor(cols).apply { addRow(row.toTypedArray()) }
    }

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor? {
        val f = fileFor(uri) ?: throw java.io.FileNotFoundException(uri.toString())
        val writable = uri.pathSegments.firstOrNull() == "capture"   // only the camera's output file
        val flags = if (writable && mode.contains('w')) ParcelFileDescriptor.parseMode(mode) else ParcelFileDescriptor.MODE_READ_ONLY
        return ParcelFileDescriptor.open(f, flags)
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0
}

object Media {
    private val thumbs = LruCache<String, Bitmap>(60)

    fun receivedDir(ctx: Context) = File(ctx.filesDir, "received").apply { mkdirs() }
    fun captureDir(ctx: Context) = File(ctx.cacheDir, "capture").apply { mkdirs() }

    /** Received files are stored as "<id>_<original name>"; show the original name. */
    fun displayName(f: File): String = f.name.substringAfter('_', f.name)

    fun mimeFor(name: String): String {
        val ext = name.substringAfterLast('.', "").lowercase()
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: "application/octet-stream"
    }

    fun safeName(name: String?): String =
        (name ?: "").substringAfterLast('/').replace(Regex("[\\\\:*?\"<>|\\x00-\\x1f]"), "_").trim().trimStart('.')
            .take(120).ifEmpty { "file" }

    fun humanSize(n: Long): String = when {
        n < 0 -> "?"
        n < 1024 -> "$n B"
        n < 1024 * 1024 -> String.format("%.1f KB", n / 1024.0)
        n < 1024L * 1024 * 1024 -> String.format("%.1f MB", n / 1048576.0)
        else -> String.format("%.2f GB", n / 1073741824.0)
    }

    fun isLink(text: String?): Boolean {
        val t = text?.trim() ?: return false
        return t.length < 4000 && Regex("^https?://\\S+$").matches(t)
    }

    data class UriInfo(val name: String, val size: Long, val mime: String)

    fun info(ctx: Context, uri: Uri): UriInfo {
        var name: String? = null
        var size = -1L
        try {
            ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
                if (c.moveToFirst()) {
                    val ni = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    val si = c.getColumnIndex(OpenableColumns.SIZE)
                    if (ni >= 0) name = c.getString(ni)
                    if (si >= 0 && !c.isNull(si)) size = c.getLong(si)
                }
            }
        } catch (_: Exception) {}
        if (name == null) name = uri.lastPathSegment?.substringAfterLast('/')
        val n = safeName(name)
        val mime = ctx.contentResolver.getType(uri) ?: mimeFor(n)
        val finalName = if (!n.contains('.')) {
            val ext = MimeTypeMap.getSingleton().getExtensionFromMimeType(mime)
            if (ext != null) "$n.$ext" else n
        } else n
        return UriInfo(finalName, size, mime)
    }

    fun bitmapFromB64(key: String, b64: String?): Bitmap? {
        if (b64.isNullOrEmpty()) return null
        thumbs.get("b:$key")?.let { return it }
        return try {
            val bytes = Base64.decode(b64, Base64.DEFAULT)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.also { thumbs.put("b:$key", it) }
        } catch (e: Exception) { null }
    }

    /** Thumbnail for an image or video file (cached). */
    fun thumbFor(file: File, mime: String, px: Int): Bitmap? {
        val key = "f:${file.path}:$px"
        thumbs.get(key)?.let { return it }
        val bmp = try {
            when {
                mime.startsWith("image/") -> decodeSampled(file.path, px)
                mime.startsWith("video/") ->
                    if (Build.VERSION.SDK_INT >= 29) ThumbnailUtils.createVideoThumbnail(file, Size(px, px), null)
                    else @Suppress("DEPRECATION") ThumbnailUtils.createVideoThumbnail(file.path, MediaStore.Images.Thumbnails.MINI_KIND)
                else -> null
            }
        } catch (e: Exception) { null }
        if (bmp != null) thumbs.put(key, bmp)
        return bmp
    }

    fun thumbForUri(ctx: Context, uri: Uri, px: Int): Bitmap? = try {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        ctx.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        val opts = BitmapFactory.Options().apply { inSampleSize = sample(bounds.outWidth, bounds.outHeight, px) }
        ctx.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, opts) }
    } catch (e: Exception) { null }

    fun decodeSampled(path: String, px: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(path, bounds)
        return BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = sample(bounds.outWidth, bounds.outHeight, px) })
    }

    private fun sample(w: Int, h: Int, px: Int): Int {
        var s = 1
        while (w / (s * 2) >= px && h / (s * 2) >= px) s *= 2
        return s
    }

    fun needsStoragePermission(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 29 &&
            ctx.checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED

    /** Copy a file into the phone's Gallery (photos/videos) or Downloads (anything else). */
    fun saveToGallery(ctx: Context, file: File, name: String, mime: String): Boolean {
        val isImage = mime.startsWith("image/")
        val isVideo = mime.startsWith("video/")
        return try {
            if (Build.VERSION.SDK_INT >= 29) {
                val (collection, dir) = when {
                    isImage -> MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY) to "${Environment.DIRECTORY_PICTURES}/DeskLink"
                    isVideo -> MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY) to "${Environment.DIRECTORY_MOVIES}/DeskLink"
                    else -> MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY) to "${Environment.DIRECTORY_DOWNLOADS}/DeskLink"
                }
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                    put(MediaStore.MediaColumns.MIME_TYPE, mime)
                    put(MediaStore.MediaColumns.RELATIVE_PATH, dir)
                    put(MediaStore.MediaColumns.IS_PENDING, 1)
                }
                val resolver = ctx.contentResolver
                val target = resolver.insert(collection, values) ?: return false
                resolver.openOutputStream(target)?.use { out -> FileInputStream(file).use { it.copyTo(out, 256 * 1024) } }
                values.clear()
                values.put(MediaStore.MediaColumns.IS_PENDING, 0)
                resolver.update(target, values, null, null)
                true
            } else {
                if (needsStoragePermission(ctx)) return false
                val base = Environment.getExternalStoragePublicDirectory(when {
                    isImage -> Environment.DIRECTORY_PICTURES
                    isVideo -> Environment.DIRECTORY_MOVIES
                    else -> Environment.DIRECTORY_DOWNLOADS
                })
                val dir = File(base, "DeskLink").apply { mkdirs() }
                var out = File(dir, name)
                var n = 1
                while (out.exists()) out = File(dir, "${name.substringBeforeLast('.')} (${n++}).${name.substringAfterLast('.', "bin")}")
                file.copyTo(out)
                MediaScannerConnection.scanFile(ctx, arrayOf(out.path), arrayOf(mime), null)
                true
            }
        } catch (e: Exception) {
            false
        }
    }

    fun viewIntent(ctx: Context, file: File, mime: String): Intent {
        val uri = FilesProvider.uriFor(ctx, file)
        return Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    fun shareIntent(ctx: Context, file: File, mime: String): Intent {
        val uri = FilesProvider.uriFor(ctx, file)
        val send = Intent(Intent.ACTION_SEND).setType(mime).putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        send.clipData = android.content.ClipData.newRawUri(Media.displayName(file), uri)
        return Intent.createChooser(send, "Share").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    fun linkIntent(url: String): Intent =
        Intent(Intent.ACTION_VIEW, Uri.parse(url.trim())).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
}
