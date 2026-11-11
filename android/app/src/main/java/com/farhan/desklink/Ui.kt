package com.farhan.desklink

import android.content.Context
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Build
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView

/** Colors follow the system (Material You accent on Android 12+, light/dark everywhere). */
class Palette(private val ctx: Context) {
    val dark = (ctx.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
    private val dynamic = Build.VERSION.SDK_INT >= 31
    private fun sys(id: Int, fallback: Long): Int = if (dynamic) ctx.getColor(id) else fallback.toInt()

    val bg = if (dark) sys(android.R.color.system_neutral1_900, 0xFF0F1116) else sys(android.R.color.system_neutral1_50, 0xFFF4F5F8)
    val card = if (dark) sys(android.R.color.system_neutral1_800, 0xFF181B22) else Color.WHITE
    val border = if (dark) 0x22FFFFFF else 0x14000000
    val text = if (dark) 0xFFE7E9EE.toInt() else 0xFF14171F.toInt()
    val muted = if (dark) 0xFF9AA2B2.toInt() else 0xFF687082.toInt()
    val accent = if (dark) sys(android.R.color.system_accent1_200, 0xFFA5B4FC) else sys(android.R.color.system_accent1_600, 0xFF4F46E5)
    val onAccent = if (dark) sys(android.R.color.system_accent1_900, 0xFF1E1B4B) else Color.WHITE
    val tonal = if (dark) sys(android.R.color.system_accent1_800, 0xFF262A4A) else sys(android.R.color.system_accent1_100, 0xFFEEF0FF)
    val onTonal = if (dark) sys(android.R.color.system_accent1_100, 0xFFC7D2FE) else sys(android.R.color.system_accent1_800, 0xFF3730A3)
    val pcBg = if (dark) 0xFF262A4A.toInt() else 0xFFEEF0FF.toInt()
    val pcFg = if (dark) 0xFFA5A8FF.toInt() else 0xFF4F46E5.toInt()
    val phoneBg = if (dark) 0xFF173328.toInt() else 0xFFE3F6EE.toInt()
    val phoneFg = if (dark) 0xFF6EE7B7.toInt() else 0xFF047857.toInt()
    val ok = 0xFF10B981.toInt()
    val off = 0xFF9AA1AE.toInt()
    val warn = if (dark) 0xFFFBBF24.toInt() else 0xFFD97706.toInt()
    val warnBg = if (dark) 0xFF3A2E12.toInt() else 0xFFFFF4E0.toInt()
}

class Ui(private val ctx: Context, val p: Palette) {
    fun dp(v: Int): Int = (v * ctx.resources.displayMetrics.density + 0.5f).toInt()
    private fun dpf(v: Float): Float = v * ctx.resources.displayMetrics.density

    fun rounded(color: Int, radiusDp: Float, stroke: Int? = null): GradientDrawable = GradientDrawable().apply {
        setColor(color)
        cornerRadius = dpf(radiusDp)
        if (stroke != null) setStroke(dp(1), stroke)
    }

    private fun ripple(content: GradientDrawable?, radiusDp: Float): RippleDrawable {
        val mask = rounded(Color.WHITE, radiusDp)
        val rippleColor = if (p.dark) 0x33FFFFFF else 0x22000000
        return RippleDrawable(ColorStateList.valueOf(rippleColor), content, mask)
    }

    fun vertical(paddingDp: Int = 0): LinearLayout = LinearLayout(ctx).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(paddingDp), dp(paddingDp), dp(paddingDp), dp(paddingDp))
    }

    fun horizontal(): LinearLayout = LinearLayout(ctx).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
    }

    fun card(): LinearLayout = vertical(16).apply {
        background = rounded(p.card, 20f, p.border)
        layoutParams = lp(bottom = 12)
    }

    fun lp(w: Int = ViewGroup.LayoutParams.MATCH_PARENT, h: Int = ViewGroup.LayoutParams.WRAP_CONTENT,
           weight: Float = 0f, top: Int = 0, bottom: Int = 0, start: Int = 0, end: Int = 0): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(w, h, weight).apply { setMargins(dp(start), dp(top), dp(end), dp(bottom)) }

    fun text(s: CharSequence, sizeSp: Float = 15f, color: Int = p.text, bold: Boolean = false, maxLines: Int = 0): TextView =
        TextView(ctx).apply {
            text = s
            setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp)
            setTextColor(color)
            if (bold) typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            if (maxLines > 0) {
                this.maxLines = maxLines
                ellipsize = TextUtils.TruncateAt.END
            }
            setLineSpacing(0f, 1.12f)
        }

    fun section(s: String): TextView = text(s.uppercase(), 12f, p.muted, bold = true).apply {
        letterSpacing = 0.06f
    }

    fun primary(label: String, onClick: () -> Unit): TextView = text(label, 15f, p.onAccent, bold = true).apply {
        gravity = Gravity.CENTER
        setPadding(dp(18), dp(11), dp(18), dp(11))
        background = ripple(rounded(p.accent, 14f), 14f)
        isClickable = true
        setOnClickListener { onClick() }
    }

    fun tonal(label: String, onClick: () -> Unit): TextView = text(label, 15f, p.onTonal, bold = true).apply {
        gravity = Gravity.CENTER
        setPadding(dp(18), dp(11), dp(18), dp(11))
        background = ripple(rounded(p.tonal, 14f), 14f)
        isClickable = true
        setOnClickListener { onClick() }
    }

    fun ghost(label: String, onClick: () -> Unit): TextView = text(label, 14f, p.accent, bold = true).apply {
        gravity = Gravity.CENTER
        setPadding(dp(12), dp(8), dp(12), dp(8))
        background = ripple(null, 10f)
        isClickable = true
        setOnClickListener { onClick() }
    }

    fun badge(isPc: Boolean): TextView = text(if (isPc) "PC" else "PHONE", 10.5f, if (isPc) p.pcFg else p.phoneFg, bold = true).apply {
        gravity = Gravity.CENTER
        minWidth = dp(54)
        setPadding(dp(6), dp(3), dp(6), dp(3))
        background = rounded(if (isPc) p.pcBg else p.phoneBg, 7f)
    }

    fun dot(color: Int): View = View(ctx).apply {
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(color) }
        layoutParams = LinearLayout.LayoutParams(dp(9), dp(9)).apply { marginEnd = dp(8) }
    }

    fun switchRow(title: String, subtitle: String, checked: Boolean, onChange: (Boolean) -> Unit): LinearLayout =
        horizontal().apply {
            setPadding(0, dp(8), 0, dp(8))
            val col = vertical()
            col.addView(text(title, 15f))
            col.addView(text(subtitle, 13f, p.muted))
            addView(col, lp(0, weight = 1f, end = 12))
            val sw = Switch(ctx).apply {
                isChecked = checked
                thumbTintList = ColorStateList(
                    arrayOf(intArrayOf(android.R.attr.state_checked), intArrayOf()),
                    intArrayOf(p.accent, if (p.dark) 0xFFB0B6C2.toInt() else Color.WHITE),
                )
                trackTintList = ColorStateList(
                    arrayOf(intArrayOf(android.R.attr.state_checked), intArrayOf()),
                    intArrayOf(p.accent and 0x00FFFFFF or 0x66000000, if (p.dark) 0x55FFFFFF else 0x33000000),
                )
                setOnCheckedChangeListener { _, v -> onChange(v) }
            }
            addView(sw)
            setOnClickListener { sw.toggle() }
        }

    fun divider(): View = View(ctx).apply {
        setBackgroundColor(p.border)
        layoutParams = lp(h = dp(1), top = 4, bottom = 4)
    }
}
