# آرشیو همه شاخه‌های من - Consolidated Branches

این شاخه (`archive/consolidated-my-branches-2026-10-23`) برای تجمیع همه شاخه‌هایی که توسط `metiteo88-sudo` در گیت‌هاب ایجاد شده‌اند، ساخته شده است.

## فهرست شاخه‌های شناسایی‌شده در ریپازیتوری `blog`

| شاخه | وضعیت | آخرین کامیت | توضیح |
|------|--------|-------------|-------|
| `master` | فعال (شاخه پیش‌فرض) | `85615f7` | شامل FVA Drive Analyzer hardened |
| `revert-3-arena/f9dcde02-blog` | فعال روی GitHub | `c0da22f` | Revert آنالایزر - حذف ابزار |
| `arena/f9dcde02-blog` | حذف شده (بازیابی شد) | `41370c8` | نسخه قبل از merge PR #3 - محتوای identical به master |
| `arena/a5be7c89-blog` | حذف شده (بازیابی شد) | `f1fc378` | نسخه اولیه آنالایزر (1579 خط) - PR #2 closed |
| `arena/0b885a57-blog` | لوکال (جلسه فعلی) | `85615f7` | شاخه فعلی Arena |

## شاخه‌های دیگر حساب شما (سایر ریپازیتوری‌ها)

- `9router`: `patch-1` (a795111), `master`
- `Web`: `metiteo88-sudo-patch-1` (ad6b6c6), `master`, `gh-pages`
- `gemini-cli`: `arena/1d8cd26f-gemini-cli` (ffb81ef), `main`
- `-_-`: `master`
- `models.dev`: فورک با صدها شاخه - اکثر شاخه‌ها از upstream هستند، توسط شما ایجاد نشده‌اند.

## روش تجمیع

- این شاخه از `master` (85615f7) ساخته شده که کامل‌ترین نسخه است.
- تاریخچه شاخه‌های حذف‌شده به صورت تگ‌های محلی بازیابی شد.
- برای حفظ محتوای متفاوت، تفاوت‌های کلیدی در پوشه `docs/branch-archive/` مستند شده است.
- شاخه `revert-3-arena/f9dcde02-blog` عمداً merge نشده چون محتوای آنالایزر را حذف می‌کند - در صورت نیاز می‌توانید آن را جداگانه merge کنید.

## دستورات برای بازیابی شاخه‌های قدیمی

```bash
git fetch origin revert-3-arena/f9dcde02-blog
git show revert-3-arena/f9dcde02-blog --stat
git checkout -b my-old-branch c0da22f
```

تاریخ ایجاد: 2026-10-23
سازنده: metiteo88-sudo via Arena

## 📸 Snapshots صادقانه (جدید)

پوشه `snapshots/` شامل شواهد واقعی از هر شاخه است:

- `COMPARISON_HONEST_REPORT.md` - گزارش کامل مقایسه با تمام diff ها
- `arena-a5be7c89-blog/tools/fva-drive-analyzer/fva_analyze.py` - نسخه قدیمی 1579 خطی (استخراج واقعی از گیت)
- `master-reference/fva_analyze.py` - نسخه فعلی 1754 خطی
- `revert-3-arena-f9dcde02-blog/` - نشان حذف آنالایزر
- `EVIDENCE.md` - روش استخراج و نحوه اعتبارسنجی
- `*.patch` - patch های واقعی برای اعمال مجدد

همه فایل‌ها با `git show` استخراج شده‌اند - هیچ چیز ساختگی نیست.
