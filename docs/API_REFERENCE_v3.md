# FunASR 转录服务 API 接口文档

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

---

## 二、WebSocket 接口

### 2.1 连接建立

WebSocket 连接地址: `ws://127.0.0.1:9876`

连接成功后，服务器会自动推送 `connected` 状态。

### 2.2 消息格式

**客户端发送消息 (JSON)**

```json
{
  "id": "uuid-v4",
  "type": "state_update | state_response | heartbeat",
  "action": "start_recording | stop_recording | query_state | heartbeat",
  "payload": { ... },
  "timestamp": 1234567890000
}
```

**服务器推送消息 (JSON)**

```json
{
  "id": "uuid-v4",
  "type": "state_update | state_response | transcription | error | heartbeat",
  "status": "idle | connected | downloading_model | model_loaded | recognizing | no_audio | error",
  "payload": { ... },
  "timestamp": 1234567890000
}
```

### 2.3 支持的 Action 命令

| action                  | 说明                | 响应 type          |
| ----------------------- | ----------------- | ---------------- |
| `start_recording`       | 开始录音识别            | `state_update`   |
| `stop_recording`        | 停止录音识别            | `state_update`   |
| `query_state`           | 查询当前状态            | `state_response` |
| `heartbeat`             | 心跳保活              | `heartbeat`      |
| `reload_text_processor` | 热重启 TextProcessor | `state_response` |
| `hotwords_reload`       | 重载热词              | `state_response` |

### 2.4 服务器状态 (ServerStatus)

| status              | 说明     | 触发条件         |
| ------------------- | ------ | ------------ |
| `idle`              | 空闲     | 服务启动后、识别完成后  |
| `connected`         | 已连接    | 客户端连接成功      |
| `downloading_model` | 下载模型中  | 模型文件不存在，需要下载 |
| `model_loaded`      | 模型加载完成 | 模型加载成功       |
| `recognizing`       | 正在识别   | 检测到音频并正在识别   |
| `no_audio`          | 无音频输入  | 音频设备无输入或音量过低 |
| `audio_detected`    | 检测到音频  | 音频设备有输入      |
| `error`             | 错误状态   | 发生错误         |

### 2.5 消息类型 (MessageType)

| type             | 方向        | 说明               |
| ---------------- | --------- | ---------------- |
| `state_update`   | 双向        | 状态更新（服务端主动推送或响应） |
| `state_response` | 双向        | 状态查询响应           |
| `transcription`  | 服务端 → 客户端 | 识别结果             |
| `error`          | 服务端 → 客户端 | 错误信息             |
| `heartbeat`      | 双向        | 心跳               |

### 2.6 服务器推送的消息类型

#### 2.6.1 状态消息 (state_update)

连接建立时自动推送，或状态变化时推送。

```json
{
  "id": "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f",
  "type": "state_update",
  "status": "idle",
  "payload": {},
  "timestamp": 1234567890000
}
```

#### 2.6.2 状态响应 (state_response)

查询状态时的响应。

```json
{
  "id": "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f",
  "type": "state_response",
  "status": "model_loaded",
  "payload": {},
  "timestamp": 1234567890000
}
```

#### 2.6.3 转录文本 (transcription)

实时转录输出。

```json
{
  "id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
  "type": "transcription",
  "status": "recognizing",
  "payload": {
    "text": "这是识别到的文字",
    "isFinal": true
  },
  "timestamp": 1234567890000
}
```

#### 2.6.4 心跳响应 (heartbeat)

```json
{
  "id": "d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f8a",
  "type": "heartbeat",
  "status": "idle",
  "payload": {},
  "timestamp": 1234567890000
}
```

#### 2.6.5 错误消息 (error)

```json
{
  "id": "e5f6a7b8-c9d0-4e1f-a2b3-c4d5e6f7a8b9",
  "type": "error",
  "status": "error",
  "payload": {
    "errorMessage": "Models still loading",
    "errorCode": "MODEL_NOT_FOUND"
  },
  "timestamp": 1234567890000
}
```

---

## 三、HTTP 接口

### 3.1 健康检查

**GET** `/health`

检查服务健康状态。

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

### 3.2 服务状态

**GET** `/status`

获取详细服务状态。

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

### 3.3 开始转写

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

### 3.4 停止转写

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

### 3.5 热重启 TextProcessor

**POST** `/control/reload-text-processor`

热重启文本处理器，无需中断服务。

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

### 3.6 重载热词

**POST** `/control/reload-hotwords`

重新从笔记目录加载热词。

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

## 四、客户端示例代码

### 4.1 Python WebSocket 客户端

```python
import asyncio
import websockets
import json


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

    async def send_command(self, action: str, payload: dict = None) -> dict:
        """发送命令并等待响应"""
        if not self.ws:
            raise RuntimeError("Not connected")

        import uuid
        msg_id = str(uuid.uuid4())
        message = {
            "id": msg_id,
            "type": "state_update",
            "action": action,
            "payload": payload or {},
            "timestamp": int(time.time() * 1000)
        }

        await self.ws.send(json.dumps(message))
        resp = await self.ws.recv()
        return json.loads(resp)

    async def start_recording(self) -> dict:
        """开始录音识别"""
        return await self.send_command("start_recording")

    async def stop_recording(self) -> dict:
        """停止录音识别"""
        return await self.send_command("stop_recording")

    async def query_state(self) -> dict:
        """查询状态"""
        return await self.send_command("query_state")

    async def heartbeat(self) -> dict:
        """发送心跳"""
        return await self.send_command("heartbeat")

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


import time

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

### 4.2 Python HTTP 客户端

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

### 4.3 JavaScript (Node.js / 浏览器)

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

### 4.4 curl 命令测试

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

## 五、能力对比

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

## 六、重启代价

| 操作                   | 耗时       | 影响范围   |
| -------------------- | -------- | ------ |
| TextProcessor reload | ~1s      | 仅当前任务  |
| 热词 reload            | ~2s      | 无      |
| Model Server restart | **~30s** | 所有推理中断 |

---

## 七、错误码

| errorCode               | 说明      |
| ----------------------- | ------- |
| `MODEL_NOT_FOUND`       | 模型文件未找到 |
| `MODEL_DOWNLOAD_FAILED` | 模型下载失败  |
| `AUDIO_DEVICE_ERROR`    | 音频设备错误  |
| `RECOGNITION_FAILED`    | 识别失败    |
| `CONNECTION_LOST`       | 连接断开    |
| `UNKNOWN`               | 未知错误    |

---

## 八、版本历史

| 版本   | 日期         | 说明                     |
| ---- | ---------- | ---------------------- |
| v4.0 | 2026/05/01 | 三层分离架构，协议兼容 su-rec 客户端 |
| v3.0 | 2026/04/30 | asyncio 异步架构           |
| v2.0 | 2026/04/29 | threading 架构           |
