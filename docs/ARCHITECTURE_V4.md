# FunASR 转录服务架构演进

> 归档日期: 2026/04/30

---

## 一、当前问题

### 1.1 模型加载慢
- 模型加载需要 ~30 秒
- 重启服务代价高
- 无法热更新模型

### 1.2 服务状态不可感知
- 客户端无法预知服务是否可用
- 无法在连接前感知服务状态
- 断线重连逻辑不完善

### 1.3 架构耦合
- 推理逻辑与格式化逻辑耦合在同一服务
- 调整标点/格式化需要重启整个服务
- 无法单独扩展

### 1.4 客户端控制缺失
- 无法从客户端启停/重启服务
- 无法查询服务状态
- 无法动态重载配置

---

## 二、目标架构 (V4)

```
┌─────────────────────────────────────────────────────────────┐
│                      Client (JS)                            │
│              WebSocket + HTTP Health Check                 │
└─────────────────────┬─────────────────────────────────────┘
                      │
┌─────────────────────▼─────────────────────────────────────┐
│                    API Server (NEW)                        │
│                                                          │
│  - WebSocket 连接管理                                      │
│  - 文字格式化、标点、组装                                   │
│  - HTTP REST API                                          │
│  - 热更新：标点/格式化逻辑无需重启模型                       │
│  - 端口: 8080                                             │
│                                                          │
│  控制端点:                                                 │
│    GET  /health      → 服务健康状态                        │
│    GET  /status       → 运行状态                           │
│    POST /control/stop     停止转写                        │
│    POST /control/start     开始转写                        │
│    POST /control/reload    重载热词/配置                   │
│                                                          │
└─────────────────────┬─────────────────────────────────────┘
                      │ 内部通信 (TCP/Queue)
┌─────────────────────▼─────────────────────────────────────┐
│                  Model Server (V3现有)                     │
│                                                          │
│  - 模型加载/卸载 (长期运行)                                 │
│  - ASR/VAD 推理                                           │
│  - 端口: 9876 (内部)                                      │
│                                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、架构优势

| 能力 | V3 (当前) | V4 (目标) |
|------|-----------|-----------|
| 模型热更新 | ❌ 需要重启 30s | ✅ 仅重启 Model Server |
| 格式化热更新 | ❌ 需要重启 | ✅ 仅重启 API Server (秒级) |
| 服务状态感知 | ❌ 无 | ✅ HTTP Health Check |
| 客户端控制 | ❌ 无 | ✅ REST API |
| 断线重连 | ❌ 手动 | ✅ 自动感知 |

---

## 四、服务感知方案

### 4.1 HTTP Health Check

```bash
# 查询服务健康状态
GET /health

# 响应
{
  "status": "ready",           # "ready" | "loading" | "error"
  "models_loaded": true,
  "uptime_seconds": 120,
  "asr_model": "SenseVoiceSmall",
  "punc_model": "ct-punc"
}
```

### 4.2 服务状态 API

```bash
# 查询详细状态
GET /status

# 响应
{
  "transcribing": true,
  "clients_connected": 2,
  "queue_asr_size": 3,
  "queue_punc_size": 12,
  "vad_config": {
    "mode": "sensevoice",
    "silence_timeout": 4.0
  }
}
```

---

## 五、客户端控制方案

### 5.1 REST 控制端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/control/stop` | POST | 停止转写 |
| `/control/start` | POST | 开始转写 |
| `/control/reload` | POST | 重载热词/配置 |
| `/control/reset-vad` | POST | 重置 VAD 状态 |

### 5.2 WebSocket 控制消息

```javascript
// 客户端发送
ws.send(JSON.stringify({
  cmd: 'control',
  action: 'reload_hotwords'
}))

// 服务端响应
{
  cmd: 'control',
  result: 'ok',
  message: 'Hotwords reloaded (2736 words)'
}
```

---

## 六、实施计划

### Phase 1: 服务拆分 (基础)
- [ ] Model Server 保持 v3 核心逻辑
- [ ] 添加 `/health` HTTP 端点
- [ ] 内部通信协议设计

### Phase 2: API Server (新服务)
- [ ] 新建 api_server.py
- [ ] WebSocket 客户端连接管理
- [ ] 标点/格式化逻辑迁移
- [ ] REST API 控制端点

### Phase 3: 客户端增强
- [ ] 添加 HTTP Health Check
- [ ] 断线自动重连
- [ ] 状态显示 (加载中/就绪/错误)

### Phase 4: 高级特性
- [ ] 多客户端支持
- [ ] 队列状态监控
- [ ] 配置热更新

---

## 七、文件结构 (目标)

```
voice-transcribe/
├── model_server.py      # 模型推理服务 (原 transcribe_server_v3)
├── api_server.py        # API 服务 (新建)
├── config.py            # 共享配置
├── interfaces/          # 接口定义
├── processors/          # 处理器
│   ├── audio_source.py
│   ├── vad_processor.py
│   ├── asr_processor.py
│   └── punc_processor.py
├── services/            # 业务逻辑
│   ├── transcription.py # 转录核心逻辑
│   └── formatter.py     # 格式化逻辑 (可热更新)
├── clients/
│   └── web_client.html  # WebSocket 测试页面
└── docs/
    └── ARCHITECTURE_V4.md
```

---

## 八、向后兼容

- V4 初期保留 V3 模式 (`run.py` 选择版本)
- 客户端可渐进升级
- V3 模式最终废弃 (标记为 deprecated)
