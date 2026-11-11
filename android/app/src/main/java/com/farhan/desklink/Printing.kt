package com.farhan.desklink

import android.app.Activity
import android.content.Context
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.pdf.PdfRenderer
import android.os.Bundle
import android.os.CancellationSignal
import android.os.ParcelFileDescriptor
import android.print.PageRange
import android.print.PrintAttributes
import android.print.PrintDocumentAdapter
import android.print.PrintDocumentInfo
import android.print.PrintManager
import android.print.pdf.PrintedPdfDocument
import android.widget.Toast
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream

/** Sends a received PDF or picture to Android's own print screen (which lists the user's printers). */
object Printing {
    fun canPrint(mime: String): Boolean = mime == "application/pdf" || isPrintableImage(mime)

    private fun isPrintableImage(mime: String): Boolean =
        mime == "image/jpeg" || mime == "image/png" || mime == "image/webp" || mime == "image/bmp" || mime == "image/gif"

    /** Must be called with an Activity: Android only prints from a screen that is showing. */
    fun print(activity: Activity, entry: FileEntry) {
        val file = entry.file
        if (file == null) {
            Toast.makeText(activity, "That file is gone", Toast.LENGTH_SHORT).show()
            return
        }
        val adapter = when {
            entry.mime == "application/pdf" -> PdfFileAdapter(file, entry.name)
            isPrintableImage(entry.mime) -> ImageAdapter(activity.applicationContext, file, entry.name)
            else -> {
                Toast.makeText(activity, "Open this file in its own app and print from there", Toast.LENGTH_LONG).show()
                return
            }
        }
        try {
            val pm = activity.getSystemService(Context.PRINT_SERVICE) as PrintManager
            pm.print(entry.name, adapter, null)
        } catch (e: Exception) {
            Toast.makeText(activity, "Couldn't open the print screen", Toast.LENGTH_SHORT).show()
        }
    }
}

/** A PDF is already what the printer wants: hand the file over unchanged. */
private class PdfFileAdapter(private val file: File, private val name: String) : PrintDocumentAdapter() {
    override fun onLayout(oldAttributes: PrintAttributes?, newAttributes: PrintAttributes?,
                          cancellationSignal: CancellationSignal?, callback: LayoutResultCallback?, extras: Bundle?) {
        if (callback == null) return
        if (cancellationSignal?.isCanceled == true) {
            callback.onLayoutCancelled()
            return
        }
        val pages = try {
            ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY).use { fd ->
                PdfRenderer(fd).use { it.pageCount }
            }
        } catch (e: Exception) {
            PrintDocumentInfo.PAGE_COUNT_UNKNOWN   // password-protected or unusual PDF: let the printer count
        }
        val info = PrintDocumentInfo.Builder(name)
            .setContentType(PrintDocumentInfo.CONTENT_TYPE_DOCUMENT)
            .setPageCount(pages)
            .build()
        callback.onLayoutFinished(info, oldAttributes != newAttributes)
    }

    override fun onWrite(pages: Array<out PageRange>?, destination: ParcelFileDescriptor?,
                         cancellationSignal: CancellationSignal?, callback: WriteResultCallback?) {
        if (callback == null || destination == null) return
        try {
            FileInputStream(file).use { input ->
                FileOutputStream(destination.fileDescriptor).use { output -> input.copyTo(output, 64 * 1024) }
            }
            if (cancellationSignal?.isCanceled == true) callback.onWriteCancelled()
            else callback.onWriteFinished(arrayOf(PageRange.ALL_PAGES))
        } catch (e: Exception) {
            callback.onWriteFailed(e.message)
        }
    }
}

/** A picture is drawn onto one page, as large as fits, keeping its shape. */
private class ImageAdapter(private val ctx: Context, private val file: File, private val name: String) : PrintDocumentAdapter() {
    private var attributes: PrintAttributes? = null

    override fun onLayout(oldAttributes: PrintAttributes?, newAttributes: PrintAttributes?,
                          cancellationSignal: CancellationSignal?, callback: LayoutResultCallback?, extras: Bundle?) {
        if (callback == null) return
        if (cancellationSignal?.isCanceled == true) {
            callback.onLayoutCancelled()
            return
        }
        attributes = newAttributes
        val info = PrintDocumentInfo.Builder(name)
            .setContentType(PrintDocumentInfo.CONTENT_TYPE_PHOTO)
            .setPageCount(1)
            .build()
        callback.onLayoutFinished(info, oldAttributes != newAttributes)
    }

    override fun onWrite(pages: Array<out PageRange>?, destination: ParcelFileDescriptor?,
                         cancellationSignal: CancellationSignal?, callback: WriteResultCallback?) {
        if (callback == null || destination == null) return
        val attrs = attributes
        if (attrs == null) {
            callback.onWriteFailed("No page size yet")
            return
        }
        val doc = PrintedPdfDocument(ctx, attrs)
        try {
            val bmp = Media.decodeSampled(file.path, 2400)
            if (bmp == null) {
                callback.onWriteFailed("Couldn't read the picture")
                return
            }
            val page = doc.startPage(0)
            val area = RectF(page.info.contentRect)
            val matrix = Matrix()
            matrix.setRectToRect(RectF(0f, 0f, bmp.width.toFloat(), bmp.height.toFloat()), area, Matrix.ScaleToFit.CENTER)
            page.canvas.drawBitmap(bmp, matrix, Paint(Paint.FILTER_BITMAP_FLAG))
            doc.finishPage(page)
            FileOutputStream(destination.fileDescriptor).use { doc.writeTo(it) }
            if (cancellationSignal?.isCanceled == true) callback.onWriteCancelled()
            else callback.onWriteFinished(arrayOf(PageRange.ALL_PAGES))
        } catch (e: Exception) {
            callback.onWriteFailed(e.message)
        } finally {
            doc.close()
        }
    }
}
