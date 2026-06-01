# Agent Backend — API Reference

Base URL (local): `http://localhost:8000`
Base URL (Railway): `https://YOUR_APP.railway.app`

---

## Endpoints

### `GET /`
Health + info

**Response:**
```json
{
  "status": "ok",
  "service": "Agent Backend",
  "gemini_keys_loaded": 5,
  "model": "gemini-3.1-flash-lite"
}
```

---

### `GET /health`
Simple health check.

**Response:** `{"status": "ok"}`

---

### `POST /analyze`
Analyze prompt — returns a plan preview and list of required inputs (before running).

**Request:**
```json
{ "prompt": "ابني بوت تيليجرام يرد على السلام" }
```

**Response:**
```json
{
  "plan_preview": "خطة: بوت تيليجرام ...",
  "required_inputs": [
    {
      "name": "telegram_token",
      "label": "Telegram Bot Token",
      "hint": "أنشئ بوت عبر @BotFather",
      "type": "secret"
    }
  ],
  "tokens_used": 807,
  "ready_to_run": false
}
```

---

### `POST /agent`
Run the full agent loop. **Streams SSE events.**

**Request:**
```json
{
  "prompt": "ابني بوت تيليجرام يرد على السلام",
  "inputs": {
    "telegram_token": "123456:ABC-your-token"
  }
}
```

**Response:** `Content-Type: text/event-stream`

Each line is:
```
data: {"type":"log","content":"...","time_ms":123,"tokens_used":0}

data: {"type":"plan","content":"الخطة...","time_ms":2100,"tokens_used":807}

data: {"type":"file_created","content":"✅ bot.py","file_path":"bot.py","preview":"import...","time_ms":5200,"tokens_used":412}

data: {"type":"done","content":"🎉 تم!","files":{"bot.py":"...","requirements.txt":"..."},"project_id":"abc123","total_steps":5}
```

**Event types:**
| type | description |
|------|-------------|
| `log` | General info message |
| `plan` | Full plan text from planner |
| `tool_start` | Agent starting a tool call |
| `tool_end` | Agent finished a tool call |
| `agent_thinking` | Intermediate reasoning |
| `file_created` | New file written |
| `file_updated` | File patched/fixed |
| `command_output` | Shell command result |
| `debug_start` | Debugger activated |
| `need_input` | Missing required inputs |
| `error` | Unrecoverable error |
| `done` | Final event with all files |

---

### `POST /agent/tool`
Call a single tool directly (for testing).

**Request:**
```json
{ "tool": "plan", "params": { "description": "بوت تيليجرام" } }
```

---

## Kotlin / Android Integration

### 1. Dependencies (build.gradle.kts)
```kotlin
dependencies {
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
    implementation("com.squareup.moshi:moshi-kotlin:1.15.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.0")
}
```

---

### 2. Data Classes
```kotlin
import com.squareup.moshi.Json
import com.squareup.moshi.JsonClass

@JsonClass(generateAdapter = true)
data class AgentRequest(
    val prompt: String,
    val inputs: Map<String, String> = emptyMap()
)

@JsonClass(generateAdapter = true)
data class AgentEvent(
    val type: String,
    val content: String,
    @Json(name = "time_ms") val timeMs: Long = 0,
    @Json(name = "tokens_used") val tokensUsed: Int = 0,
    @Json(name = "file_path") val filePath: String? = null,
    val preview: String? = null,
    val files: Map<String, String>? = null,
    @Json(name = "project_id") val projectId: String? = null,
    val error: String? = null
)

@JsonClass(generateAdapter = true)
data class AnalyzeRequest(val prompt: String)

@JsonClass(generateAdapter = true)
data class AnalyzeResponse(
    @Json(name = "plan_preview") val planPreview: String,
    @Json(name = "required_inputs") val requiredInputs: List<InputField>,
    @Json(name = "tokens_used") val tokensUsed: Int,
    @Json(name = "ready_to_run") val readyToRun: Boolean
)

@JsonClass(generateAdapter = true)
data class InputField(
    val name: String,
    val label: String,
    val hint: String? = null,
    val type: String = "text"
)
```

---

### 3. SSE Streaming Client (OkHttp)
```kotlin
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory

class AgentClient(private val baseUrl: String) {

    private val moshi = Moshi.Builder()
        .addLast(KotlinJsonAdapterFactory())
        .build()

    private val okHttpClient = OkHttpClient.Builder()
        .readTimeout(0, java.util.concurrent.TimeUnit.SECONDS) // no timeout for SSE
        .build()

    /** Analyze prompt before running — returns required inputs */
    suspend fun analyze(prompt: String): AnalyzeResponse {
        val adapter = moshi.adapter(AnalyzeRequest::class.java)
        val body = adapter.toJson(AnalyzeRequest(prompt))
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("$baseUrl/analyze")
            .post(body)
            .build()

        val response = okHttpClient.newCall(request).execute()
        val json = response.body!!.string()
        return moshi.adapter(AnalyzeResponse::class.java).fromJson(json)!!
    }

    /** Stream agent events as a Flow */
    fun runAgent(
        prompt: String,
        inputs: Map<String, String> = emptyMap()
    ): Flow<AgentEvent> = callbackFlow {

        val reqAdapter = moshi.adapter(AgentRequest::class.java)
        val evtAdapter = moshi.adapter(AgentEvent::class.java)

        val body = reqAdapter.toJson(AgentRequest(prompt, inputs))
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("$baseUrl/agent")
            .post(body)
            .header("Accept", "text/event-stream")
            .header("Cache-Control", "no-cache")
            .build()

        val factory = EventSources.createFactory(okHttpClient)
        val listener = object : EventSourceListener() {
            override fun onEvent(
                eventSource: EventSource,
                id: String?,
                type: String?,
                data: String
            ) {
                if (data.isBlank()) return
                runCatching { evtAdapter.fromJson(data) }
                    .onSuccess { event -> event?.let { trySend(it) } }
            }

            override fun onClosed(eventSource: EventSource) {
                close()
            }

            override fun onFailure(
                eventSource: EventSource,
                t: Throwable?,
                response: Response?
            ) {
                close(t)
            }
        }

        val eventSource = factory.newEventSource(request, listener)
        awaitClose { eventSource.cancel() }
    }
}
```

---

### 4. Usage in ViewModel
```kotlin
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch

class AgentViewModel : ViewModel() {

    private val client = AgentClient("https://YOUR_APP.railway.app")

    private val _events = MutableStateFlow<List<AgentEvent>>(emptyList())
    val events: StateFlow<List<AgentEvent>> = _events.asStateFlow()

    private val _isRunning = MutableStateFlow(false)
    val isRunning: StateFlow<Boolean> = _isRunning.asStateFlow()

    fun runAgent(prompt: String, inputs: Map<String, String> = emptyMap()) {
        viewModelScope.launch {
            _isRunning.value = true
            _events.value = emptyList()

            client.runAgent(prompt, inputs)
                .catch { e -> /* handle error */ }
                .collect { event ->
                    _events.update { it + event }
                    if (event.type == "done" || event.type == "error") {
                        _isRunning.value = false
                    }
                }
        }
    }
}
```

---

### 5. Compose UI (Material 3)
```kotlin
@Composable
fun AgentScreen(viewModel: AgentViewModel = viewModel()) {
    val events by viewModel.events.collectAsState()
    val isRunning by viewModel.isRunning.collectAsState()
    var prompt by remember { mutableStateOf("") }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {

        OutlinedTextField(
            value = prompt,
            onValueChange = { prompt = it },
            label = { Text("وصف المشروع") },
            modifier = Modifier.fillMaxWidth()
        )

        Spacer(modifier = Modifier.height(8.dp))

        Button(
            onClick = { viewModel.runAgent(prompt) },
            enabled = !isRunning && prompt.isNotBlank(),
            modifier = Modifier.fillMaxWidth()
        ) {
            if (isRunning) CircularProgressIndicator(modifier = Modifier.size(16.dp))
            else Text("🚀 تشغيل الوكيل")
        }

        Spacer(modifier = Modifier.height(16.dp))

        LazyColumn {
            items(events) { event ->
                EventCard(event)
            }
        }
    }
}

@Composable
fun EventCard(event: AgentEvent) {
    val icon = when (event.type) {
        "plan"         -> "📋"
        "file_created" -> "✅"
        "file_updated" -> "🔧"
        "debug_start"  -> "🐛"
        "done"         -> "🎉"
        "error"        -> "❌"
        else           -> "ℹ️"
    }
    Surface(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        shape = MaterialTheme.shapes.medium,
        tonalElevation = 2.dp
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = "$icon ${event.type}",
                style = MaterialTheme.typography.labelMedium
            )
            Text(
                text = event.content,
                style = MaterialTheme.typography.bodySmall
            )
            if (event.timeMs > 0) {
                Text(
                    text = "${event.timeMs}ms • ${event.tokensUsed} tokens",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline
                )
            }
        }
    }
}
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_KEYS` | required | مفاتيح Gemini مفصولة بفاصلة |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | النموذج الافتراضي |
| `GEMINI_PREMIUM_MODEL` | `gemini-3.5-flash` | النموذج المميز (للمخطط) |
| `PREMIUM_RPM` | `5` | حد الطلبات في الدقيقة للنموذج المميز |
| `PREMIUM_RPD` | `20` | حد الطلبات في اليوم للنموذج المميز |
| `PORT` | `8000` | منفذ الخادم |
