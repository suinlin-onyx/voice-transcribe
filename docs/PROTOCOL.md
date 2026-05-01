# FunASR 转录服务 WebSocket 协议

> 版本: v4.0 (三层分离架构)
> 协议: 兼容 su-rec 插件客户端
> 更新日期: 2026/05/01

---

## 一、概述

### 1.1 服务架构

```
┌─────────────────────────────────────────────────────────────┐
│                      客户端 (Client)                         │
│              WebSocket / HTTP                                │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                    API Server                                │
│  WebSocket: ws://127.0.0.1:9876                          │
│  HTTP:      http://127.0.0.1:9877                         │
│                                                              │
│  - WebSocket 连接管理                                        │
│  - HTTP REST API                                            │
│  - 消息协议转换                                              │
│  - 状态主动推送                                              │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                  Model Server                               │
│  - ASR/VAD 推理 (SenseVoiceSmall)                         │
│  - PUNC 标点模型                                           │
│  - TextProcessor 后处理                                     │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 连接信息

| 协议        | 地址                      | 用途               |
| --------- | ----------------------- | ---------------- |
| WebSocket | `ws://127.0.0.1:9876`   | 双向通信、命令控制、实时转录推送 |
| HTTP      | `http://127.0.0.1:9877` | 健康检查、控制命令 (POST) |

### 1.3 协议版本

| 版本   | 日期         | 说明                     |
| ---- | ---------- | ---------------------- |
| v4.0 | 2026/05/01 | 三层分离架构，协议兼容 su-rec 客户端 |
| v3.0 | 2026/04/30 | asyncio 异步架构           |
| v2.0 | 2026/04/29 | threading 架构           |

---

## 二、消息结构

### 2.1 基础消息格式

所有消息都遵循以下基础结构：

```json
{
  "id": "uuid-v4",
  "type": "message_type",
  "timestamp": 1234567890000
}
```

| 字段          | 类型      | 必填  | 说明                 |
| ----------- | ------- | --- | ------------------ |
| `id`        | string  | 是   | 消息唯一标识，格式为 UUID v4 |
| `type`      | string  | 是   | 消息类型，见消息类型表        |
| `timestamp` | integer | 是   | 毫秒级时间戳             |

### 2.2 消息方向

```
客户端 ──────────── WebSocket ────────────▶ 服务端
                     ◀─────────── 服务端推送

服务端 HTTP API (9877):
  GET  /health           ▶ 客户端
  GET  /status           ▶ 客户端
  POST /control/xxx       ▶ 客户端
```

---

## 三、消息类型 (MessageType)

| type             | 方向        | 说明               |
| ---------------- | --------- | ---------------- |
| `state_update`   | 双向        | 状态更新（服务端主动推送或响应） |
| `state_response` | 双向        | 状态查询响应           |
| `transcription`  | 服务端 → 客户端 | 识别结果推送           |
| `heartbeat`      | 双向        | 心跳保活             |
| `error`          | 服务端 → 客户端 | 错误信息             |

---

## 四、服务器状态 (ServerStatus)

| status              | 说明     | 触发条件              |
| ------------------- | ------ | ----------------- |
| `idle`              | 空闲     | 服务启动后、识别完成后       |
| `connected`         | 已连接    | 客户端连接成功（仅服务端→客户端） |
| `downloading_model` | 下载模型中  | 模型文件不存在，需要下载      |
| `model_loaded`      | 模型加载完成 | 模型加载成功            |
| `recognizing`       | 正在识别   | 检测到音频并正在识别        |
| `no_audio`          | 无音频输入  | 音频设备无输入或音量过低      |
| `audio_detected`    | 检测到音频  | 音频设备有输入           |
| `error`             | 错误状态   | 发生错误              |

---

## 五、客户端动作 (ClientAction)

| action                  | 说明                | 响应 type          |
| ----------------------- | ----------------- | ---------------- |
| `start_recording`       | 开始录音识别            | `state_update`   |
| `stop_recording`        | 停止录音识别            | `state_update`   |
| `query_state`           | 查询当前状态            | `state_response` |
| `heartbeat`             | 心跳保活              | `heartbeat`      |
| `reload_text_processor` | 热重启 TextProcessor | `state_response` |
| `hotwords_reload`       | 重载热词              | `state_response` |

---

## 六、消息格式详解

### 6.1 客户端 → 服务端

#### 6.1.1 动作消息

```json
{
  "id": "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx",
  "type": "state_update",
  "action": "start_recording | stop_recording | query_state | heartbeat | reload_text_processor | hotwords_reload",
  "payload": { ... },
  "timestamp": 1234567890000
}
```

| 字段          | 类型      | 必填  | 说明                 |
| ----------- | ------- | --- | ------------------ |
| `id`        | string  | 是   | UUID v4 格式         |
| `type`      | string  | 是   | 固定为 `state_update` |
| `action`    | string  | 是   | 动作名称               |
| `payload`   | object  | 否   | 动作参数               |
| `timestamp` | integer | 是   | 毫秒时间戳              |

#### 6.1.2 心跳消息

```json
{
  "id": "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx",
  "type": "heartbeat",
  "action": "heartbeat",
  "payload": {},
  "timestamp": 1234567890000
}
```

### 6.2 服务端 → 客户端

#### 6.2.1 状态更新 (state_update)

连接建立时自动推送，或状态变化时推送。

```json
{
  "id": "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx",
  "type": "state_update",
  "status": "idle | connected | downloading_model | model_loaded | recognizing | no_audio | audio_detected | error",
  "payload": { ... },
  "timestamp": 1234567890000
}
```

**示例：连接建立**

```json
{
  "id": "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f",
  "type": "state_update",
  "status": "connected",
  "payload": {},
  "timestamp": 1234567890000
}
```

**示例：开始识别**

```json
{
  "id": "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f",
  "type": "state_update",
  "status": "recognizing",
  "payload": {},
  "timestamp": 1234567890000
}
```

#### 6.2.2 状态响应 (state_response)

查询状态时的响应。

```json
{
  "id": "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx",
  "type": "state_response",
  "status": "idle | model_loaded | recognizing | ...",
  "payload": { ... },
  "timestamp": 1234567890000
}
```

#### 6.2.3 转录文本 (transcription)

实时转录输出，服务端主动推送。

```json
{
  "id": "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx",
  "type": "transcription",
  "status": "recognizing",
  "payload": {
    "text": "这是识别到的文字",
    "isFinal": true,
    "timestamp": 1234567890000
  },
  "timestamp": 1234567890000
}
```

| payload 字段  | 类型      | 说明                    |
| ----------- | ------- | --------------------- |
| `text`      | string  | 识别到的文字内容              |
| `isFinal`   | boolean | 是否为最终结果，`true` 表示完整句子 |
| `timestamp` | integer | 识别时间戳（可选）             |

#### 6.2.4 心跳响应 (heartbeat)

```json
{
  "id": "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx",
  "type": "heartbeat",
  "status": "idle | recognizing | ...",
  "payload": {},
  "timestamp": 1234567890000
}
```

#### 6.2.5 错误消息 (error)

```json
{
  "id": "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx",
  "type": "error",
  "status": "error",
  "payload": {
    "errorMessage": "错误描述信息",
    "errorCode": "ERROR_CODE",
    "timestamp": 1234567890000
  },
  "timestamp": 1234567890000
}
```

---

## 七、服务器状态机

```
                                    ┌─────────────┐
                                    │    idle    │
                                    └──────┬──────┘
                                           │
                                           │ client: start_recording
                                           ▼
                              ┌────────────────────────┐
                              │   downloading_model    │ (if model not exists)
                              └───────────┬────────────┘
                                          │ model downloaded
                                          ▼
                              ┌────────────────────────┐
              ┌───────────────│     model_loaded       │───────────────┐
              │               └──────────┬────────────┘               │
              │                          │ model ready                  │
              │                          ▼                              │
              │               ┌────────────────────────┐                │
              │               │    audio_detected     │                │
              │               └──────────┬────────────┘                │
              │                          │ audio level OK               │
              │                          ▼                              │
              │               ┌────────────────────────┐                │
              │               │     recognizing       │                │
              │               └──────────┬────────────┘                │
              │                          │ silence timeout              │
              │                          ▼                              │
              │               ┌────────────────────────┐                │
              └──────────────►│      no_audio         │◄───────────────┘
                              └────────────────────────┘
                                           │ audio detected
                                           ▼
                              ┌────────────────────────┐
                              │   error (optional)    │
                              └────────────────────────┘
```

---

## 八、通信流程

### 8.1 连接建立

```
客户端                              服务端
  │                                   │
  │  ──── WebSocket 握手 ──────────►  │
  │                                   │
  │  ◄───── onopen ─────────────────  │
  │                                   │
  │       state_update {             │
  │         status: "connected"      │
  │       }                          │
  │  ◄───────────────────────────────│
  │                                   │
  │       (等待模型加载)              │
  │                                   │
  │       state_update {             │
  │         status: "model_loaded"   │
  │       }                          │
  │  ◄───────────────────────────────│
```

### 8.2 开始识别

```
客户端                              服务端
  │                                   │
  │  state_update {                  │
  │    action: "start_recording"     │
  │  }                               │
  │  ───────────────────────────────►│
  │                                   │
  │       state_update {             │
  │         status: "recognizing"    │
  │       }                          │
  │  ◄───────────────────────────────│
  │                                   │
  │       audio_detected (可选)       │
  │  ◄───────────────────────────────│
  │                                   │
  │       transcription {            │
  │         text: "识别文字",        │
  │         isFinal: true            │
  │       }                          │
  │  ◄───────────────────────────────│
```

### 8.3 停止识别

```
客户端                              服务端
  │                                   │
  │  state_update {                  │
  │    action: "stop_recording"      │
  │  }                               │
  │  ───────────────────────────────►│
  │                                   │
  │       state_update {             │
  │         status: "idle"          │
  │       }                          │
  │  ◄───────────────────────────────│
```

### 8.4 心跳机制

```
客户端                              服务端
  │                                   │
  │       heartbeat {                 │
  │         type: "heartbeat",       │
  │         action: "heartbeat",     │
  │         timestamp: 1234567890     │
  │       }                          │
  │  ───────────────────────────────►│
  │                                   │
  │       heartbeat {                 │
  │         type: "heartbeat",       │
  │         status: "idle"           │
  │       }                          │
  │  ◄───────────────────────────────│
```

**心跳间隔**: 建议客户端每 30 秒发送一次
**心跳超时**: 服务端 60 秒无心跳可主动断开连接

---

## 九、HTTP 接口

### 9.1 健康检查

**GET** `/health`

```bash
curl http://127.0.0.1:9877/health
```

**响应**

```json
{
  "status": "idle",
  "models_loaded": true,
  "transcribing": false
}
```

### 9.2 服务状态

**GET** `/status`

```bash
curl http://127.0.0.1:9877/status
```

**响应**

```json
{
  "status": "idle",
  "models_loaded": true,
  "transcribing": false,
  "text_processor": {
    "buffer_len": 0,
    "segment_count": 5,
    "last_process_ago": 12.5
  }
}
```

### 9.3 开始转写

**POST** `/control/start_recording`

```bash
curl -X POST http://127.0.0.1:9877/control/start_recording
```

**响应**

```json
{
  "success": true,
  "message": "Transcription started"
}
```

### 9.4 停止转写

**POST** `/control/stop_recording`

```bash
curl -X POST http://127.0.0.1:9877/control/stop_recording
```

**响应**

```json
{
  "success": true,
  "message": "Transcription stopped"
}
```

### 9.5 热重启 TextProcessor

**POST** `/control/reload-text-processor`

```bash
curl -X POST http://127.0.0.1:9877/control/reload-text-processor
```

**响应**

```json
{
  "success": true,
  "message": "TextProcessor reloaded"
}
```

### 9.6 重载热词

**POST** `/control/reload-hotwords`

```bash
curl -X POST http://127.0.0.1:9877/control/reload-hotwords
```

**响应**

```json
{
  "success": true,
  "message": "Reloaded 4979 hotwords"
}
```

---

## 十、能力对比

| 能力     | WebSocket | HTTP     |
| ------ | --------- | -------- |
| 双向通信   | ✅         | ❌        |
| 实时转录推送 | ✅         | ❌        |
| 状态主动推送 | ✅         | ❌        |
| 多客户端支持 | ✅         | ❌        |
| 命令控制   | ✅         | ✅        |
| 健康检查   | ✅         | ✅        |
| 跨域支持   | -         | ✅ (CORS) |
| 心跳保活   | ✅         | ❌        |

---

## 十一、重启代价

| 操作                   | 耗时       | 影响范围   |
| -------------------- | -------- | ------ |
| TextProcessor reload | ~1s      | 仅当前任务  |
| 热词 reload            | ~2s      | 无      |
| Model Server restart | **~30s** | 所有推理中断 |

---

## 十二、错误码

| errorCode               | 说明      |
| ----------------------- | ------- |
| `MODEL_NOT_FOUND`       | 模型文件未找到 |
| `MODEL_DOWNLOAD_FAILED` | 模型下载失败  |
| `AUDIO_DEVICE_ERROR`    | 音频设备错误  |
| `RECOGNITION_FAILED`    | 识别失败    |
| `CONNECTION_LOST`       | 连接断开    |
| `UNKNOWN`               | 未知错误    |

---

## 十三、客户端实现指南

> 本章节面向客户端（su-rec 插件）开发者，说明客户端需要实现的核心功能。

### 13.1 客户端架构

```
┌─────────────────────────────────────────────────────────────┐
│                      客户端 (Client)                         │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │ServiceManager│  │ConnectionMgr│  │StateController│     │
│  │  - 服务检测   │  │  - WS 连接  │  │  - 状态机   │       │
│  │  - 自动拉起   │  │  - 心跳    │  │  - 状态推送  │       │
│  └─────────────┘  └─────────────┘  └─────────────┘       │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐                         │
│  │MessageBridge│  │ResourceMgr │                         │
│  │  - 消息路由  │  │  - 配置管理  │                         │
│  │  - 序列化   │  │  - 热词加载  │                         │
│  └─────────────┘  └─────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

### 13.2 连接流程

客户端连接到服务的完整流程：

```
1. ServiceManager.isServiceRunning()  ─── HTTP GET /health
   │
   ├── 返回 200 OK ───▶ 服务已运行 ───▶ 直接连接 WebSocket
   │
   └── 连接失败 ───▶ 服务未运行
          │
          ▼
     拉起服务进程 (py run.py)
          │
          ▼
     waitForService() ─── 每 2s 检测 /health
          │
          ├── 返回 200 OK ───▶ 服务就绪 ───▶ 连接 WebSocket
          │
          └── 超时 (45s) ───▶ 提示用户手动启动
```

### 13.3 核心接口

#### 13.3.1 HTTP 接口 (用于服务检测)

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查，检测服务是否运行 |
| `/status` | GET | 获取详细服务状态 |

#### 13.3.2 WebSocket 接口 (用于通信)

| 动作 | 方向 | 说明 |
|------|------|------|
| `start_recording` | → | 开始录音识别 |
| `stop_recording` | → | 停止录音识别 |
| `query_state` | → | 查询当前状态 |
| `heartbeat` | → | 心跳保活 |
| `state_update` | ← | 服务端状态推送 |
| `transcription` | ← | 识别结果 |
| `heartbeat` | ← | 心跳响应 |

### 13.4 最小实现

#### 13.4.1 连接管理器 (TypeScript)

```typescript
class FunASRClient {
  private ws: WebSocket | null = null;
  private serverPath = "D:\\arvin\\obsidian_workpace\\voice-transcribe\\run.py";

  /**
   * 检测服务是否运行
   */
  async isServiceRunning(): Promise<boolean> {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2000);

      const response = await fetch("http://127.0.0.1:9877/health", {
        signal: controller.signal
      });

      clearTimeout(timeout);
      return response.ok;
    } catch {
      return false;
    }
  }

  /**
   * 等待服务就绪
   */
  async waitForService(timeout = 45000): Promise<boolean> {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      if (await this.isServiceRunning()) return true;
      await new Promise(r => setTimeout(r, 2000));
    }
    return false;
  }

  /**
   * 拉起服务
   */
  async startServer(): Promise<void> {
    const { spawn } = require("child_process");
    spawn("cmd", ["/c", "start", "/B", "py", "-3.11", this.serverPath], {
      cwd: "D:\\arvin\\obsidian_workpace\\voice-transcribe",
      shell: false,
      detached: false
    });
  }

  /**
   * 连接服务（自动拉起）
   */
  async connect(): Promise<void> {
    // 1. 检测服务
    if (!await this.isServiceRunning()) {
      console.log("Service not running, starting...");
      await this.startServer();
      const ready = await this.waitForService();
      if (!ready) throw new Error("Service failed to start");
    }

    // 2. WebSocket 连接
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket("ws://127.0.0.1:9876");

      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error("WS connection failed"));
    });
  }

  /**
   * 发送消息
   */
  send(action: string, payload: object = {}): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    this.ws.send(JSON.stringify({
      id: crypto.randomUUID(),
      type: "state_update",
      action,
      payload,
      timestamp: Date.now()
    }));
  }

  /**
   * 开始录音
   */
  startRecording(): void {
    this.send("start_recording");
  }

  /**
   * 停止录音
   */
  stopRecording(): void {
    this.send("stop_recording");
  }

  /**
   * 断开连接
   */
  disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}
```

#### 13.4.2 消息处理器

```typescript
class MessageHandler {
  onTranscript(text: string): void {
    // 处理识别结果
    console.log("Transcript:", text);
  }

  onStateUpdate(status: string): void {
    // 处理状态更新
    console.log("State:", status);
  }

  onError(message: string): void {
    // 处理错误
    console.error("Error:", message);
  }

  handle(data: any): void {
    switch (data.type) {
      case "transcription":
        this.onTranscript(data.payload?.text || "");
        break;
      case "state_update":
      case "state_response":
        this.onStateUpdate(data.status);
        break;
      case "error":
        this.onError(data.payload?.errorMessage || "Unknown error");
        break;
    }
  }
}
```

### 13.5 状态机

客户端状态机与服务器状态对应：

```
┌───────────┐     click      ┌────────────┐
│  offline  │ ─────────────▶│ connecting │
└───────────┘               └─────┬──────┘
     ▲                            │
     │                       连接成功
     │                            ▼
     │offline              ┌──────────┐
     ◀───────────────────── │ recording│
     │                       └────┬─────┘
     │                            │ click
     │offline                     ▼
     ◀───────────────────── ┌────────┐
                            │ stopped│
                            └────┬───┘
                                 │ click
                                 ▼
                            (recording)
```

### 13.6 完整使用示例

```typescript
async function main() {
  const client = new FunASRClient();
  const handler = new MessageHandler();

  try {
    // 1. 连接（自动拉起服务）
    await client.connect();
    console.log("Connected");

    // 2. 设置消息处理
    client.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      handler.handle(data);
    };

    // 3. 开始录音
    client.startRecording();

    // 4. 等待一段时间...
    await new Promise(r => setTimeout(r, 60000));

    // 5. 停止录音
    client.stopRecording();

  } catch (e) {
    console.error("Failed:", e);
  } finally {
    client.disconnect();
  }
}
```

### 13.7 错误处理

| 场景 | 处理方式 |
|------|----------|
| 服务未运行 | 自动拉起，等待 45s 就绪 |
| 拉起失败 | 提示用户手动启动 |
| WebSocket 断开 | 自动重连（最多 3 次） |
| 服务超时无响应 | 提示用户检查服务状态 |

### 13.8 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `serverPath` | `run.py 路径` | 服务启动脚本 |
| `wsPort` | `9876` | WebSocket 端口 |
| `httpPort` | `9877` | HTTP 端口 |
| `startupTimeout` | `45000` | 服务就绪超时（ms） |
| `reconnectAttempts` | `3` | 最大重连次数 |

---

## 十四、客户端示例代码

### 17.1 Python WebSocket 客户端

```python
import asyncio
import websockets
import json
import time
import uuid


class FunASRClient:
    """FunASR 转录服务 WebSocket 客户端"""

    def __init__(self, uri: str = "ws://127.0.0.1:9876"):
        self.uri = uri
        self.ws = None
        self.running = False

    async def connect(self):
        """连接服务器"""
        self.ws = await websockets.connect(self.uri)
        self.running = True
        print(f"Connected to {self.uri}")

        # 接收初始状态
        msg = await self.ws.recv()
        status = json.loads(msg)
        print(f"Server status: {status}")
        return status

    def send_command(self, action: str, payload: dict = None) -> dict:
        """发送命令并等待响应"""
        if not self.ws:
            raise RuntimeError("Not connected")

        msg_id = str(uuid.uuid4())
        message = {
            "id": msg_id,
            "type": "state_update",
            "action": action,
            "payload": payload or {},
            "timestamp": int(time.time() * 1000)
        }

        return message

    async def start_recording(self) -> dict:
        """开始录音识别"""
        msg = self.send_command("start_recording")
        await self.ws.send(json.dumps(msg))
        resp = await self.ws.recv()
        return json.loads(resp)

    async def stop_recording(self) -> dict:
        """停止录音识别"""
        msg = self.send_command("stop_recording")
        await self.ws.send(json.dumps(msg))
        resp = await self.ws.recv()
        return json.loads(resp)

    async def query_state(self) -> dict:
        """查询状态"""
        msg = self.send_command("query_state")
        await self.ws.send(json.dumps(msg))
        resp = await self.ws.recv()
        return json.loads(resp)

    async def heartbeat(self) -> dict:
        """发送心跳"""
        msg = self.send_command("heartbeat")
        await self.ws.send(json.dumps(msg))
        resp = await self.ws.recv()
        return json.loads(resp)

    async def listen(self):
        """
        监听服务器推送消息
        """
        print("Listening for messages...")
        while self.running:
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=60)
                data = json.loads(msg)
                msg_type = data.get("type")
                msg_status = data.get("status")

                if msg_type == "transcription":
                    text = data.get("payload", {}).get("text", "")
                    print(f"转录: {text}")
                elif msg_type == "state_update":
                    print(f"状态更新: {msg_status}")
                elif msg_type == "state_response":
                    print(f"状态响应: {msg_status}")
                elif msg_type == "heartbeat":
                    print("心跳响应")
                elif msg_type == "error":
                    error = data.get("payload", {}).get("errorMessage", "")
                    print(f"错误: {error}")

            except asyncio.TimeoutError:
                # 发送心跳保活
                await self.heartbeat()
            except websockets.ConnectionClosed:
                print("Connection closed")
                break

    async def close(self):
        """关闭连接"""
        self.running = False
        if self.ws:
            await self.ws.close()


async def main():
    client = FunASRClient()

    try:
        # 连接
        await client.connect()

        # 开始录音
        result = await client.start_recording()
        print(f"Start: {result}")

        # 监听 10 秒
        await asyncio.sleep(10)

        # 停止录音
        result = await client.stop_recording()
        print(f"Stop: {result}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

### 13.2 Python HTTP 客户端

```python
import requests


class FunASRHTTPClient:
    """FunASR 转录服务 HTTP 客户端"""

    def __init__(self, base_url: str = "http://127.0.0.1:9877"):
        self.base_url = base_url

    def health(self) -> dict:
        """健康检查"""
        return requests.get(f"{self.base_url}/health").json()

    def status(self) -> dict:
        """获取状态"""
        return requests.get(f"{self.base_url}/status").json()

    def start_recording(self) -> dict:
        """开始转写"""
        return requests.post(f"{self.base_url}/control/start_recording").json()

    def stop_recording(self) -> dict:
        """停止转写"""
        return requests.post(f"{self.base_url}/control/stop_recording").json()

    def reload_text_processor(self) -> dict:
        """热重启 TextProcessor"""
        return requests.post(f"{self.base_url}/control/reload-text-processor").json()

    def reload_hotwords(self) -> dict:
        """重载热词"""
        return requests.post(f"{self.base_url}/control/reload-hotwords").json()


# 使用示例
if __name__ == "__main__":
    client = FunASRHTTPClient()

    # 检查健康
    health = client.health()
    print(f"Health: {health}")

    # 开始转写
    result = client.start_recording()
    print(f"Start: {result}")

    # 等待一段时间...
    import time
    time.sleep(10)

    # 停止转写
    result = client.stop_recording()
    print(f"Stop: {result}")
```

### 13.3 JavaScript (Node.js / 浏览器)

```javascript
class FunASRClient {
    constructor(uri = "ws://127.0.0.1:9876") {
        this.uri = uri;
        this.ws = null;
    }

    connect() {
        return new Promise((resolve, reject) => {
            this.ws = new WebSocket(this.uri);

            this.ws.onopen = () => {
                console.log("Connected");
                resolve();
            };

            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            };

            this.ws.onerror = (error) => {
                console.error("WebSocket error:", error);
                reject(error);
            };

            this.ws.onclose = () => {
                console.log("Connection closed");
            };
        });
    }

    handleMessage(data) {
        switch (data.type) {
            case "state_update":
                console.log("状态更新:", data.status);
                break;
            case "state_response":
                console.log("状态响应:", data.status);
                break;
            case "transcription":
                console.log("转录:", data.payload.text);
                break;
            case "heartbeat":
                console.log("心跳响应");
                break;
            case "error":
                console.error("错误:", data.payload.errorMessage);
                break;
        }
    }

    send(action, payload = {}) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            const message = {
                id: crypto.randomUUID(),
                type: "state_update",
                action: action,
                payload: payload,
                timestamp: Date.now()
            };
            this.ws.send(JSON.stringify(message));
        }
    }

    startRecording() { this.send("start_recording"); }
    stopRecording() { this.send("stop_recording"); }
    queryState() { this.send("query_state"); }
    heartbeat() { this.send("heartbeat"); }

    close() {
        if (this.ws) {
            this.ws.close();
        }
    }
}

// 使用示例
const client = new FunASRClient();
await client.connect();
client.startRecording();

// 监听 10 秒
setTimeout(() => {
    client.stopRecording();
    client.close();
}, 10000);
```

### 13.4 curl 命令测试

```bash
# 健康检查
curl http://127.0.0.1:9877/health

# 获取状态
curl http://127.0.0.1:9877/status

# 开始转写
curl -X POST http://127.0.0.1:9877/control/start_recording

# 停止转写
curl -X POST http://127.0.0.1:9877/control/stop_recording

# 热重启 TextProcessor
curl -X POST http://127.0.0.1:9877/control/reload-text-processor

# 重载热词
curl -X POST http://127.0.0.1:9877/control/reload-hotwords
```

---

## 十五、服务端实现注意事项

### 17.1 WebSocket 服务器实现 (Python)

```python
# 使用 websockets 库 (version 16.0+)
import asyncio
import websockets
import json
import time
import uuid

async def handler(websocket):
    # 生成消息ID
    def gen_id():
        return str(uuid.uuid4())

    def now_ms():
        return int(time.time() * 1000)

    # 发送 connected 状态
    await websocket.send(json.dumps({
        "id": gen_id(),
        "type": "state_update",
        "status": "connected",
        "payload": {},
        "timestamp": now_ms()
    }))

    async for message in websocket:
        data = json.loads(message)
        action = data.get("action", "").lower()

        # 处理各 action...
        if action == "heartbeat":
            await websocket.send(json.dumps({
                "id": gen_id(),
                "type": "heartbeat",
                "status": "idle",
                "payload": {},
                "timestamp": now_ms()
            }))

# 启动服务器
async def main():
    async with websockets.serve(handler, "127.0.0.1", 9876):
        await asyncio.Future()  # 运行直到取消

asyncio.run(main())
```

### 14.2 端口选择

- WebSocket 默认端口: `9876`
- HTTP 默认端口: `9877` (WebSocket 端口 + 1)

### 14.3 心跳超时

- 如果 60 秒内未收到心跳，服务端可主动断开连接
- 客户端应处理连接意外断开的情况

---

## 十六、服务发现与自动拉起

### 17.1 设计目标

客户端连接时自动检测服务状态，未运行时自动拉起服务，避免用户手动启动。

### 17.2 架构流程

```
┌─────────────────────────────────────────────────────────────┐
│                        客户端 (Client)                       │
│                                                             │
│   1. 检测服务 ──HTTP /health──▶ 服务端                     │
│        │                          │                         │
│        │                    返回 200 OK                    │
│        ▼                          │                         │
│   2. WebSocket 连接 ────────────▶│                         │
│                                     │                       │
│   服务未运行？                         │                       │
│        │                             ▼                       │
│        ▼                    3. 返回连接失败                  │
│   4. 拉起服务进程                                        │
│        │                                                  │
│        ▼                                                  │
│   5. 等待模型加载 (~30s)                                  │
│        │                                                  │
│        ▼                                                  │
│   6. 重新检测服务 ─────────────────────────▶               │
└─────────────────────────────────────────────────────────────┘
```

### 17.3 服务发现协议

#### 17.3.1 HTTP 健康检查 (检测服务是否运行)

```
GET http://127.0.0.1:9877/health
```

**响应 (服务运行中):**

```json
{
  "status": "idle",
  "models_loaded": true,
  "transcribing": false,
  "uptime": 3600
}
```

| 字段              | 类型      | 说明        |
| --------------- | ------- | --------- |
| `status`        | string  | 当前服务状态    |
| `models_loaded` | boolean | 模型是否已加载   |
| `transcribing`  | boolean | 是否正在转写    |
| `uptime`        | integer | 服务运行时长（秒） |

**响应 (服务未运行):**

```
连接失败 / 超时
```

### 17.4 自动拉起流程

```
客户端                              服务端
  │                                   │
  │  HTTP GET /health                 │
  │  ────────────────────────────────▶│
  │                                   │
  │  ◄─────── 连接失败 ───────────────│
  │       (服务未运行)                 │
  │                                   │
  ▼                                   │
  拉起服务进程                          │
  (spawn py run.py)                    │
  │                                   │
  │  等待 30 秒 (模型加载)             │
  │                                   │
  │  HTTP GET /health                 │
  │  ────────────────────────────────▶│
  │                                   │
  │  ◄─────── 200 OK ────────────────│
  │       (服务就绪)                   │
  │                                   │
  │  WebSocket 连接                   │
  │  ────────────────────────────────▶│
  │                                   │
```

### 17.5 客户端实现 (TypeScript)

```typescript
class ServiceManager {
  private serverPath: string;
  private serverProcess: any = null;
  private isStartingServer = false;

  constructor(
    private serverHost = "127.0.0.1",
    private serverPort = 9877,
    private startupTimeout = 45000  // 45秒等待模型加载
  ) {
    // 服务启动脚本路径（需客户端配置）
    this.serverPath = "D:\\arvin\\obsidian_workpace\\voice-transcribe\\run.py";
  }

  /**
   * 检测服务是否运行
   */
  async isServiceRunning(): Promise<boolean> {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2000);

      const response = await fetch(`http://${this.serverHost}:${this.serverPort}/health`, {
        signal: controller.signal
      });

      clearTimeout(timeout);
      return response.ok;
    } catch {
      return false;
    }
  }

  /**
   * 等待服务就绪
   */
  async waitForService(timeout = this.startupTimeout): Promise<boolean> {
    const startTime = Date.now();

    while (Date.now() - startTime < timeout) {
      if (await this.isServiceRunning()) {
        return true;
      }
      await this.sleep(2000);  // 每 2 秒检测一次
    }

    return false;
  }

  /**
   * 拉起服务
   */
  async startServer(): Promise<void> {
    if (this.isStartingServer) return;
    if (this.serverProcess) return;

    this.isStartingServer = true;

    return new Promise((resolve, reject) => {
      const spawn = require("child_process");

      this.serverProcess = spawn.spawn("cmd", ["/c", "start", "/B", "py", "-3.11", this.serverPath], {
        cwd: "D:\\arvin\\obsidian_workpace\\voice-transcribe",
        shell: false,
        detached: false
      });

      this.serverProcess.on("close", (code: number) => {
        console.log("[Server] Closed:", code);
        this.serverProcess = null;
        this.isStartingServer = false;
      });

      this.serverProcess.on("error", (err: Error) => {
        console.error("[Server] Error:", err);
        this.isStartingServer = false;
        reject(err);
      });

      // 启动后标记为已开始
      this.isStartingServer = false;
      resolve();
    });
  }

  /**
   * 确保服务运行
   */
  async ensureService(): Promise<boolean> {
    // 1. 检测服务是否运行
    if (await this.isServiceRunning()) {
      console.log("[ServiceManager] Service already running");
      return true;
    }

    // 2. 拉起服务
    console.log("[ServiceManager] Starting server...");
    await this.startServer();

    // 3. 等待服务就绪
    console.log("[ServiceManager] Waiting for service to be ready...");
    const ready = await this.waitForService();

    if (ready) {
      console.log("[ServiceManager] Service is ready");
    } else {
      console.error("[ServiceManager] Service failed to start");
    }

    return ready;
  }

  /**
   * 停止服务
   */
  stopServer(): void {
    if (this.serverProcess) {
      try {
        this.serverProcess.kill();
        this.serverProcess = null;
      } catch (e) {
        console.error("[ServiceManager] Failed to stop server:", e);
      }
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
```

### 17.6 集成到连接管理器

```typescript
class FunASRClient {
  private serviceManager: ServiceManager;
  private ws: WebSocket | null = null;

  constructor(
    private serverHost = "127.0.0.1",
    private wsPort = 9876,
    private httpPort = 9877
  ) {
    this.serviceManager = new ServiceManager(serverHost, httpPort);
  }

  /**
   * 连接服务（自动拉起）
   */
  async connect(): Promise<void> {
    // 1. 确保服务运行
    const ready = await this.serviceManager.ensureService();

    if (!ready) {
      throw new Error("Service failed to start");
    }

    // 2. 建立 WebSocket 连接
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(`ws://${this.serverHost}:${this.wsPort}`);

      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error("WebSocket connection failed"));
    });
  }

  /**
   * 断开连接
   */
  disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  /**
   * 释放资源（可选：停止服务）
   */
  destroy(stopServer = false): void {
    this.disconnect();
    if (stopServer) {
      this.serviceManager.stopServer();
    }
  }
}
```

### 17.7 单例控制

为避免多个客户端同时拉起服务造成重复实例，使用进程级锁：

```typescript
class ServiceManager {
  private static instance: ServiceManager | null = null;
  private static lockFile = "D:\\arvin\\obsidian_workpace\\voice-transcribe\\.service.lock";

  static getInstance(): ServiceManager {
    if (!ServiceManager.instance) {
      ServiceManager.instance = new ServiceManager();
    }
    return ServiceManager.instance;
  }

  async acquireLock(): Promise<boolean> {
    const fs = require("fs");

    try {
      // 尝试创建锁文件
      fs.writeFileSync(ServiceManager.lockFile, String(process.pid), "utf-8");
      return true;
    } catch (e) {
      // 锁文件已存在，检查进程是否存活
      try {
        const pid = parseInt(fs.readFileSync(ServiceManager.lockFile, "utf-8"));
        // 检查进程是否存在（Windows）
        const result = spawn.execSync(`tasklist /FI "PID eq ${pid}"`, { encoding: "utf-8" });
        if (result.includes(String(pid))) {
          return false;  // 进程存活，锁被占用
        }
        // 进程已死，强占锁
        fs.writeFileSync(ServiceManager.lockFile, String(process.pid), "utf-8");
        return true;
      } catch {
        return false;
      }
    }
  }

  releaseLock(): void {
    const fs = require("fs");
    try {
      fs.unlinkSync(ServiceManager.lockFile);
    } catch {}
  }
}
```

### 17.8 错误处理

| 场景           | 处理方式          |
| ------------ | ------------- |
| 服务启动失败       | 显示错误提示，允许用户重试 |
| 服务启动超时 (45s) | 重试或提示用户手动启动   |
| 锁被占用         | 等待原服务结束或提示用户  |
| 模型加载失败       | 记录日志，尝试重新拉起   |

### 17.9 配置参数

| 参数                    | 默认值         | 说明           |
| --------------------- | ----------- | ------------ |
| `serverHost`          | `127.0.0.1` | 服务地址         |
| `wsPort`              | `9876`      | WebSocket 端口 |
| `httpPort`            | `9877`      | HTTP 端口      |
| `startupTimeout`      | `45000`     | 等待服务就绪超时（毫秒） |
| `healthCheckInterval` | `2000`      | 健康检查间隔（毫秒）   |

---

## 十七、服务端单例实现

### 17.1 设计目标

确保服务进程只有一个实例在运行，避免资源冲突和状态不一致。

### 17.2 跨平台实现

```python
# single_instance.py
"""
跨平台单例检查模块

Windows: 使用 msvcrt 文件锁 (标准库)
Unix/macOS: 使用 fcntl.flock 文件锁
"""
import sys
import os


def acquire_single_instance():
    """
    获取单例锁

    Returns:
        True - 获取锁成功，服务可以启动
        False - 已有实例运行，服务不应启动
    """
    if sys.platform == "win32":
        return _acquire_windows()
    else:
        return _acquire_unix()


def _acquire_windows():
    """Windows: 使用 msvcrt 文件锁"""
    import msvcrt
    import atexit

    pid_file = _get_pid_file_path()

    try:
        f = open(pid_file, 'r+')
    except FileNotFoundError:
        f = open(pid_file, 'w+')

    try:
        # LK_NBLCK = non-blocking lock
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        f.seek(0)
        f.write(str(os.getpid()))
        f.flush()

        def cleanup():
            try:
                f.close()
                if os.path.exists(pid_file):
                    os.remove(pid_file)
            except:
                pass

        atexit.register(cleanup)
        return True

    except IOError:
        f.close()
        print("Error: 服务已在运行，请先关闭现有实例")
        return False


def _acquire_unix():
    """Unix/macOS: 使用 flock 文件锁"""
    import fcntl
    import atexit

    pid_file = _get_pid_file_path()

    try:
        f = open(pid_file, 'w')
    except Exception as e:
        print(f"Warning: 无法打开 PID 文件 ({e})")
        return True  # 降级，不阻止启动

    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(str(os.getpid()))
        f.flush()

        def cleanup():
            try:
                f.close()
                if os.path.exists(pid_file):
                    os.remove(pid_file)
            except:
                pass

        atexit.register(cleanup)
        return True

    except IOError:
        f.close()
        print("Error: 服务已在运行，请先关闭现有实例")
        return False


def _get_pid_file_path():
    """获取 PID 文件路径"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".funasr.pid")
```

### 17.3 使用方式

```python
# run.py
import sys
from single_instance import acquire_single_instance

def main():
    # 单例检查
    if not acquire_single_instance():
        sys.exit(1)

    # 正常启动逻辑
    from api_server import APIServer
    from model_server import ModelServer
    # ...

if __name__ == "__main__":
    main()
```

### 17.4 启动输出

```
# 单例启动成功
$ py -3.11 run.py
Starting v4 (三层分离架构)...
FunASR Transcription Server v4.0 (三层分离架构)
WebSocket: ws://127.0.0.1:9876
HTTP:     http://127.0.0.1:9877
等待模型加载...

# 重复启动
$ py -3.11 run.py
Error: 服务已在运行，请先关闭现有实例
```

### 17.5 实现原理

| 平台 | 机制 | 说明 |
|------|------|------|
| Windows | `msvcrt.locking()` | Windows C 运行时的文件锁，标准库自带 |
| Unix/macOS | `fcntl.flock()` | POSIX 文件锁 |

两者都是**操作系统级别的文件锁**，进程崩溃后自动释放。

### 17.6 无外部依赖

全部使用 Python 标准库，无需安装任何第三方包。

---

## 十八、文件结构

```
voice-transcribe/
├── run.py                    # 入口，启动所有模块
├── single_instance.py        # 单例检查模块
├── api_server.py             # API Server 模块 (WebSocket + HTTP)
├── model_server.py           # Model Server 模块
├── text_processor.py        # Text Processor 模块
├── config.py                 # 共享配置
└── docs/
    └── PROTOCOL.md           # 本文档 - 统一协议定义
```
