# 客户端集成规范

> 面向 voice-transcribe 服务端开发者，描述与 su-rec Obsidian 插件的交互约定。
> 客户端视角文档：[su-rec/服务端交互协议](../../su-rec/docs/服务端交互协议.md)

---

## 一、架构概览

```
┌─────────────────────────────────────────┐
│           su-rec (Obsidian 插件)          │
│                                          │
│  服务发现 → 自动拉起 → WebSocket 通信      │
└──────────────────┬──────────────────────┘
                   │ WebSocket + HTTP
┌──────────────────▼──────────────────────┐
│         voice-transcribe (Python)        │
│                                          │
│  API Server  ←→  Model Server            │
│  (WS + HTTP)      (ASR/VAD/PUNC)        │
└─────────────────────────────────────────┘
```

---

## 二、端口约定

| 协议 | 端口 | 说明 |
|------|------|------|
| WebSocket | `P` | 双向通信，`P` 由 CLI 参数或 settings.json 决定 |
| HTTP | `P + 1` | 健康检查、控制 API |

默认端口：WebSocket `9876`，HTTP `9877`。

---

## 三、CLI 参数

`run.py` 接受以下参数：

```
python run.py --port 9876 --debug
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--port` | int | `settings.json → server.port` | WebSocket 端口，HTTP 自动为 port+1 |
| `--debug` | flag | `settings.json → debug` | 调试模式，启用控制台日志和窗口显示 |

### 端口优先级

1. `--port` CLI 参数（最高优先级）
2. 如果未传，读取 `settings.json → server.port`
3. 接收到端口后，更新 `settings.json` 持久化

### debug 优先级

1. `--debug` CLI flag
2. 如果未传，读取 `settings.json → debug`（默认 `true`）

---

## 四、启动时序

WebSocket 端口在模型加载**之前**就已监听。这是分层架构的核心优势——客户端可以尽早连接并接收状态推送。

```
时间线:
  t=0s    run.py 启动
  t=0.1s  WS 端口 open，HTTP 端口 open
  t=0.2s  API Server 就绪，可以 accept 连接
  t=0.5s  开始加载 ASR 模型
  t=5s    ASR 模型就绪
  t=6s    开始加载 PUNC 模型
  t=8s    PUNC 模型就绪
  t=8.5s  模型全部就绪
```

---

## 五、加载进度协议

### 5.1 新增 `loading` 状态

连接建立后，服务端在模型加载期间推送 `loading` 状态，告知客户端当前进度。

```json
{
  "id": "uuid",
  "type": "state_update",
  "status": "loading",
  "payload": {
    "step": "asr_loading",
    "message": "加载 ASR 模型 (ONNX)...",
    "elapsed": 3
  },
  "timestamp": 1700000000000
}
```

| payload 字段 | 类型 | 说明 |
|-------------|------|------|
| `step` | string | 当前步骤标识 |
| `message` | string | 用户可读的进度描述 |
| `elapsed` | int | 已用时间（秒） |

### 5.2 步骤标识

| step | 说明 |
|------|------|
| `asr_loading` | 正在加载 ASR 模型 |
| `vad_loading` | 正在加载 VAD 模型 |
| `punc_loading` | 正在加载标点模型 |
| `hotwords_loading` | 正在加载热词 |
| `ready` | 全部就绪（等同于 `model_loaded`） |

### 5.3 完整连接序列

```
客户端                                   服务端
  │                                        │
  │  ──── WebSocket 握手 ──────────────►   │
  │                                        │
  │       state_update {status: "connected"}│
  │  ◄───────────────────────────────────  │
  │                                        │
  │       state_update {status: "loading", │
  │         payload: {step: "asr_loading", │
  │           message: "加载 ASR 模型..."}} │
  │  ◄───────────────────────────────────  │
  │                                        │
  │       ... 更多 loading 推送 ...        │
  │                                        │
  │       state_update {status: "model_loaded"}│
  │  ◄───────────────────────────────────  │
  │                                        │
  │  客户端现在可以发送 start_recording    │
```

---

## 六、调试模式

### 6.1 行为差异

| 行为 | debug=true（默认） | debug=false |
|------|-------------------|-------------|
| 控制台窗口 | 显示（python.exe） | 隐藏（pythonw.exe） |
| stdout 日志级别 | INFO（进度、状态） | WARNING（仅错误） |
| stderr 输出 | 完整输出 | 重定向到日志文件 |
| su-rec 拉起方式 | `python.exe run.py --debug` | `pythonw.exe run.py` |

### 6.2 settings.json

```json
{
  "debug": true,
  "server": {
    "host": "127.0.0.1",
    "port": 9876
  }
}
```

---

## 七、单例锁（按端口）

锁文件名包含端口号，允许不同端口共存：

```
.voice-transcribe-9876.lock
.voice-transcribe-9877.lock
```

同一端口只允许一个实例，不同端口可以同时运行。

---

## 八、Python 路径检测

服务端不硬编码 Python 路径。su-rec 拉起服务时的检测顺序：

1. 用户配置的 Python 路径（su-rec 设置面板）
2. 环境变量 `PYTHON_PATH`
3. PATH 中的 `python` / `python3`
4. 常见安装位置（`Python311`、`Python312` 等）

---

## 九、双向链接

- [su-rec 服务端交互协议](../../su-rec/docs/服务端交互协议.md) — 客户端视角
- [PROTOCOL.md](./PROTOCOL.md) — WebSocket/HTTP 协议细节
- [README.md](./README.md) — 服务端开发文档
