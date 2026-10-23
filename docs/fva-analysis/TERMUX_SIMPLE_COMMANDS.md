# دستور ترموکس - بر اساس نام فایل‌های واقعی شما

**شاخه:** `arena/0b885a57-blog` = `active`  
**فایل کامل:** `termux-list-zips-2026-10-23.sh` (161 خط)

## سریع‌ترین دستور (کپی کنید در Termux)

### نصب (یک بار):
```bash
pkg update -y && pkg install -y unzip p7zip unrar
```

### تنظیم مسیر درایو (خودتون عوض کنید):
```bash
DRIVE="$HOME/storage/shared/MyDriveCopy"
# یا
DRIVE="/sdcard/MyDrive"
ls "$DRIVE"  # چک کنید وجود داره
```

### دستور کامل - همه ZIP/RAR ها:
```bash
find "$DRIVE" -type f \( -iname "*.zip" -o -iname "*.rar" \) -exec sh -c 'echo "== $1 =="; unzip -l "$1" 2>&1 | head -n 50; echo ""' _ {} \;
```

### فقط 3 فایل حجیم شما:
```bash
# 03.zip - 187MB (بزرگترین)
unzip -l "$DRIVE/03.zip" | head -n 100

# 0.rar - 114MB (دومین)
unrar l "$DRIVE/0.rar" | head -n 100
# اگر unrar ندارید:
7z l "$DRIVE/0.rar" | head -n 100

# FarsiVoiceAI_code.zip - 24MB
unzip -l "$DRIVE/02_FarsiVoiceAI_موتور_صدا/بسته‌های_کد_zip/FarsiVoiceAI_code.zip" | head -n 100
```

### همه FarsiVoiceAI:
```bash
find "$DRIVE/02_FarsiVoiceAI_موتور_صدا" -iname "*.zip" -exec sh -c 'echo "== $1 =="; unzip -l "$1" | head -n 30' _ {} \;
```

### همه کتاب پاسخ به تاریخ:
```bash
find "$DRIVE/01_کتاب_پاسخ_به_تاریخ" -iname "*.zip" -exec sh -c 'echo "== $1 =="; unzip -l "$1" | head -n 30' _ {} \;
```

### ذخیره گزارش برای ارسال به من:
```bash
OUT="$HOME/fva_report"
mkdir -p "$OUT"
find "$DRIVE" -type f \( -iname "*.zip" -o -iname "*.rar" \) -exec sh -c 'unzip -l "$1" > "$OUT/$(basename "$1").txt" 2>&1' _ {} \;
ls -lh "$OUT"
tar -czf /sdcard/fva_report.tar.gz "$OUT"
```

## ویژگی‌ها:
- فقط‌خواندنی (استخراج نمی‌کنه - فقط `unzip -l`)
- فارسی/یونیکد درست نشون میده
- برای RAR از `unrar` یا `7z` استفاده می‌کنه (مثل 0.rar شما)
- حجم‌ها با `du -sh` چک میشه

