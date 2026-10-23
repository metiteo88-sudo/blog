#!/data/data/com.termux/files/usr/bin/bash
# حذف ایمن فقط مطمئن‌ها - با اطمینان بالا + هش یکسان + حساس نیست
# شاخه: arena/0b885a57-blog = active
# قانون شما: فقط وقتی از محتوایش مطمئنیم حذف کن

REPORT="$HOME/fva_report"
SAFE="$REPORT/high_conf_duplicates/safe_to_delete.csv"
BACKUP="$HOME/trash_backup_fva_$(date +%Y%m%d_%H%M%S)"

if [ ! -f "$SAFE" ]; then
  echo "❌ فایل نیست: $SAFE"
  echo "اول: bash docs/fva-analysis/separate-high-conf-duplicates.sh"
  exit 1
fi

mkdir -p "$BACKUP"
echo "=== حذف ایمن - فقط 147 گروه مطمئن (الف/ج با اطمینان بالا) ==="
echo "پشتیبان: $BACKUP"
echo "قانون: هر گروه تکراری → یکی نگه دار، بقیه منتقل به پشتیبان (نه rm)"
echo ""

# آمار
total_groups=$(cut -d',' -f1 "$SAFE" | sort -u | wc -l)
total_files=$(wc -l < "$SAFE")
echo "گروه مطمئن: $total_groups"
echo "فایل تکراری مطمئن: $((total_files-1)) ردیف"  # منهای هدر
echo ""

# برای هر گروه: اولین فایل را نگه دار، بقیه را جابجا کن
python3 << 'PY'
import csv, pathlib, shutil, os
from collections import defaultdict

REPORT = pathlib.Path.home() / "fva_report"
SAFE = REPORT / "high_conf_duplicates" / "safe_to_delete.csv"
BACKUP = pathlib.Path.home() / f"trash_backup_fva_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}"
# پیدا کردن BACKUP واقعی (آخرین ساخته شده)
import glob
cands = sorted(glob.glob(str(pathlib.Path.home() / "trash_backup_fva_*")))
if cands:
    BACKUP = pathlib.Path(cands[-1])
else:
    BACKUP = pathlib.Path.home() / "trash_backup_fva_manual"
BACKUP.mkdir(parents=True, exist_ok=True)

groups = defaultdict(list)
with open(SAFE, encoding='utf-8-sig', newline='') as f:
    r = csv.DictReader(f)
    for row in r:
        g = row.get("گروه","")
        path = row.get("مسیر فعلی","")
        if g and path:
            groups[g].append(row)

kept = 0
moved = 0
log_lines = []

# اولویت نگه داشتن: فایل‌های نه در .trash-storage و نه در SHAREit/.offline (آنها کش هستند)
# اگر همه در کش باشند، اولین را نگه دار
for g, members in groups.items():
    # مرتب: اول فایل‌هایی که مسیرشان کوتاه‌تر و بدون .trash-storage و بدون .offline
    def score(m):
        p = m.get("مسیر فعلی","")
        s = 0
        if ".trash-storage" in p: s += 10
        if "SHAREit/.offline" in p: s += 5
        if ".trash" in p.lower(): s += 10
        s += len(p) / 1000  # کوتاه‌تر بهتر
        return s
    members_sorted = sorted(members, key=score)
    # نگه دار اولی
    keep = members_sorted[0]
    kept += 1
    log_lines.append(f"KEEP {g}: {keep.get('مسیر فعلی','')} (حجم {keep.get('حجم','')} - {keep.get('بخش','')} {keep.get('اطمینان','')})")
    # بقیه را منتقل کن
    for m in members_sorted[1:]:
        src = pathlib.Path("/sdcard") / m.get("مسیر فعلی","") if not m.get("مسیر فعلی","").startswith("/") else pathlib.Path(m.get("مسیر فعلی",""))
        # در fva_report مسیرها نسبی از /sdcard هستند، پس /sdcard/ + مسیر
        # اگر مسیر با / شروع شد مستقیم
        p = m.get("مسیر فعلی","")
        if p.startswith("/"):
            src = pathlib.Path(p)
        else:
            src = pathlib.Path("/sdcard") / p
        # مقصد پشتیبان با حفظ ساختار
        dst = BACKUP / p
        dst.parent.mkdir(parents=True, exist_ok=True)
        log_lines.append(f"MOVE {g}: {src} -> {dst}")
        # اگر فایل وجود دارد منتقل کن، وگرنه فقط لاگ
        if src.exists():
            try:
                shutil.move(str(src), str(dst))
                moved += 1
            except Exception as e:
                log_lines.append(f"  ERROR {e}")
        else:
            log_lines.append(f"  SKIP not found: {src}")
            # سعی با /storage/emulated/0
            alt = pathlib.Path("/storage/emulated/0") / p
            if alt.exists():
                dst2 = BACKUP / p
                dst2.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(alt), str(dst2))
                    moved += 1
                    log_lines.append(f"  MOVED alt {alt} -> {dst2}")
                except Exception as e:
                    log_lines.append(f"  ERROR alt {e}")

# نوشتن لاگ
with open(BACKUP / "delete_log.txt", "w", encoding="utf-8") as f:
    f.write(f"زمان: {__import__('datetime').datetime.now()}\n")
    f.write(f"گروه مطمئن: {len(groups)}\n")
    f.write(f"نگه داشته: {kept}\n")
    f.write(f"منتقل به پشتیبان: {moved}\n")
    f.write("="*60 + "\n")
    for line in log_lines:
        f.write(line + "\n")

print(f"✅ نگه داشته: {kept} فایل (یکی در هر گروه)")
print(f"✅ منتقل به پشتیبان: {moved} فایل")
print(f"📁 پشتیبان: {BACKUP}")
print(f"📄 لاگ: {BACKUP}/delete_log.txt")
print("")
print("برای برگرداندن: cp -r $BACKUP/* /sdcard/")
print("برای حذف دائم پشتیبان (بعد از چک): rm -rf $BACKUP")

PY

# نمایش لاگ
echo ""
echo "=== خلاصه ==="
cat "$BACKUP/delete_log.txt" 2>/dev/null | head -n 40
echo ""
ls -lh "$BACKUP" 2>/dev/null | head -n 20
du -sh "$BACKUP" 2>/dev/null
