# وكيل ذكي لبناء مشاريع بايثون 🤖

يبني بوتات تليجرام وتطبيقات FastAPI تلقائياً مع بث مباشر لكل الأحداث.

## البدء السريع

```bash
# تثبيت المكتبات
pip install -r requirements.txt

# نسخ ملف الإعدادات
cp .env.example .env
# ثم عدّل .env وأضف مفاتيح Gemini

# تشغيل الخادم
python main.py
```

## المتغيرات البيئية

| المتغير | الوصف | مثال |
|---------|-------|-------|
| `GEMINI_KEYS` | مفاتيح Gemini مفصولة بفاصلة | `key1,key2,key3` |
| `GEMINI_MODEL` | اسم النموذج | `gemini-2.0-flash-lite` |
| `PORT` | منفذ الخادم | `8000` |

## نقاط النهاية

### `GET /`
معلومات الخدمة وحالتها.

### `POST /analyze`
تحليل الطلب وتحديد المدخلات المطلوبة **قبل** التشغيل.

```json
// طلب
{"prompt": "ابني بوت تليجرام يرد على السلام"}

// استجابة
{
  "plan_preview": "...",
  "required_inputs": [
    {"name": "telegram_token", "label": "Telegram Bot Token", "type": "secret"}
  ],
  "ready_to_run": false
}
```

### `POST /agent`
تشغيل الوكيل مع **بث مباشر SSE**.

```json
// طلب
{
  "prompt": "ابني بوت تليجرام يرد على السلام",
  "inputs": {
    "telegram_token": "1234567890:ABCdef..."
  }
}
```

أنواع أحداث SSE:
| النوع | الوصف |
|-------|-------|
| `log` | رسائل عامة |
| `plan` | خطة العمل المُولَّدة |
| `tool_start` | بدء تنفيذ أداة |
| `tool_end` | انتهاء تنفيذ أداة |
| `file_created` | ملف جديد تم إنشاؤه |
| `file_updated` | ملف تم تعديله |
| `command_output` | ناتج تنفيذ أمر |
| `need_input` | النظام يحتاج مدخلات من المستخدم |
| `debug_start` | بدء تصحيح خطأ |
| `debug_end` | انتهاء التصحيح |
| `error` | خطأ |
| `done` | اكتمال المهمة |

### `POST /agent/tool`
استدعاء أداة واحدة مباشرة للاختبار.

```json
{"tool": "plan", "params": {"description": "بوت تليجرام للتذكيرات"}}
```

## النشر على Vercel

```bash
# تثبيت Vercel CLI
npm i -g vercel

# النشر
vercel --prod
```

ثم أضف المتغيرات البيئية في لوحة Vercel:
- `GEMINI_KEYS` = مفاتيحك مفصولة بفاصلة
- `GEMINI_MODEL` = `gemini-2.0-flash-lite`

> **ملاحظة حول النموذج:** إذا واجهت خطأ `model not found`، جرّب `gemini-2.0-flash` أو `gemini-1.5-flash` في متغير `GEMINI_MODEL`.

## بنية الكود

```
agent-backend/
├── main.py              # FastAPI + نقاط النهاية
├── agents/
│   ├── orchestrator.py  # المايسترو — يدير الحلقة كاملاً
│   ├── planner.py       # الوكيل المخطط
│   ├── coder.py         # الوكيل المبرمج
│   ├── debugger.py      # الوكيل المصحح
│   └── assistant.py     # الوكيل المساعد
├── core/
│   ├── gemini.py        # عميل Gemini مع توزيع المفاتيح
│   ├── events.py        # صيغة أحداث SSE
│   └── project.py       # عزل المشاريع في /tmp
├── tools/
│   └── exec_tools.py    # تنفيذ الأوامر
├── requirements.txt
├── vercel.json
└── .env.example
```
