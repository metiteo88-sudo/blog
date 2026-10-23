# شواهد صادقانه - Evidence

این پوشه شامل شواهد واقعی از محتوای هر شاخه است، نه کپی ساختگی.

## چگونه ساخته شد
هر فایل با دستور `git show <SHA>:<path>` مستقیماً از تاریخچه گیت استخراج شد:

```bash
git show f1fc378:tools/fva-drive-analyzer/fva_analyze.py > snapshot/...
git show c0da22f --stat > revert-stat.txt
git diff f1fc378..85615f7 > arena-a5be7c89-blog-vs-master.patch
```

هیچ فایلی دستی تغییر داده نشده.

## اعتبارسنجی
شما می‌توانید هر ادعا را خودتان چک کنید:

```bash
git log --all --oneline --graph
git diff master..arena/a5be7c89-blog --stat
git show f1fc378:tools/fva-drive-analyzer/fva_analyze.py | wc -l  # باید 1579 باشد
git show master:tools/fva-drive-analyzer/fva_analyze.py | wc -l  # باید 1754 باشد
```

## لیست فایل‌های snapshot
- `arena-a5be7c89-blog/tools/fva-drive-analyzer/fva_analyze.py` - 1579 خط (واقعی از تاریخچه)
- `arena-a5be7c89-blog/tools/fva-drive-analyzer/test_fva_analyze.py` - 245 خط
- `master-reference/fva_analyze.py` - 1754 خط (مرجع فعلی)
- `revert-3-arena-f9dcde02-blog/DELETED_MARKER...` - نشان حذف
- `*.patch` - diff های واقعی قابل اعمال مجدد
- `COMPARISON_HONEST_REPORT.md` - تحلیل صادقانه بالا
