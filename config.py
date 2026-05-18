"""
config.py - 配置文件
配置从 config/settings.json 加载
"""
import os
import json

# 项目根目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config", "settings.json")

# 模型缓存目录
MODEL_CACHE_DIR = os.path.join(SCRIPT_DIR, "models")

# Obsidian vault 路径
OBSIDIAN_VAULT_PATH = os.path.join(
    os.path.dirname(SCRIPT_DIR),
    "arvin-notes"
)


def load_config(config_path: str = None) -> dict:
    """从 settings.json 加载配置"""
    path = config_path or CONFIG_FILE
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    raise FileNotFoundError(f"Config file not found: {path}")


def save_config(config: dict, config_path: str = None) -> None:
    """保存配置到 settings.json"""
    path = config_path or CONFIG_FILE
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
