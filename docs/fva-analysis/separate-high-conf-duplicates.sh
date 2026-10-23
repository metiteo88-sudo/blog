#!/data/data/com.termux/files/usr/bin/bash
# جدا کردن تکراری‌های مطمئن (با اطمینان بالا) از نامطمئن
# بر اساس دستور شما: فقط وقتی از محتوایش مطمئنیم حذف کن
# شاخه: arena/0b885a57-blog = active

REPORT="$HOME/fva_report"
MAP="$REPORT/FVA_relevance_map_2026-10-23.csv"
DUP="$REPORT/duplicate_candidates.csv"

if [ ! -f "$MAP" ] || [ ! -f "$DUP" ]; then
  echo "❌ فایل‌ها نیست: $MAP یا $DUP"
  exit 1
fi

OUT="$REPORT/high_conf_duplicates"
mkdir -p "$OUT"

echo "=== جدا کردن - فقط با اطمینان بالا + هش یکسان ==="
echo "کل تکراری‌ها: $(wc -l < "$DUP") ردیف"
echo "کل فایل‌ها: $(wc -l < "$MAP") ردیف"
echo ""

# 1. لیست همه گروه‌های تکراری
echo "گروه‌های تکراری (ستون اول Dxxx):"
cut -d',' -f1 "$DUP" | sort | uniq -c | head -n 20 | tee "$OUT/groups_count.txt"
# تعداد گروه‌ها
groups=$(cut -d',' -f1 "$DUP" | sort -u | wc -l)
echo "تعداد گروه تکراری: $groups" | tee -a "$OUT/groups_count.txt"

# 2. جدا کردن مطمئن vs نامطمئن
# مطمئن = هش SHA256 موجود + اطمینان بالا + بخش الف/ج (نه د) + حساس=خیر
# برای این کار MAP و DUP را به هم وصل می‌کنیم

python3 << 'PY'
import csv, pathlib, collections

REPORT = pathlib.Path.home() / "fva_report"
MAP = REPORT / "FVA_relevance_map_2026-10-23.csv"
DUP = REPORT / "duplicate_candidates.csv"
OUT = REPORT / "high_conf_duplicates"
OUT.mkdir(exist_ok=True)

# خواندن MAP: شناسه -> (بخش, اطمینان, حساس, هش)
id_to_info = {}
with open(MAP, encoding='utf-8-sig', newline='') as f:
    r = csv.DictReader(f)
    for row in r:
        # ستون‌ها: نام, شناسه, مسیر فعلی, نوع, حجم, ..., بخش, اطمینان, دلیل, حساس؟, هش, گروه, ...
        iid = row.get("شناسه","").strip()
        # بخش: الف) مرتبط قطعی, ب) مرتبط احتمالی, ج) نامرتبط, د) شک‌دار
        section = row.get("بخش","")
        conf = row.get("اطمینان","")  # بالا, متوسط, پایین
        sensitive = row.get("حساس؟","")
        h = row.get("هش (SHA-256/MD5)","")
        name = row.get("نام","")
        path = row.get("مسیر فعلی","")
        if iid:
            id_to_info[iid] = (section, conf, sensitive, h, name, path)

# خواندن DUP و گروه‌بندی
# DUP headers: گروه,نوع هش,هش,حجم,نام,مسیر فعلی,شناسه
groups = collections.defaultdict(list)
with open(DUP, encoding='utf-8-sig', newline='') as f:
    r = csv.DictReader(f)
    for row in r:
        g = row.get("گروه","").strip()
        hid = row.get("شناسه","").strip()
        if g and hid:
            groups[g].append(row)

safe_groups = []
unsafe_groups = []

for g, members in groups.items():
    # چک همه اعضای گروه
    all_high = True
    has_sensitive = False
    has_uncertain = False
    for m in members:
        iid = m.get("شناسه","").strip()
        info = id_to_info.get(iid)
        if not info:
            all_high = False
            has_uncertain = True
            continue
        section, conf, sensitive, h, name, path = info
        # قانون سخت‌گیرانه شما:
        # فقط اگر اطمینان == بالا و بخش != د) شک‌دار و حساس=خیر
        if conf != "بالا":
            all_high = False
        if "د) شک‌دار" in section:
            has_uncertain = True
            all_high = False
        if sensitive == "بله":
            has_sensitive = True
            all_high = False

    if all_high and not has_sensitive and not has_uncertain:
        safe_groups.append((g, members))
    else:
        unsafe_groups.append((g, members))

print(f"گروه‌های مطمئن (قابل حذف با اطمینان بالا): {len(safe_groups)}")
print(f"گروه‌های نامطمئن (حذف ممنوع): {len(unsafe_groups)}")

# نوشتن خروجی
with open(OUT / "safe_to_delete.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["گروه","نام","مسیر فعلی","شناسه","حجم","بخش","اطمینان"])
    for g, members in safe_groups:
        for m in members:
            iid = m.get("شناسه","")
            info = id_to_info.get(iid, ("","","","","",""))
            section, conf, sensitive, h, name, path = info
            w.writerow([g, m.get("نام",""), m.get("مسیر فعلی",""), iid, m.get("حجم",""), section, conf])

with open(OUT / "unsafe_keep.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["گروه","نام","مسیر فعلی","شناسه","حجم","بخش","اطمینان","دلیل نامطمئن"])
    for g, members in unsafe_groups:
        for m in members:
            iid = m.get("شناسه","")
            info = id_to_info.get(iid, ("","","","","",""))
            section, conf, sensitive, h, name, path = info
            reason = []
            if conf != "بالا": reason.append(f"اطمینان={conf}")
            if "د) شک‌دار" in section: reason.append("د) شک‌دار")
            if sensitive == "بله": reason.append("حساس")
            w.writerow([g, m.get("نام",""), m.get("مسیر فعلی",""), iid, m.get("حجم",""), section, conf, ";".join(reason)])

# خلاصه
with open(OUT / "summary.txt", "w", encoding="utf-8") as f:
    f.write(f"تعداد کل گروه تکراری: {len(groups)}\n")
    f.write(f"مطمئن (قابل حذف): {len(safe_groups)} گروه\n")
    f.write(f"نامطمئن (حذف ممنوع): {len(unsafe_groups)} گروه\n")
    f.write("\nگروه‌های مطمئن (اولین 10):\n")
    for g, members in safe_groups[:10]:
        f.write(f" - {g}: {len(members)} فایل، حجم {members[0].get('حجم','')}\n")
        for m in members:
            f.write(f"    * {m.get('مسیر فعلی','')}\n")

print("✅ تمام شد:")
print(f" - مطمئن: {OUT}/safe_to_delete.csv")
print(f" - نامطمئن: {OUT}/unsafe_keep.csv")
print(f" - خلاصه: {OUT}/summary.txt")
PY

ls -lh "$OUT/"
echo ""
cat "$OUT/summary.txt"
echo ""
echo "=== نمونه مطمئن (اولین 5 ردیف) ==="
head -n 6 "$OUT/safe_to_delete.csv" 2>&1 | head -n 6
echo ""
echo "=== نمونه نامطمئن (اولین 5 ردیف) ==="
head -n 6 "$OUT/unsafe_keep.csv" 2>&1 | head -n 6
