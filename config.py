"""
config.py - 配置加载
从 config/settings.json 加载，解析模型路径引用
"""
import os
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config", "settings.json")


def load_config(config_path: str = None) -> dict:
    """加载配置，解析模型路径引用。

    settings.json 中 models 段定义所有模型的绝对路径，
    asr/vad/punc 的 model 字段引用 models 中的 key。
    加载后自动解析为 model_path 字段。
    """
    path = config_path or CONFIG_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    models = config.get("models", {})
    for section in ["asr", "vad", "punc"]:
        if section in config:
            model_key = config[section].get("model")
            if model_key and model_key in models:
                config[section]["model_path"] = models[model_key]
            elif model_key:
                raise ValueError(
                    f"Model key '{model_key}' in [{section}] not found in models section. "
                    f"Available: {list(models.keys())}"
                )

    return config


def save_config(config: dict, config_path: str = None) -> None:
    """保存配置到 settings.json"""
    path = config_path or CONFIG_FILE
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# 直接加载默认配置
DEFAULT_CONFIG = load_config()
