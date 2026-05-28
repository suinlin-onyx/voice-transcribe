# 转录重复文本问题分析

> 分析日期: 2026/05/24
> 触发: 转录输出中出现 "去皮皮皮"、"这这这这这中间" 等重复字符

---

## 一、现象

```
实际说话: "去皮，青椒一根..."
转录输出: "去皮皮皮，叮叮椒王一根..."
实际说话: "这中间位置立着放..."
转录输出: "这这这这这中间位置立着放双面..."
```

重复模式:
- 单字重复 3+ 次: `皮皮皮`、`这这这这这`
- 词内部分重复: `中中间`（应为 "中间"）、`大大面`（应为 "大面"）

---

## 二、根因分析

### 2.1 主因：VAD Pre-roll 音频重叠

**文件**: `processors/vad_processor.py` → `SenseVoiceVAD._output_segment()`

```python
# 第 187-191 行
pre_roll_audio = self._history_buffer[-pre_roll_samples:]  # 150ms 历史音频
segment_audio = np.concatenate([pre_roll_audio, self._speech_buffer])
```

**问题**: pre-roll 的历史音频末端 与 speech_buffer 前端 **包含相同的音频数据**。
- 每个 segment 输出后，history_buffer 末端的音频**没有被消费掉**
- 下一个 segment 的 pre-roll 包含了上一个 segment 的词尾音频
- ASR 在两个 segment 中都识别了这部分音频 → 文本重复

**图示**:
```
音频流:  [静音] [词A] [词B] [词C] [静音] [词D]
          └──── Segment 1 ────┘
                  └── pre_roll ──┘└── speech ──┘
                  
          └── history 保留了词C末尾 ──┘
                                        └─── Segment 2 ───┘
                                          └─pre-roll─┘└─speech─┘
                                          (包含了词C末尾)

结果: ASR 两次识别词C末尾 → "词C词C"
```

### 2.2 ASR 模型"口吃"幻觉

**文件**: `processors/asr_processor.py` → `SenseVoiceASR`

SenseVoice 作为端到端 ASR 模型，在处理以下情况时会产生重复预测:
- **短音频块** (0.3s chunk): 上下文不足，模型将噪声误判为语音，重复最后一个 token
- **边界碎片**: VAD 切分产生的碎片式音频，缺乏完整语义上下文
- **低质量音频**: 信噪比低时，模型解码循环中反复预测同一 token

`filter_noise()` 方法目前只过滤短词、纯数字、纯标点，**不检测音节重复**。

### 2.3 缺少跨 Segment 文本去重

**文件**: `transcribe_server_ws.py` → `_punc_worker()`

```python
# 第 727 行
pending_text += text  # 简单拼接，无去重
```

管道中 VAD → ASR → PUNC → Output **没有任何环节检测连续 segment 之间的文本重叠**。

### 2.4 次要因素

- **max_speech_duration 过长** (v2 中配置为 30s): 长 segment 增加 ASR 进入重复循环的概率
- **VAD chunk 边界碎片化**: 0.3s min_speech_duration 可能切在词中间，造成碎片识别

---

## 三、解决方案

### 方案 1: ASR 层音节重复折叠 ✅ 优先

**位置**: `processors/asr_processor.py` → `SenseVoiceASR.filter_noise()`

**逻辑**:
```
检测连续重复 ≥3 次的中文字符 → 折叠为最多 2 个
白名单保留拟声词: 哈、呵、嘿、咚、啪等
```

**风险**: 极低。中文中不存在合法的 3+ 次连续重复。

### 方案 2: 文本跨 Segment 去重 ✅ 优先

**位置**: 新建 `processors/text_dedup.py`

**逻辑**:
```
取上一个 segment 文本的最后 20 个字符作为尾部窗口
取新 segment 文本的前 20 个字符作为头部窗口
找尾部后缀 与 头部前缀 的最长公共子串
如果重叠 ≥2 个字符 → 从新 segment 中切掉重叠部分
```

**风险**: 低。只在文本层面处理重叠，不改动音频处理。

### 方案 3: 减小 Pre/Post Roll 时间 ⚠️ 备选

**位置**: `processors/vad_processor.py` → `VADConfig`

将 `pre_roll_duration: 0.15` → `0.05`, `post_roll_duration: 0.2` → `0.1`。

**风险**: 中。可能重新引入 ASR 截断问题。仅当前两个方案不够时启用。

---

## 四、实施顺序

| # | 方案 | 改动文件 | 风险 | 状态 |
|---|------|----------|------|------|
| 1 | ASR 音节重复折叠 | `processors/asr_processor.py` | 极低 | 🔲 待实现 |
| 2 | 文本跨 segment 去重 | `processors/text_dedup.py` (新) + `transcribe_server_ws.py` | 低 | 🔲 待实现 |
| 3 | 减小 pre/post roll | `processors/vad_processor.py` | 中 | 🔲 备选 |
