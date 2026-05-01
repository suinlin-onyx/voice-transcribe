"""
processors/hotword_manager.py - 热词管理层
从文档中提取热词，支持动态更新
"""
import os
import re
from typing import List, Set, Optional
from pathlib import Path
import threading

from logger import get_logger

logger = get_logger(__name__, "HOTWORD")


class HotwordManager:
    """热词管理器"""

    def __init__(self):
        self._hotwords: Set[str] = set()
        self._blacklist: Set[str] = {"相关", "等", "等等", "其他", "一些", "某些"}
        self._lock = threading.Lock()

    def load_from_file(self, file_path: str) -> int:
        """从文件加载热词 (每行一个)"""
        count = 0
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    word = line.strip()
                    if word and len(word) >= 2:
                        self.add_hotword(word)
                        count += 1
        except Exception as e:
            logger.error(f"Failed to load hotwords from {file_path}: {e}")
        return count

    def load_from_documents(self, doc_paths: List[str]) -> int:
        """从 Obsidian 文档目录批量提取热词"""
        total_count = 0

        for doc_path in doc_paths:
            path = Path(doc_path)
            if not path.exists():
                continue

            # 匹配模式
            patterns = [
                r'\[\[([^\]|]+)\]\]',           # [[术语]]
                r'\[\[([^|\]]+)\|([^\]]+)\]\]', # [[显示名|链接]]
                r'【([^】]+)】',                 # 【术语】
                r'\*\*([^*]+)\*\*',             # **术语**
                r'`([^`]+)`',                   # `代码/术语`
            ]

            for md_file in path.rglob("*.md"):
                try:
                    with open(md_file, 'r', encoding='utf-8') as f:
                        content = f.read()

                    for pattern in patterns:
                        matches = re.findall(pattern, content)
                        for match in matches:
                            # match 可能是 tuple
                            word = match if isinstance(match, str) else match[0]
                            word = word.strip()
                            if word and 2 <= len(word) <= 20:
                                self.add_hotword(word)
                                total_count += 1

                except Exception as e:
                    pass  # 跳过读取失败的文件

        return total_count

    def load_from_investment_notes(self, notes_path: str) -> int:
        """从投资笔记中提取专业术语"""
        total_count = 0

        # 投资领域专业术语
        investment_terms = [
            # 基本面分析
            "基本面", "估值", "市盈率", "市净率", "股息率", "ROE", "净利润",
            "营收", "毛利率", "净利率", "负债率", "现金流", "EPS", "PE", "PB",
            # 技术面
            "K线", "均线", "MACD", "KDJ", "布林带", "成交量", "换手率",
            # 板块
            "半导体", "新能源", "白酒", "消费", "医疗", "金融", "地产",
            "银行", "券商", "保险", "光伏", "锂电", "芯片", "AI",
            # 操作
            "建仓", "加仓", "减仓", "清仓", "止损", "补仓", "止盈", "追涨",
            # 机构
            "社保", "公募", "私募", "北向", "外资", "游资", "庄家",
            # 分析框架
            "困境反转", "周期轮动", "护城河", "安全边际", "左侧交易", "右侧交易",
            "价值投资", "成长股", "蓝筹", "白马", "龙头",
            # 宏观
            "政策", "货币", "加息", "降息", "通胀", "美联储", "央行",
            # 特定公司/产品
            "茅台", "宁德", "比亚迪", "腾讯", "阿里", "美团",
        ]

        for term in investment_terms:
            self.add_hotword(term)
            total_count += 1

        # 从文档提取
        count = self.load_from_documents([notes_path])
        return total_count + count

    def add_hotword(self, word: str) -> bool:
        """添加热词"""
        word = word.strip()

        # 过滤黑名单和无效词
        if not word or len(word) < 2 or word in self._blacklist:
            return False

        # 过滤纯数字/英文太短的
        if len(word) == 2 and not re.search(r'[一-鿿]', word):
            # 纯英文至少3个字符
            return False

        with self._lock:
            self._hotwords.add(word)
        return True

    def remove_hotword(self, word: str) -> bool:
        """移除热词"""
        with self._lock:
            if word in self._hotwords:
                self._hotwords.remove(word)
                return True
        return False

    def get_hotwords(self) -> List[str]:
        """获取热词列表"""
        with self._lock:
            return list(self._hotwords)

    def clear(self) -> None:
        """清空热词"""
        with self._lock:
            self._hotwords.clear()

    def set_blacklist(self, words: List[str]) -> None:
        """设置黑名单"""
        self._blacklist.update(words)

    def __len__(self) -> int:
        return len(self._hotwords)

    def __repr__(self) -> str:
        return f"HotwordManager({len(self._hotwords)} words)"

    def load_from_hotwords_dir(self, hotwords_dir: str) -> int:
        """从 hotwords 目录加载热词文件"""
        if not os.path.exists(hotwords_dir):
            return 0

        count = 0
        for filename in os.listdir(hotwords_dir):
            if filename.endswith('.txt'):
                filepath = os.path.join(hotwords_dir, filename)
                n = self.load_from_file(filepath)
                count += n
                logger.info(f"Loaded {n} hotwords from {filename}")

        return count

    def save_to_file(self, filepath: str) -> None:
        """保存热词到文件"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            for word in sorted(self._hotwords):
                f.write(word + '\n')
