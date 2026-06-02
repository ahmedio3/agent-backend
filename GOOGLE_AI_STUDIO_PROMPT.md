# Prompt لتحديث تطبيق Kotlin في Google AI Studio

انسخ النص التالي وأرسله في Google AI Studio:

---

أحتاج تحديث تطبيق Kotlin المتصل بـ API خاص بي. فيما يلي التغييرات التي حدثت على الـ API:

## 1. تغيير أسماء التيرات (مهم جداً)

تغيّرت قيم `tier` المُرسَلة في جميع طلبات POST:

| القديم | الجديد       |
|--------|--------------|
| `"mini"` | `"x1.0"` |
| `"core"` | `"x1.5"` |
| `"max"`  | `"x2.0"` |

كل مكان في الكود يُرسل `tier = "mini"` أو `"core"` أو `"max"` يجب تحديثه للقيم الجديدة.
أسماء العرض للمستخدم يجب أن تصبح: **Veylor x1.0** / **Veylor x1.5** / **Veylor x2.0**

## 2. إصلاح Terminal 404

المشكلة الأصلية: عند فتح Terminal يظهر خطأ `HTTP 404`.

**السبب:** الـ API يبحث عن `project_id` في الذاكرة، وبعد restart ينساها.
**الإصلاح على الـ API:** تم. الـ API الآن يبحث تلقائياً في `/tmp/agent_{id}` على القرص.

**المطلوب من Kotlin:** لا يوجد تغيير في الكود، لكن تأكد أن:
- الـ `project_id` المُستخدم في `POST /terminal` هو نفس الـ `project_id` الذي عاد من `POST /agent` في حقل `project_id` داخل حدث `done`
- إذا كان التطبيق يحفظ الـ `project_id` بشكل صحيح ويُرسله عند فتح التيرمنال، المشكلة محلولة

## 3. إضافة ميزة الرفع إلى GitHub

Endpoint جديد: `POST /deploy/github/{project_id}`

**Request Body:**
```json
{
  "token": "ghp_xxxxxxxxxxxx",
  "repo_name": "my-project",
  "private": false,
  "commit_message": "Initial commit from Veylor",
  "branch": "main"
}
```

**الـ Response:** SSE stream يُرجع events بالشكل:
```json
{"type": "start",   "message": "..."}
{"type": "log",     "message": "...", "level": "info|warning|error"}
{"type": "url",     "url": "https://github.com/user/repo", "repo": "user/repo"}
{"type": "done",    "url": "https://github.com/...", "repo": "user/repo", "commit": "abc1234", "branch": "main", "files_uploaded": 12, "message": "..."}
{"type": "error",   "message": "..."}
```

**المطلوب من Kotlin:**
- أضف شاشة/ديالوج جديد "رفع إلى GitHub" يظهر بعد اكتمال بناء المشروع
- فيها حقل نصي لـ GitHub Token (Classic PAT)، حقل لاسم الـ repo، toggle لـ private/public
- عند الضغط على "رفع": أرسل الـ request لـ SSE stream واعرض التقدم كما تفعل مع `/agent`
- عند ظهور `type: "done"` اعرض رابط الـ repo مع زر لفتحه في المتصفح

## 4. تحديث عرض الـ Vercel SSE

الـ Vercel SSE يُرسل الآن سطور keepalive بهذا الشكل:
```
: ping
```
(سطر يبدأ بـ `:` بدون `data:`) — تأكد أن الـ SSE parser يتجاهل هذه السطور ولا تُسبب crash.
في معظم مكتبات SSE هذا يُتجاهل تلقائياً، لكن لو كانت هناك معالجة يدوية للـ stream تأكد من تخطي السطور التي تبدأ بـ `:`.

## 5. تحديث endpoint `/usage`

الـ response الآن يحتوي على مفاتيح `"x1.0"`, `"x1.5"`, `"x2.0"` بدلاً من `"mini"`, `"core"`, `"max"`.
إذا كان التطبيق يُحلّل هذا الـ response، حدّث أسماء المفاتيح.

---

هذه جميع التغييرات. طبّقها على الكود الموجود وأخبرني بأي أسئلة.
