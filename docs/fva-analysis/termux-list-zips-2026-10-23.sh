#!/data/data/com.termux/files/usr/bin/bash
# Termux Android - نمایش محتوای داخل هر ZIP/RAR بدون استخراج (فقط‌خواندنی)
# بر اساس نام فایل‌های واقعی درایو شما (از ساختار متادیتا)
# شاخه: arena/0b885a57-blog = active
# تاریخ: 2026-10-23

set -u

# ===== 1. نصب ابزارهای لازم (یک بار) =====
echo "=== نصب ابزارها ==="
pkg update -y && pkg install -y unzip p7zip unrar termux-tools

# اگر bsdtar بخواید (برای RAR هم کار می‌کنه):
# pkg install -y libarchive

# ===== 2. مسیر درایو شما - این رو عوض کنید =====
# مثال‌ها:
DRIVE="$HOME/storage/shared/MyDriveCopy"  # اگر با Drive for Desktop سینک کردید
# DRIVE="/sdcard/MyDrive"                 # یا مسیر کارت حافظه
# DRIVE="$HOME/storage/downloads"         # اگر دانلود کردید

# اگر پوشه درایوتون جای دیگه است، خط بالا رو ویرایش کنید
if [ ! -d "$DRIVE" ]; then
  echo "⚠️ پوشه درایو پیدا نشد: $DRIVE"
  echo "لطفاً متغیر DRIVE بالا رو به مسیر درست تغییر بدید"
  echo "پوشه‌های موجود در HOME:"
  ls -d $HOME/* 2>/dev/null | head -n 20
  ls -d /sdcard/* 2>/dev/null | head -n 20
  exit 1
fi

OUT="$HOME/fva_termux_report"
mkdir -p "$OUT"

echo "منبع: $DRIVE"
echo "خروجی: $OUT"
echo ""

# ===== 3. دستور اصلی - لیست همه ZIP/RAR ها =====
echo "=== فهرست فایل‌های حجیم زیپ/رار ==="
# بر اساس نام‌های واقعی شما:
# - 03.zip (187MB) - ریشه
# - 0.rar (114MB) - ریشه
# - FarsiVoiceAI_code.zip (24MB)
# - extract-book-standalone.zip (x2)
# - fva_layers.zip, fixes-7bugs.zip, real-tests.zip, FarsiVoiceAI_code.zip, etc.

find "$DRIVE" -type f \( -iname "*.zip" -o -iname "*.rar" -o -iname "*.7z" \) -print0 | while IFS= read -r -d '' f; do
  rel="${f#$DRIVE/}"
  size=$(du -h "$f" | cut -f1)
  echo "📦 $rel ($size)"
done | tee "$OUT/zip_files_list.txt"

echo ""
echo "جزئیات ذخیره شد: $OUT/zip_files_list.txt"
echo ""

# ===== 4. نمایش محتوای داخل هر ZIP (بدون استخراج) =====
echo "=== محتوای داخل هر ZIP ==="

# تابع امن برای هر فایل
list_zip() {
  local file="$1"
  local rel="${file#$DRIVE/}"
  local base=$(basename "$rel" | tr '/' '_')
  local outtxt="$OUT/contents_${base}.txt"

  echo "--- $rel ---" | tee -a "$OUT/all_contents_summary.txt"

  if [[ "$file" == *.zip ]]; then
    # روش 1: unzip -l (سریع‌ترین در Termux)
    if command -v unzip >/dev/null 2>&1; then
      echo "[unzip -l] $rel" >> "$outtxt"
      unzip -l "$file" >> "$outtxt" 2>&1
      echo "   ✅ unzip -l -> $outtxt"
    fi
    # روش 2: 7z (اگر نصب باشه، فارسی بهتر نشون میده)
    if command -v 7z >/dev/null 2>&1; then
      echo "" >> "$outtxt"
      echo "[7z l] $rel" >> "$outtxt"
      7z l "$file" >> "$outtxt" 2>&1
    fi
    # روش 3: zipinfo
    if command -v zipinfo >/dev/null 2>&1; then
      echo "" >> "$outtxt"
      echo "[zipinfo] $rel" >> "$outtxt"
      zipinfo -l "$file" >> "$outtxt" 2>&1
    fi

  elif [[ "$file" == *.rar ]]; then
    # برای 0.rar شما (114MB)
    echo "[RAR] $rel" >> "$outtxt"
    if command -v unrar >/dev/null 2>&1; then
      unrar l "$file" >> "$outtxt" 2>&1
      echo "   ✅ unrar l -> $outtxt"
    elif command -v 7z >/dev/null 2>&1; then
      7z l "$file" >> "$outtxt" 2>&1
      echo "   ✅ 7z l (rar) -> $outtxt"
    elif command -v bsdtar >/dev/null 2>&1; then
      bsdtar -tvf "$file" >> "$outtxt" 2>&1
      echo "   ✅ bsdtar -> $outtxt"
    else
      echo "⚠️ برای RAR ابزار نصب نیست. نصب کنید: pkg install unrar 7zip" >> "$outtxt"
      echo "   ⚠️ ابزار RAR نیست"
    fi

  elif [[ "$file" == *.7z ]]; then
    if command -v 7z >/dev/null 2>&1; then
      7z l "$file" >> "$outtxt" 2>&1
      echo "   ✅ 7z l -> $outtxt"
    fi
  fi

  # خلاصه تعداد فایل داخل آرشیو
  lines=$(grep -c "^-" "$outtxt" 2>/dev/null || echo "?")
  echo "   📄 $rel => $outtxt"
  echo ""
}

# اجرای تابع برای همه
export -f list_zip
export DRIVE OUT
find "$DRIVE" -type f \( -iname "*.zip" -o -iname "*.rar" -o -iname "*.7z" \) -print0 | while IFS= read -r -d '' f; do
  list_zip "$f"
done

echo "تمام محتوا ذخیره شد در: $OUT/"
ls -lh "$OUT/"

# ===== 5. دستور تک‌فایلی سریع (برای کپی‌پیست فوری) =====
echo ""
echo "=== دستورات سریع تک‌فایلی (کپی کنید) ==="
cat << 'QUICK'
# فقط 03.zip (187MB) بزرگترین:
unzip -l "$DRIVE/03.zip" | head -n 100

# فقط 0.rar (114MB) - اگر unrar دارید:
unrar l "$DRIVE/0.rar" | head -n 100
# یا با 7z:
7z l "$DRIVE/0.rar" | head -n 100

# فقط FarsiVoiceAI_code.zip (24MB):
unzip -l "$DRIVE/02_FarsiVoiceAI_موتور_صدا/بسته‌های_کد_zip/FarsiVoiceAI_code.zip" | head -n 100

# همه zip های FarsiVoiceAI:
find "$DRIVE/02_FarsiVoiceAI_موتور_صدا" -iname "*.zip" -exec sh -c 'echo "== $1 =="; unzip -l "$1" | head -n 30' _ {} \;

# همه extract-book:
find "$DRIVE/01_کتاب_پاسخ_به_تاریخ" -iname "*.zip" -exec sh -c 'echo "== $1 =="; unzip -l "$1" | head -n 30' _ {} \;

QUICK

# ===== 6. گزارش حجیم‌ها با حجم واقعی =====
echo ""
echo "=== گزارش حجم‌ها ==="
du -sh "$DRIVE/03.zip" "$DRIVE/0.rar" 2>/dev/null | tee "$OUT/huge_files_size.txt"
find "$DRIVE" -type f -name "*.zip" -o -name "*.rar" | xargs du -sh 2>/dev/null | sort -rh | head -n 20 | tee -a "$OUT/huge_files_size.txt"

echo ""
echo "✅ تمام شد! گزارش‌ها در: $OUT"
echo "برای ارسال به من: tar -czf /sdcard/fva_report.tar.gz $OUT  سپس آپلود کنید"
