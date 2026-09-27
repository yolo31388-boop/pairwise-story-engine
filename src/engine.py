"""剧情引擎 - 带8个bug"""
from dataclasses import dataclass, field

@dataclass
class StoryNode:
    nid: str
    text: str
    choices: list = field(default_factory=list)
    condition: str = None
    is_end: bool = False

class StoryEngine:
    def __init__(self):
        self.nodes: dict[str, StoryNode] = {}
        self.variables = {}  # bug1: 全局共享
        self.current = None
        self.visited = set()
        self.choice_score = 0  # bug3: 只看最后选择

    def evaluate_condition(self, condition):
        # bug6: 精确等于
        return eval(condition, {}, self.variables)

    def transition(self, node_id):
        node = self.nodes[node_id]
        # bug2: 已结束节点还评估
        if node.is_end:
            return None
        self.current = node_id
        self.visited.add(node_id)
        return node

    def make_choice(self, choice_idx):
        node = self.nodes[self.current]
        choice = node.choices[choice_idx]
        # bug3: 只记录最后一次
        self.choice_score = choice.get("score", 0)
        if "next" in choice:
            return self.transition(choice["next"])
        return None

    def determine_ending(self):
        # bug3: 只看最后选择
        if self.choice_score > 0:
            return "good"
        return "bad"

    def rollback(self, node_id):
        # bug4: 不恢复变量
        self.current = node_id
